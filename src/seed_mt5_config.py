r"""
seed_mt5_config.py — one-time seed of users/<USER_ID>/mt5_config.

Pushes the current config.py broker credentials into Firebase so the node is
populated on first run and the mobile app has something to edit. The bot reads
this node at startup, falling back to config.py when it is missing or partial.

Usage (from the project root):
    & ".\.venv\Scripts\python.exe" src/seed_mt5_config.py
    & ".\.venv\Scripts\python.exe" src/seed_mt5_config.py --force   # overwrite

Existing values are never overwritten without --force, so a password set from
the app cannot be clobbered by a stray re-run.

Safe to delete this file once the node exists.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import firebase_admin
from firebase_admin import credentials, db

from config import (
    FIREBASE_CREDENTIALS,
    FIREBASE_DATABASE_URL,
    MT5_LOGIN,
    MT5_PASSWORD,
    MT5_SERVER,
    USER_ID,
)

MT5_CONFIG_PATH = f"users/{USER_ID}/mt5_config"


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed mt5_config in Firebase from config.py.")
    parser.add_argument("--force", action="store_true", help="overwrite an existing mt5_config node")
    args = parser.parse_args()

    if not os.path.exists(FIREBASE_CREDENTIALS):
        sys.exit(f"ERROR: Firebase credentials '{FIREBASE_CREDENTIALS}' not found. "
                 "Run this from the project root.")

    if not (MT5_LOGIN and MT5_PASSWORD and MT5_SERVER):
        sys.exit("ERROR: config.py is missing MT5_LOGIN / MT5_PASSWORD / MT5_SERVER — nothing to seed.")

    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})
        ref = db.reference(MT5_CONFIG_PATH)
        existing = ref.get()
    except Exception as e:
        sys.exit(f"ERROR: Could not reach Firebase — {e}")

    print(f"Path: {MT5_CONFIG_PATH}")

    if existing and not args.force:
        # Report shape only — the stored password is never printed
        keys = ", ".join(sorted(existing)) if isinstance(existing, dict) else type(existing).__name__
        print(f"\nmt5_config already exists (fields: {keys}). Nothing written.")
        print("Re-run with --force to overwrite it with the config.py values.")
        return

    payload = {
        "login":      int(MT5_LOGIN),
        "password":   MT5_PASSWORD,
        "server":     MT5_SERVER,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "seed_script",
    }

    try:
        ref.set(payload)
        written = ref.get()
    except Exception as e:
        sys.exit(f"ERROR: Write failed — {e}")

    if not isinstance(written, dict):
        sys.exit("ERROR: Read-back after write did not return an object — check Firebase rules.")

    print("\nSeeded mt5_config:")
    print(f"  login:      {written.get('login')}")
    print(f"  password:   {'*' * 8} ({len(str(written.get('password', '')))} chars stored)")
    print(f"  server:     {written.get('server')}")
    print(f"  updated_at: {written.get('updated_at')}")
    print("\nThe bot will use these on its next start. This file can be deleted now.")


if __name__ == "__main__":
    main()
