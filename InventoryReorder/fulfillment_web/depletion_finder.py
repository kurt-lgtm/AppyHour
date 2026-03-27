"""Find depletion XLSX files from Gmail Sent folder and Downloads directory.

Searches for WeeklyProductionQuery*.xlsx attachments sent by the user
via email, plus any matching files in the local Downloads folder.
"""

from __future__ import annotations

import email
import glob
import imaplib
import os
import tempfile
import time
from datetime import date, timedelta
from email.header import decode_header


def find_depletion_gmail_sent(
    user: str,
    password: str,
    since_days: int = 7,
) -> list[dict]:
    """Search Gmail Sent folder for depletion XLSX attachments.

    Returns list of {filename, path, date_sent, subject, message_id}.
    Downloaded files are saved to a temp directory.
    """
    if not user or not password:
        return []

    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        conn.login(user, password)
    except Exception:
        return []

    results = []
    try:
        # Select Sent folder (Gmail uses "[Gmail]/Sent Mail")
        status, _ = conn.select('"[Gmail]/Sent Mail"', readonly=True)
        if status != "OK":
            # Fallback for localized Gmail
            conn.select('"[Gmail]/All Mail"', readonly=True)

        since_date = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, f'SINCE {since_date}')
        if status != "OK" or not data[0]:
            return []

        msg_ids = data[0].split()
        tmp_dir = os.path.join(tempfile.gettempdir(), "appyhour_depletion")
        os.makedirs(tmp_dir, exist_ok=True)

        for msg_id in msg_ids:
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            message_id = msg.get("Message-ID", msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id))

            # Parse date
            date_str = msg.get("Date", "")
            try:
                from email.utils import parsedate_to_datetime
                msg_date = parsedate_to_datetime(date_str).date().isoformat()
            except Exception:
                msg_date = date.today().isoformat()

            # Parse subject
            subject_raw = msg.get("Subject", "")
            subject_decoded = ""
            for part, enc in decode_header(subject_raw):
                if isinstance(part, bytes):
                    subject_decoded += part.decode(enc or "utf-8", errors="replace")
                else:
                    subject_decoded += part

            # Look for depletion XLSX attachments
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                filename = part.get_filename()
                if not filename:
                    continue
                # Sanitize filename to prevent path traversal
                filename = os.path.basename(filename)
                fn_lower = filename.lower()
                if "weeklyproductionquery" in fn_lower and fn_lower.endswith(".xlsx"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        save_path = os.path.join(tmp_dir, filename)
                        with open(save_path, "wb") as f:
                            f.write(payload)
                        results.append({
                            "filename": filename,
                            "path": save_path,
                            "date_sent": msg_date,
                            "subject": subject_decoded,
                            "message_id": message_id,
                            "source": "gmail_sent",
                        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("IMAP depletion scan error: %s", e)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    return results


def find_depletion_downloads(
    downloads_dir: str | None = None,
    since_days: int = 7,
) -> list[dict]:
    """Scan Downloads folder for depletion XLSX files.

    Returns list of {filename, path, date_modified, source}.
    """
    if downloads_dir is None:
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")

    if not os.path.isdir(downloads_dir):
        return []

    cutoff = time.time() - (since_days * 86400)
    results = []

    for fpath in glob.glob(os.path.join(downloads_dir, "*.xlsx")):
        fn = os.path.basename(fpath)
        if "weeklyproductionquery" not in fn.lower():
            continue
        mtime = os.path.getmtime(fpath)
        if mtime < cutoff:
            continue
        results.append({
            "filename": fn,
            "path": fpath,
            "date_modified": date.fromtimestamp(mtime).isoformat(),
            "source": "downloads",
        })

    return results


def find_all_depletion_files(
    user: str,
    password: str,
    applied_files: list[str] | None = None,
    since_days: int = 7,
) -> list[dict]:
    """Find depletion files from all sources, deduplicated and filtered.

    Args:
        user: Gmail IMAP username.
        password: Gmail app password.
        applied_files: List of filenames already applied (skip these).
        since_days: How far back to search.

    Returns:
        List of depletion file dicts, sorted newest first.
    """
    if applied_files is None:
        applied_files = []
    applied_set = set(applied_files)

    gmail_files = find_depletion_gmail_sent(user, password, since_days)
    download_files = find_depletion_downloads(since_days=since_days)

    # Deduplicate by filename (prefer gmail_sent since it's the primary source)
    seen = set()
    combined = []
    for f in gmail_files + download_files:
        fn = f["filename"]
        if fn in seen or fn in applied_set:
            continue
        seen.add(fn)
        combined.append(f)

    # Sort by date (newest first)
    def sort_key(f):
        return f.get("date_sent") or f.get("date_modified") or ""

    combined.sort(key=sort_key, reverse=True)
    return combined
