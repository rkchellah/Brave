"""
trade_logger.py — Append-only CSV log of every Flow signal that reaches ANALYSE.

One row per signal lifecycle write. Exit fields stay blank until
update_trade_outcome() patches the row when the MT5 position closes.
"""

from __future__ import annotations

import csv
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

LOG_DIR  = Path("logs")
LOG_PATH = LOG_DIR / "trade_log.csv"

COLUMNS = [
    "timestamp",
    "symbol",
    "direction",
    "entry",
    "sl",
    "tp",
    "rr",
    "deepseek_verdict",
    "deepseek_reason",
    "risk_check_result",
    "outcome",
    "mt5_ticket",
    "exit_price",
    "exit_reason",
    "pnl",
]


def log_signal(row: dict) -> None:
    """
    Append one signal row to logs/trade_log.csv.

    Missing columns are written as empty strings. Extra keys are ignored.
    Never raises — a log failure must not block trading.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        is_new = not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0

        payload = {col: _cell(row.get(col)) for col in COLUMNS}

        with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            if is_new:
                writer.writeheader()
            writer.writerow(payload)
    except OSError as e:
        log.error(f"[trade_logger] Could not write signal log: {e}")


def update_trade_outcome(
    mt5_ticket: int | str,
    exit_price: float | str = "",
    exit_reason: str = "",
    pnl: float | str = "",
) -> bool:
    """
    Patch exit fields on the row matching mt5_ticket.

    Rewrites the CSV atomically. Returns True if a row was updated.
    Call this when a position closes (SL/TP/manual) — exit data is not
    known at signal time.
    """
    ticket = str(mt5_ticket).strip()
    if not ticket or ticket in ("0", "None"):
        return False

    try:
        if not LOG_PATH.exists():
            return False

        with LOG_PATH.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            return False

        updated = False
        for row in rows:
            if str(row.get("mt5_ticket", "")).strip() != ticket:
                continue
            row["exit_price"]  = _cell(exit_price)
            row["exit_reason"] = _cell(exit_reason)
            row["pnl"]         = _cell(pnl)
            updated = True
            break  # First match — tickets are unique per fill

        if not updated:
            return False

        _rewrite(rows)
        return True
    except OSError as e:
        log.error(f"[trade_logger] Could not update ticket {ticket}: {e}")
        return False


def attach_ticket(symbol: str, mt5_ticket: int | str, outcome: str = "EXECUTED") -> bool:
    """
    Attach a ticket to the latest HITL row for symbol (manual confirm path).

    Graph writes outcome=HITL with a blank ticket; bot.py calls this after
    the user confirms and place_order succeeds.
    """
    ticket = str(mt5_ticket).strip()
    if not ticket or ticket in ("0", "None"):
        return False

    return _patch_latest_hitl(symbol, mt5_ticket=ticket, outcome=outcome)


def mark_hitl_outcome(symbol: str, outcome: str) -> bool:
    """Update the latest HITL row for symbol when confirm/reject fails or expires."""
    return _patch_latest_hitl(symbol, outcome=outcome)


def _patch_latest_hitl(symbol: str, mt5_ticket: str | None = None, outcome: str | None = None) -> bool:
    try:
        if not LOG_PATH.exists():
            return False

        with LOG_PATH.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        for row in reversed(rows):
            if str(row.get("symbol", "")).upper() != str(symbol).upper():
                continue
            if str(row.get("outcome", "")).upper() != "HITL":
                continue
            if str(row.get("mt5_ticket", "")).strip():
                continue
            if mt5_ticket is not None:
                row["mt5_ticket"] = mt5_ticket
            if outcome is not None:
                row["outcome"] = outcome
            _rewrite(rows)
            return True

        return False
    except OSError as e:
        log.error(f"[trade_logger] Could not patch HITL row for {symbol}: {e}")
        return False


def _rewrite(rows: list[dict]) -> None:
    """Atomic rewrite so a crash mid-write doesn't truncate the log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="trade_log_", suffix=".csv", dir=str(LOG_DIR))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({col: _cell(row.get(col)) for col in COLUMNS})
        Path(tmp_name).replace(LOG_PATH)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value)
