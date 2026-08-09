r"""
clear_pending_signals.py — one-time cleanup of users/<USER_ID>/pending_signals.

Clears stale test signals so a real HITL run starts from an empty slate.

Usage (from the project root, with the venv python):
    & ".\.venv\Scripts\python.exe" scripts/clear_pending_signals.py --dry-run
    & ".\.venv\Scripts\python.exe" scripts/clear_pending_signals.py
    & ".\.venv\Scripts\python.exe" scripts/clear_pending_signals.py --yes

Safety:
    * --dry-run shows what would go, touches nothing
    * every run writes a JSON backup to logs/backups/ before deleting
    * deletion needs a typed confirmation unless --yes is passed
    * only the pending_signals subtree is touched — never the parent user node

Stop the bot first. If it is mid-cycle it may push a new signal moments after
the wipe, which is harmless but confusing during a test.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import firebase_admin
from firebase_admin import credentials, db

from config import FIREBASE_CREDENTIALS, FIREBASE_DATABASE_URL, USER_ID

PENDING_PATH = f"users/{USER_ID}/pending_signals"
BACKUP_DIR   = os.path.join("logs", "backups")


def connect() -> None:
    if not os.path.exists(FIREBASE_CREDENTIALS):
        sys.exit(f"ERROR: Firebase credentials '{FIREBASE_CREDENTIALS}' not found. "
                 "Run this from the project root.")
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})
    except Exception as e:
        sys.exit(f"ERROR: Could not initialise Firebase — {e}")


def fetch(ref) -> dict:
    try:
        data = ref.get()
    except Exception as e:
        sys.exit(f"ERROR: Could not read {PENDING_PATH} — {e}")

    if data is None:
        return {}
    if not isinstance(data, dict):
        sys.exit(f"ERROR: Expected a dict at {PENDING_PATH}, got {type(data).__name__}. "
                 "Nothing deleted — inspect this node manually.")
    return data


def summarise(signals: dict) -> None:
    print(f"\n{len(signals)} entr{'y' if len(signals) == 1 else 'ies'} under {PENDING_PATH}:\n")
    counts: dict[str, int] = {}

    for key, sig in signals.items():
        if not isinstance(sig, dict):
            counts["(malformed)"] = counts.get("(malformed)", 0) + 1
            print(f"  {key}  <malformed entry: {type(sig).__name__}>")
            continue

        status = str(sig.get("status", "UNKNOWN")).upper()
        counts[status] = counts.get(status, 0) + 1
        print(f"  {key}  {str(sig.get('symbol', '?')):<8} "
              f"{str(sig.get('direction', '?')):<5} {status:<10} "
              f"pushed {sig.get('pushed_at', '?')}")

    print("\n  Totals: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def backup(signals: dict) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path  = os.path.join(BACKUP_DIR, f"pending_signals_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(signals, f, indent=2, default=str)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete all pending_signals for this user.")
    parser.add_argument("--dry-run", action="store_true", help="show what would be deleted, change nothing")
    parser.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    args = parser.parse_args()

    print(f"Database: {FIREBASE_DATABASE_URL}")
    print(f"Path:     {PENDING_PATH}")

    connect()
    ref     = db.reference(PENDING_PATH)
    signals = fetch(ref)

    if not signals:
        print("\nNothing to delete — pending_signals is already empty.")
        return

    summarise(signals)

    if args.dry_run:
        print("\nDry run — nothing was deleted.")
        return

    path = backup(signals)
    print(f"\nBackup written: {path}")

    if not args.yes:
        answer = input(f"\nDelete all {len(signals)} entries? Type 'delete' to confirm: ").strip()
        if answer.lower() != "delete":
            print("Aborted — nothing was deleted.")
            return

    try:
        ref.delete()
    except Exception as e:
        sys.exit(f"ERROR: Delete failed — {e}\nData is intact; backup kept at {path}")

    remaining = fetch(ref)
    if remaining:
        sys.exit(f"WARNING: {len(remaining)} entries still present after delete — check Firebase rules.")

    print(f"\nDeleted {len(signals)} entries. {PENDING_PATH} is now empty.")
    print("Ready for a clean HITL test.")


if __name__ == "__main__":
    main()
