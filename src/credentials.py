"""
credentials.py — broker credentials entered at startup, never stored.

MT5 authenticates with the real password, so it cannot be kept hashed: a hash
is one-way and mt5.login() would have nothing to send. The protection here is
that the password never reaches disk — it is typed into the terminal with echo
off, held in memory for the life of the process, and never logged.

What *is* hashed is the fingerprint: a salted SHA-256 of the password, printed
as 12 hex characters. It lets the operator confirm they typed the same password
as the previous run without the password itself appearing on screen or in the
log, and it is short and salted with login+server, so it is not a crackable
hash of the secret.

Resolution order used by the bot:
    1. MT5_LOGIN / MT5_PASSWORD / MT5_SERVER environment variables (all three)
       — for unattended/service runs where no terminal exists.
    2. Interactive prompt, when stdin is a terminal.
    3. Caller's fallback (Firebase, then config.py) — a warning path only.
"""

import getpass
import hashlib
import os
import sys

MT5Credentials = tuple[int, str, str, str]  # (login, password, server, source)


def password_fingerprint(login: int, server: str, password: str) -> str:
    """Short salted digest of a password — safe to log, not reversible."""
    salt = f"{login}|{server.strip().lower()}|".encode("utf-8")
    return hashlib.sha256(salt + password.encode("utf-8")).hexdigest()[:12]


def env_credentials() -> MT5Credentials | None:
    """Credentials from the environment, or None unless all three are set."""
    raw_login = os.getenv("MT5_LOGIN", "").strip()
    password  = os.getenv("MT5_PASSWORD", "")
    server    = os.getenv("MT5_SERVER", "").strip()

    if not (raw_login and password and server):
        return None

    try:
        login = int(raw_login)
    except ValueError:
        return None
    if login <= 0:
        return None

    return login, password, server, "environment"


def can_prompt() -> bool:
    """True when there is a real terminal to read a hidden password from."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def prompt_credentials(
    default_login: int = 0,
    default_server: str = "",
    max_attempts: int = 3,
) -> MT5Credentials:
    """
    Ask for broker login, server and password on the terminal.

    Login and server are echoed and may be defaulted with Enter; the password
    is read with echo off and is never defaulted. Raises RuntimeError if the
    user cannot supply usable values within max_attempts.
    """
    print()
    print("=" * 60)
    print(" Broker credentials — not stored, required each run")
    print("=" * 60)

    login = _prompt_login(default_login, max_attempts)
    server = _prompt_server(default_server, max_attempts)
    password = _prompt_password(max_attempts)

    print(f" Password fingerprint: {password_fingerprint(login, server, password)}")
    print("=" * 60)
    print()

    return login, password, server, "terminal prompt"


def _prompt_login(default_login: int, max_attempts: int) -> int:
    suffix = f" [{default_login}]" if default_login > 0 else ""
    for _ in range(max_attempts):
        raw = input(f" MT5 login{suffix}: ").strip()
        if not raw and default_login > 0:
            return default_login
        try:
            login = int(raw)
        except ValueError:
            print(" ! Login must be a number.")
            continue
        if login <= 0:
            print(" ! Login must be greater than zero.")
            continue
        return login
    raise RuntimeError("No valid MT5 login entered")


def _prompt_server(default_server: str, max_attempts: int) -> str:
    suffix = f" [{default_server}]" if default_server else ""
    for _ in range(max_attempts):
        raw = input(f" MT5 server{suffix}: ").strip()
        if not raw:
            raw = default_server
        if raw:
            return raw
        print(" ! Server cannot be empty.")
    raise RuntimeError("No MT5 server entered")


def _prompt_password(max_attempts: int) -> str:
    for _ in range(max_attempts):
        password = getpass.getpass(" MT5 password (hidden): ")
        if password:
            return password
        print(" ! Password cannot be empty.")
    raise RuntimeError("No MT5 password entered")
