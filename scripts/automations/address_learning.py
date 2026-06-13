"""Learning loop for the wrong-address automation.

Captures the decisions Kurt actually makes on staged orders and mines them into
rules the triager applies on future runs — so the automation stages less over
time and auto-handles more.

FLOW
  - When the automation STAGES a NEEDS_FIX (low confidence / no key), it calls
    `record_pending(order)` — stores pre-state (address + Google guess).
  - On every later run, `capture_resolutions()` re-pulls each pending order by
    GID. If the invalid_address tag is gone (Kurt fixed it), it diffs
    pre-address -> current-address and appends a LEARNED DECISION to the ledger,
    then drops it from pending.
  - `learned_typos()` / `learned_false_positives()` expose mined rules the
    triager consults FIRST (before Google), so a correction Kurt makes once is
    applied automatically next time.
  - `mine_candidates()` proposes NEW rules from the ledger for approval (TIER
    pattern — never auto-promoted), written to a review file.

FILES (all under scripts/automations/_learning/, git-ignored derived data):
  decisions.jsonl        — append-only ledger of every captured before->after
  pending.json           — staged orders awaiting Kurt's decision
  learned_typos.json     — APPROVED token/substring substitutions (applied)
  false_positives.json   — APPROVED untag allowlist (street/customer keys)
  candidates.md          — mined rules awaiting Kurt approval (NOT applied)

ASCII-only. Pure stdlib + the caller's gql(). No writes to live data here.
"""
import json
import os
import re
from datetime import datetime, timezone

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_learning")
_LEDGER = os.path.join(_DIR, "decisions.jsonl")
_PENDING = os.path.join(_DIR, "pending.json")
_TYPOS = os.path.join(_DIR, "learned_typos.json")
_FALSEPOS = os.path.join(_DIR, "false_positives.json")
_CANDIDATES = os.path.join(_DIR, "candidates.md")

_MIN_OCCURRENCES = 2  # a pattern must repeat before it becomes a candidate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _dump(path, obj):
    os.makedirs(_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=True)
    os.replace(tmp, path)


# ── pending watchlist ─────────────────────────────────────────────────
def record_pending(row: dict) -> None:
    """Store a staged order's pre-state so a later run can learn the outcome."""
    pending = _load(_PENDING, {})
    pending[row["gid"]] = {
        "name": row.get("name"),
        "pre_a1": row.get("a1"), "pre_a2": row.get("a2"),
        "city": row.get("city"), "prov": row.get("prov"), "zip": row.get("zip"),
        "google_a1": (row.get("fix") or {}).get("a1"),
        "google_a2": (row.get("fix") or {}).get("a2"),
        "staged_at": _now(),
    }
    _dump(_PENDING, pending)


def capture_resolutions(gql, base, hdr, order_query, invalid_tag: str) -> list[dict]:
    """Re-pull pending orders. If Kurt resolved one (tag gone / address changed),
    append a learned decision and drop it from pending. Returns captured rows."""
    pending = _load(_PENDING, {})
    if not pending:
        return []
    captured = []
    still = {}
    for gid, pre in pending.items():
        try:
            o = gql(base, hdr, order_query, {"id": gid}).get("order")
        except Exception:  # noqa: BLE001
            still[gid] = pre  # transient — keep watching
            continue
        if not o:
            continue  # deleted/cancelled — drop
        tags = o.get("tags") or []
        addr = o.get("shippingAddress") or {}
        cur_a1 = (addr.get("address1") or "").strip()
        cur_a2 = (addr.get("address2") or "").strip()
        resolved = invalid_tag not in tags
        changed = (cur_a1 != (pre.get("pre_a1") or "").strip()
                   or cur_a2 != (pre.get("pre_a2") or "").strip())
        if not resolved and not changed:
            still[gid] = pre  # Kurt hasn't acted yet
            continue
        # Kurt made a call. Classify it.
        google_a1 = (pre.get("google_a1") or "").strip()
        if changed and google_a1 and cur_a1 == google_a1:
            kind = "accepted_google"
        elif changed:
            kind = "manual_rewrite"
        else:
            kind = "untag_only"  # he decided the original was fine
        rec = {
            "ts": _now(), "gid": gid, "name": pre.get("name"), "kind": kind,
            "before_a1": pre.get("pre_a1"), "before_a2": pre.get("pre_a2"),
            "after_a1": cur_a1, "after_a2": cur_a2,
            "google_a1": pre.get("google_a1"), "google_a2": pre.get("google_a2"),
            "city": pre.get("city"), "prov": pre.get("prov"), "zip": pre.get("zip"),
        }
        with open(_ensure_ledger(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=True) + "\n")
        captured.append(rec)
    _dump(_PENDING, still)
    return captured


