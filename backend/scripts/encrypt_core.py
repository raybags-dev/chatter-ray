#!/usr/bin/env python3
"""Encrypt all backend/app/core/*.py files (except __init__.py).

Run after CI/CD jobs complete. Files are replaced in-place with ciphertext.
Set ENCRYPTION_KEY env var to the secret key.

Usage: ENCRYPTION_KEY=... python backend/scripts/encrypt_core.py
"""
import base64
import hashlib
import os
import sys
from pathlib import Path

MAGIC = b"# RAYBAGS_ENCRYPTED\n"


def get_fernet(key_str: str):
    from cryptography.fernet import Fernet
    key_bytes = hashlib.sha256(key_str.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_file(path: Path, fernet) -> bool:
    content = path.read_bytes()
    if content.startswith(MAGIC):
        return False  # already encrypted
    encrypted = fernet.encrypt(content)
    path.write_bytes(MAGIC + base64.b64encode(encrypted) + b"\n")
    return True


def main() -> None:
    key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not key:
        print("ERROR: ENCRYPTION_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    fernet = get_fernet(key)
    core_dir = Path(__file__).parent.parent / "app" / "core"
    encrypted = []
    for py in sorted(core_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        if encrypt_file(py, fernet):
            encrypted.append(py.name)
            print(f"  encrypted: {py.name}")
        else:
            print(f"  skipped (already encrypted): {py.name}")

    print(f"\nDone — {len(encrypted)} file(s) encrypted.")


if __name__ == "__main__":
    main()
