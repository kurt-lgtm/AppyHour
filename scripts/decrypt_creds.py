"""Decrypt + extract a coldchain-creds-backup-*.zip.enc produced by backup_offsite.py.

Usage:
    python scripts/decrypt_creds.py <blob.zip.enc> <out_dir>
    # passphrase from AH_BACKUP_PASSPHRASE env, or you'll be prompted.

The blob framing is: b"AHENC1\\n" + 16-byte scrypt salt + Fernet token.
Extracted files land in <out_dir> with their original relative names
(creds JSONs flat; cut_order_server/.env nested), ready to copy into
%APPDATA%\\AppyHour\\ and the repo respectively.
"""
from __future__ import annotations

import base64
import getpass
import io
import os
import sys
import zipfile
from pathlib import Path

_ENC_MAGIC = b"AHENC1\n"
_SCRYPT_N = 2**14


def _fernet_for(passphrase: str, salt: bytes):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    key = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=8, p=1).derive(passphrase.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def decrypt(blob: Path, out_dir: Path, passphrase: str) -> list[str]:
    raw = blob.read_bytes()
    if not raw.startswith(_ENC_MAGIC):
        raise ValueError(f"not an AHENC1 blob: {blob}")
    body = raw[len(_ENC_MAGIC):]
    salt, token = body[:16], body[16:]
    from cryptography.fernet import InvalidToken

    try:
        plain = _fernet_for(passphrase, salt).decrypt(token)
    except InvalidToken as exc:
        raise SystemExit("decrypt failed: wrong passphrase or corrupt blob") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(plain)) as zf:
        zf.extractall(out_dir)
        return zf.namelist()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    blob, out_dir = Path(argv[0]), Path(argv[1])
    passphrase = os.environ.get("AH_BACKUP_PASSPHRASE") or getpass.getpass("Backup passphrase: ")
    names = decrypt(blob, out_dir, passphrase)
    print(f"decrypted {len(names)} file(s) -> {out_dir}")
    for n in names:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