def _ensure_ledger() -> str:
    os.makedirs(_DIR, exist_ok=True)
    return _LEDGER


# ── approved rules the triager consults ───────────────────────────────
def learned_typos() -> dict:
    """Approved {wrong_token: right_token} substitutions (case-insensitive)."""
    return _load(_TYPOS, {})


def learned_false_positives() -> set:
    """Approved untag-allowlist keys (lowercased 'a1|city|prov')."""
    return set(_load(_FALSEPOS, []))


def fp_key(addr: dict) -> str:
    return f"{(addr.get('address1') or '').lower()}|{(addr.get('city') or '').lower()}|{(addr.get('provinceCode') or '').lower()}"


def apply_learned_typos(a1: str) -> str:
    """Apply approved token substitutions to an address1 string."""
    typos = learned_typos()
    if not typos or not a1:
        return a1
    out = a1
    for wrong, right in typos.items():
        out = re.sub(rf"\b{re.escape(wrong)}\b", right, out, flags=re.I)
    return out


# ── miner: propose new rules for approval (never auto-applied) ─────────
def mine_candidates() -> dict:
    """Scan the ledger; surface repeated typo + false-positive patterns that
    aren't yet approved. Writes candidates.md. Returns counts."""
    if not os.path.exists(_LEDGER):
        return {"typos": 0, "false_positives": 0}
    typo_counts: dict[tuple, int] = {}
    fp_counts: dict[str, int] = {}
    with open(_LEDGER, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r["kind"] == "manual_rewrite":
                for w, right in _token_diffs(r.get("before_a1") or "", r.get("after_a1") or ""):
                    typo_counts[(w, right)] = typo_counts.get((w, right), 0) + 1
            elif r["kind"] == "untag_only":
                k = f"{(r.get('after_a1') or r.get('before_a1') or '').lower()}|{(r.get('city') or '').lower()}|{(r.get('prov') or '').lower()}"
                fp_counts[k] = fp_counts.get(k, 0) + 1

    approved_typos = {k.lower() for k in learned_typos()}
    approved_fp = learned_false_positives()
    cand_typos = {f"{w} -> {r}": n for (w, r), n in typo_counts.items()
                  if n >= _MIN_OCCURRENCES and w.lower() not in approved_typos}
    cand_fp = {k: n for k, n in fp_counts.items()
               if n >= _MIN_OCCURRENCES and k not in approved_fp}

    L = [f"# Address-rule candidates — {datetime.now():%Y-%m-%d}", "",
         "Mined from Kurt's actual fixes. Approve by moving into learned_typos.json / "
         "false_positives.json. NOT auto-applied.", ""]
    L.append("## Typo substitutions (seen >=2x)")
    L += [f"- `{k}` ({n}x)" for k, n in sorted(cand_typos.items(), key=lambda x: -x[1])] or ["_none_"]
    L.append("")
    L.append("## False-positive allowlist (untagged >=2x)")
    L += [f"- `{k}` ({n}x)" for k, n in sorted(cand_fp.items(), key=lambda x: -x[1])] or ["_none_"]
    os.makedirs(_DIR, exist_ok=True)
    with open(_CANDIDATES, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return {"typos": len(cand_typos), "false_positives": len(cand_fp), "path": _CANDIDATES}


def _token_diffs(before: str, after: str):
    """Yield (wrong, right) token pairs where a single token changed in place."""
    b, a = before.split(), after.split()
    if len(b) != len(a):
        return  # structural change (split-merge) — not a token typo
    for wb, wa in zip(b, a):
        if wb != wa and wb.lower() != wa.lower() and len(wb) > 2:
            yield wb, wa
