"""Shopify + Google service-account credential resolver.

Env vars win; InventoryReorder settings are the fallback. Usable by lean
MCP servers (shipping) that don't want to import the tkinter app just to
load a JSON file.

Env vars:
  SHOPIFY_STORE_URL       e.g. "elevatefoods" (subdomain only, no .myshopify.com)
  SHOPIFY_ACCESS_TOKEN    Admin API access token
  SHOPIFY_API_VERSION     optional; defaults to "2024-10"

  GOOGLE_SVC_ACCOUNT_JSON_CONTENT   inline service-account JSON (App Platform)
  GOOGLE_SVC_ACCOUNT_JSON           path to the service-account JSON file
  GOOGLE_CREDENTIALS_JSON           path (legacy .env key; relative to AppyHour/)

Google service account — why this exists (negatives first):
  * The SA JSON path was hardcoded at ~30 call sites with NO env-var escape.
    App Platform has no such file, so every sheet-writing job was structurally
    unable to run in the cloud — not "misconfigured", *impossible*. One reader
    here means consumer N+1 inherits the env path instead of copying a literal.
  * NEVER print, log, or interpolate the credential value. json.JSONDecodeError
    carries the offending document in its args, so _sa_info_from_env() re-raises
    a scrubbed error — a traceback that leaks the private key into a scheduled
    job's log file is a credential disclosure, not a debugging aid.
  * A missing/invalid credential RAISES. It never degrades to an unauthenticated
    client: a sheet writer that silently no-ops is indistinguishable from one
    with nothing to write, and that class has burned this operation repeatedly.
  * Layer rule: appyhour_lib is stdlib-only at IMPORT time. google.oauth2 is
    imported lazily inside get_google_credentials(), never at module scope.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from google.oauth2.service_account import Credentials

DEFAULT_API_VERSION = "2026-04"

# Env var names — match the existing convention (cut_order_server/app/creds.py,
# cut_order_server/DEPLOY.md, cut_order_server/deploy/provision.sh). Do NOT coin
# a new prefix; GOOGLE_CREDENTIALS_JSON is already taken in AppyHour/.env for a
# PATH, so inline JSON needs the distinct _CONTENT name.
GOOGLE_SA_JSON_ENV = "GOOGLE_SVC_ACCOUNT_JSON_CONTENT"
GOOGLE_SA_PATH_ENVS = ("GOOGLE_SVC_ACCOUNT_JSON", "GOOGLE_CREDENTIALS_JSON")

# AppyHour repo root (appyhour_lib/ -> AppyHour/).
_APPYHOUR_ROOT = Path(__file__).resolve().parent.parent

# The historical hardcoded key. Stays the LAST fallback so local runs with no
# env var set behave exactly as they did before. Misspelling is real — the file
# is genuinely named "perfomance"; do not "fix" it.
GOOGLE_SA_FALLBACK_FILE = _APPYHOUR_ROOT / "shipping-perfomance-review-accd39ac4b78.json"

DEFAULT_GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)


def _read_settings_fallback() -> dict:
    """Read InventoryReorder settings JSON directly (no module import).

    Keeps this resolver lean for the shipping MCP.
    """
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "AppyHour" / "inventory_reorder_settings.json",
        Path(__file__).resolve().parent.parent / "InventoryReorder" / "inventory_reorder_settings.json",
        Path(__file__).resolve().parent.parent / "InventoryReorder" / "dist" / "inventory_reorder_settings.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                with p.open(encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def get_shopify_credentials() -> tuple[str, str]:
    """Return (store_subdomain, access_token).

    Env vars first, InventoryReorder settings as fallback. Raises RuntimeError
    if neither source has both pieces.
    """
    store = os.environ.get("SHOPIFY_STORE_URL", "").strip()
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN", "").strip()

    if not (store and token):
        settings = _read_settings_fallback()
        store = store or str(settings.get("shopify_store_url", "")).strip()
        token = token or str(settings.get("shopify_access_token", "")).strip()

    if not store or not token:
        raise RuntimeError(
            "Shopify credentials not found. Set SHOPIFY_STORE_URL + "
            "SHOPIFY_ACCESS_TOKEN env vars, or configure InventoryReorder settings."
        )
    return store, token


def get_shopify_auth() -> tuple[str, dict[str, str]]:
    """Return (base_url, headers) tuple for requests calls."""
    store, token = get_shopify_credentials()
    version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION
    base = f"https://{store}.myshopify.com/admin/api/{version}"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    return base, headers


def get_openweather_key() -> str | None:
    """Return OWM key from env (OPENWEATHER_API_KEY) or None."""
    key = os.environ.get("OPENWEATHER_API_KEY", "").strip()
    return key or None


# ── Google service account ──────────────────────────────────────────────────


def _sa_info_from_env() -> dict[str, Any] | None:
    """Parse GOOGLE_SVC_ACCOUNT_JSON_CONTENT, or None if unset/blank.

    Raises RuntimeError on malformed JSON. The message deliberately carries only
    the env var name and the byte length — json.JSONDecodeError.args embeds the
    whole document, which for this variable IS the private key.
    """
    raw = os.environ.get(GOOGLE_SA_JSON_ENV, "").strip()
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{GOOGLE_SA_JSON_ENV} is set ({len(raw)} bytes) but is not valid JSON "
            f"(parse failed at line {exc.lineno} col {exc.colno}). "
            f"Paste the service-account JSON file's entire contents, unmodified."
        ) from None  # 'from None' — the original exception carries the key
    if not isinstance(info, dict):
        raise RuntimeError(
            f"{GOOGLE_SA_JSON_ENV} parsed as {type(info).__name__}, expected a JSON object."
        )
    missing = [k for k in ("client_email", "private_key", "token_uri") if not info.get(k)]
    if missing:
        raise RuntimeError(
            f"{GOOGLE_SA_JSON_ENV} is missing required key(s): {', '.join(missing)}. "
            f"Expected a Google service-account key file, not an OAuth client file."
        )
    return info


def _sa_path_from_env() -> Path | None:
    """First existing path named by a GOOGLE_SA_PATH_ENVS var, else None.

    A relative value resolves against CWD first, then the AppyHour root
    (AppyHour/.env ships GOOGLE_CREDENTIALS_JSON as '../<key>.json'). A named
    path that does not exist falls THROUGH to the next source rather than
    raising, so setting it can never break a working local run.
    """
    for name in GOOGLE_SA_PATH_ENVS:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        for candidate in (Path(raw), _APPYHOUR_ROOT / raw):
            if candidate.is_file():
                return candidate.resolve()
    return None


def google_sa_source() -> str:
    """Describe where the credential WOULD come from. Never returns the value.

    Safe to print/log: returns 'env:GOOGLE_SVC_ACCOUNT_JSON_CONTENT',
    'file:<path>', or 'MISSING'.
    """
    if os.environ.get(GOOGLE_SA_JSON_ENV, "").strip():
        return f"env:{GOOGLE_SA_JSON_ENV}"
    path = _sa_path_from_env()
    if path is not None:
        return f"file:{path}"
    if GOOGLE_SA_FALLBACK_FILE.is_file():
        return f"file:{GOOGLE_SA_FALLBACK_FILE}"
    return "MISSING"


def get_google_sa_email() -> str:
    """Service-account email for the resolved credential. Not a secret."""
    info = _sa_info_from_env()
    if info is not None:
        return str(info["client_email"])
    path = get_google_credentials_path()
    with open(path, encoding="utf-8") as fh:
        return str(json.load(fh).get("client_email", ""))


def get_google_credentials_path() -> str:
    """Path to the service-account JSON FILE.

    For consumers that genuinely need a path on disk. Raises RuntimeError when
    only the inline env var is available — callers on App Platform must use
    get_google_credentials() instead of asking for a file that isn't there.
    We deliberately do NOT spill the inline credential to a temp file.
    """
    path = _sa_path_from_env()
    if path is not None:
        return str(path)
    if GOOGLE_SA_FALLBACK_FILE.is_file():
        return str(GOOGLE_SA_FALLBACK_FILE)
    if os.environ.get(GOOGLE_SA_JSON_ENV, "").strip():
        raise RuntimeError(
            f"Only {GOOGLE_SA_JSON_ENV} (inline JSON) is available — there is no "
            f"credentials FILE on this host. Use "
            f"appyhour_lib.credentials.get_google_credentials() instead of a path."
        )
    raise RuntimeError(_missing_message())


def _missing_message() -> str:
    return (
        "Google service-account credentials not found. Set "
        f"{GOOGLE_SA_JSON_ENV} to the full contents of the service-account JSON "
        f"(App Platform / any host with no key file), or {GOOGLE_SA_PATH_ENVS[0]} "
        f"to its path, or place the key at {GOOGLE_SA_FALLBACK_FILE}."
    )


def get_google_credentials(scopes: Sequence[str] | None = None) -> Credentials:
    """Return google.oauth2 service-account Credentials. NEVER returns None.

    Resolution order (first hit wins):
      1. GOOGLE_SVC_ACCOUNT_JSON_CONTENT  — inline JSON  (App Platform / cloud)
      2. GOOGLE_SVC_ACCOUNT_JSON          — file path
      3. GOOGLE_CREDENTIALS_JSON          — file path (legacy .env key)
      4. AppyHour/shipping-perfomance-review-accd39ac4b78.json  (local default)

    With no env var set this is step 4 — byte-for-byte the historical behaviour.
    Raises RuntimeError if nothing resolves; callers must NOT catch-and-continue
    with an unauthenticated client.
    """
    from google.oauth2.service_account import Credentials as _Credentials

    scope_list = list(scopes) if scopes else list(DEFAULT_GOOGLE_SCOPES)

    info = _sa_info_from_env()
    if info is not None:
        return _Credentials.from_service_account_info(info, scopes=scope_list)

    path = _sa_path_from_env()
    if path is None and GOOGLE_SA_FALLBACK_FILE.is_file():
        path = GOOGLE_SA_FALLBACK_FILE
    if path is None:
        raise RuntimeError(_missing_message())
    return _Credentials.from_service_account_file(str(path), scopes=scope_list)
