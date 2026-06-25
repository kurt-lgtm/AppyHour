"""Upload backup artifacts to Google Drive via the existing drive.file OAuth token — gws-INDEPENDENT.

The scheduled offsite backup (backup_offsite.py) uploads via `gws drive +upload`, which fails when the
`gws` CLI isn't installed/on PATH (the 2026-06-24 breakage → offsite went local-only). This uploader uses
the SAME Drive OAuth token upload_sheet.py already uses (scope drive.file — sufficient to CREATE new files
the app owns), so it needs no gws. Resumable upload handles the 100MB+ DB/knowledge bundles.

Usage:
    python scripts/drive_backup_upload.py <file> [<file> ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TOKEN = Path(r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\drive_oauth_token.json")


def _drive():
    td = json.loads(TOKEN.read_text())
    creds = Credentials(token=td["token"], refresh_token=td["refresh_token"], token_uri=td["token_uri"],
                        client_id=td["client_id"], client_secret=td["client_secret"], scopes=td["scopes"])
    if creds.expired or not creds.valid:
        creds.refresh(Request())
        td["token"] = creds.token
        TOKEN.write_text(json.dumps(td, indent=2))
    return build("drive", "v3", credentials=creds)


def upload(svc, path: Path):
    media = MediaFileUpload(str(path), resumable=True)
    res = svc.files().create(body={"name": path.name}, media_body=media,
                             fields="id,name,size,webViewLink", supportsAllDrives=True).execute()
    mb = int(res.get("size", 0)) / 1048576
    print(f"  OFFSITE: {path.name} ({mb:.1f}MB) id={res['id']} -> {res.get('webViewLink')}")
    return res


def main(argv):
    paths = [Path(a) for a in argv if not a.startswith("--")]
    if not paths:
        print("no files given")
        return 1
    svc = _drive()
    for p in paths:
        if not p.exists():
            print(f"  SKIP (missing): {p}")
            continue
        upload(svc, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
