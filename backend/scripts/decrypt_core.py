#!/usr/bin/env python3
"""Decrypt backend/app/core/*.py files encrypted by encrypt_core.py.

Run at container startup or after cloning the repo.
Set ENCRYPTION_KEY env var to the secret key.

Usage: ENCRYPTION_KEY=... python backend/scripts/decrypt_core.py
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


def decrypt_file(path: Path, fernet) -> bool:
    content = path.read_bytes()
    if not content.startswith(MAGIC):
        return False  # not encrypted
    encrypted_b64 = content[len(MAGIC):].strip()
    plaintext = fernet.decrypt(base64.b64decode(encrypted_b64))
    path.write_bytes(plaintext)
    return True


def main() -> None:
    key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not key:
        # No key set — silently skip (plain dev environment)
        return

    fernet = get_fernet(key)
    core_dir = Path(__file__).parent.parent / "app" / "core"
    decrypted = []
    for py in sorted(core_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        try:
            if decrypt_file(py, fernet):
                decrypted.append(py.name)
        except Exception as exc:
            print(f"  WARN: could not decrypt {py.name}: {exc}", file=sys.stderr)

    if decrypted:
        print(f"Decrypted {len(decrypted)} core file(s).")


if __name__ == "__main__":
    main()
