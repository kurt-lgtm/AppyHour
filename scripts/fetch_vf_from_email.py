"""Fetch every vF production sheet we EMAILED to RMFG, straight from Gmail via IMAP.

The sent vF is the fulfillment authority (VF_SHEET_RULES §7) but local copies rot:
the wk0727 sheet is already gone from disk. The mailbox is the durable copy —
this pulls the raw attachments back so `vf_archive.py` can ingest them.

Never overwrites an existing file (never-delete-prior-output-files): a same-named
attachment with different bytes is saved with a `__dupN` suffix and reported.

Usage:
    python fetch_vf_from_email.py [--days 400] [--dest <dir>] [--query <gmail-raw>]
"""

from __future__ import annotations

import argparse
import email
import hashlib
import imaplib
import re
import sys
from email.header import decode_header, make_header
from pathlib import Path

ENV = Path(r"C:/Users/Work/Claude Projects/AppyHour/.env")
DEFAULT_DEST = Path(r"C:/AppyHourData/vf_archive/raw")
# Gmail raw search. Sheets reach RMFG from us and come back on replies, so search
# All Mail rather than a single folder; filename: matches the attachment itself.
DEFAULT_QUERY = "filename:AHB_WeeklyProductionQuery has:attachment"


# Subjects carry emoji; Windows' cp1252 stdout raises mid-run and kills the fetch.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def decode_filename(raw: str) -> str:
    """RFC2047-encoded attachment names are common here ([[imap-attachment-filename-rfc2047]])."""
    try:
        name = str(make_header(decode_header(raw)))
    except Exception:
        name = raw
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def save(dest_dir: Path, name: str, payload: bytes) -> tuple[Path | None, str]:
    """Write payload, never clobbering. Returns (path_written_or_None, status)."""
    digest = hashlib.sha256(payload).hexdigest()
    target = dest_dir / name
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            return None, "identical"
        stem, suffix = target.stem, target.suffix
        n = 2
        while (alt := dest_dir / f"{stem}__dup{n}{suffix}").exists():
            if hashlib.sha256(alt.read_bytes()).hexdigest() == digest:
                return None, "identical"
            n += 1
        alt.write_bytes(payload)
        return alt, "variant"
    target.write_bytes(payload)
    return target, "new"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400, help="Gmail newer_than window (default 400)")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--query", default=DEFAULT_QUERY, help="extra Gmail raw query terms")
    args = ap.parse_args()

    env = load_env(ENV)
    args.dest.mkdir(parents=True, exist_ok=True)

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(env["IMAP_SMTP_USER"], env["IMAP_SMTP_APP_PASSWORD"])
    imap.select('"[Gmail]/All Mail"', readonly=True)

    q = f'"{args.query} newer_than:{args.days}d"'
    status, data = imap.search(None, "X-GM-RAW", q)
    if status != "OK":
        print(f"search failed: {status}", file=sys.stderr)
        return 1
    ids = data[0].split()
    print(f"matched {len(ids)} messages ({args.days}d) for: {args.query}")

    counts = {"new": 0, "variant": 0, "identical": 0}
    for mid in ids:
        _, msg_data = imap.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        subj = str(msg.get("Subject", ""))[:60]
        date = str(msg.get("Date", ""))
        for part in msg.walk():
            raw_fn = part.get_filename()
            if not raw_fn:
                continue
            name = decode_filename(raw_fn)
            if not name.lower().endswith((".xlsx", ".xlsm")):
                continue
            if "weeklyproductionquery" not in name.lower():
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            path, st = save(args.dest, name, payload)
            counts[st] += 1
            if st != "identical":
                print(f"  {st:8s} {path.name}  ({len(payload):,}B)  [{date[:16]} | {subj}]")

    imap.logout()
    print(f"\nnew={counts['new']} variant={counts['variant']} identical={counts['identical']} -> {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
