"""
trade_logger.py — Append-only CSV logs. Every CSV Brave writes lives here.

log_signal()         → logs/trade_log.csv                (full setups that leave DETECT)
log_detect_attempt() → logs/frost_attempts.csv           (every DETECT, including near-misses)
log_execution()      → logs/trades/trades_YYYY-MM-DD.csv (broker fills, per day)

Exit fields on trade_log stay blank until update_trade_outcome() patches the
row when the MT5 position closes.

Paths are anchored to the project root, not the working directory — the bot is
launched both from the root and from src/, and a relative path silently splits
the log into two directories that never reconcile against each other.
"""

from __future__ import annotations

import csv
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config import LOG_DIR as _LOG_DIR

log = logging.getLogger(__name__)

LOG_DIR      = Path(_LOG_DIR)
LOG_PATH     = LOG_DIR / "trade_log.csv"
ATTEMPT_PATH = LOG_DIR / "frost_attempts.csv"
TRADES_DIR   = LOG_DIR / "trades"

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

ATTEMPT_COLUMNS = [
    "timestamp",
    "symbol",
    "h1_trend",
    "aoi_distance_pips",
    "sweep_reclaim",
    "outcome",
    "reason",
]

EXECUTION_COLUMNS = [
    "timestamp",
    "symbol",
    "strategy",
    "direction",
    "entry_price",
    "sl",
    "tp",
    "lot",
    "risk_reward",
    "ticket",
    "account_balance",
    "account_equity",
]


def log_signal(row: dict) -> None:
    """
    Append one signal row to logs/trade_log.csv.

    Missing columns are written as empty strings. Extra keys are ignored.
    Never raises — a log failure must not block trading.
    """
    _append_row(LOG_PATH, COLUMNS, row, label="signal log")


def log_detect_attempt(row: dict) -> None:
    """
    Append one DETECT attempt to logs/frost_attempts.csv.

    Captures near-misses (distance-to-level, missing trigger, wrong regime, etc.)
    so a week of observation can answer whether the active strategy is too
    strict. Both Flow and Frost write this same schema.
    Never raises.
    """
    payload = dict(row)
    if not payload.get("timestamp"):
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    _append_row(ATTEMPT_PATH, ATTEMPT_COLUMNS, payload, label="detect attempt log")


def log_execution(symbol: str, signal: dict, lot: float, ticket: int,
                  price: float, sl: float, tp: float, account) -> None:
    """
    Append a broker fill to logs/trades/trades_YYYY-MM-DD.csv.

    Called by trade_executor after a confirmed fill. Never raises — a failed
    log write must not lose an executed trade.
    """
    path = TRADES_DIR / f"trades_{datetime.now().strftime('%Y-%m-%d')}.csv"
    _append_row(path, EXECUTION_COLUMNS, {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "symbol":          symbol,
        # No strategy default: a signal that reached a fill without naming itself
        # is a bug, and silently stamping it with whichever strategy is current
        # would hide that in the one CSV that records real money.
        "strategy":        signal.get("strategy_name", "unknown"),
        "direction":       signal.get("direction", ""),
        "entry_price":     price,
        "sl":              sl,
        "tp":              tp,
        "lot":             lot,
        "risk_reward":     signal.get("risk_reward_ratio"),
        "ticket":          ticket,
        "account_balance": getattr(account, "balance", None),
        "account_equity":  getattr(account, "equity", None),
    }, label="execution log")


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

    rows = _read_rows(LOG_PATH)
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

    try:
        _rewrite(rows, LOG_PATH, COLUMNS)
        return True
    except (OSError, csv.Error) as e:
        log.error(f"[trade_logger] Could not update ticket {ticket}: {e}")
        return False


def list_open_fills() -> list[dict]:
    """Rows that have an MT5 ticket but no exit yet — waiting for SL/TP/close."""
    rows = _read_rows(LOG_PATH) or []

    open_fills: list[dict] = []
    for row in rows:
        ticket = str(row.get("mt5_ticket", "")).strip()
        if not ticket or ticket in ("0", "None"):
            continue
        if str(row.get("exit_price", "")).strip():
            continue
        open_fills.append({
            "ticket": str(ticket),
            "sl":     str(row.get("sl", "")).strip(),
            "tp":     str(row.get("tp", "")).strip(),
        })
    return open_fills


def attach_ticket(symbol: str, mt5_ticket: int | str, outcome: str = "EXECUTED",
                  sl: float | str | None = None, tp: float | str | None = None) -> bool:
    """
    Attach a ticket to the HITL row this fill came from (manual confirm path).

    Graph writes outcome=HITL with a blank ticket; bot.py calls this after
    the user confirms and place_order succeeds.

    Pass the confirmed signal's sl/tp whenever they are known. Matching on
    "latest HITL row for the symbol" alone mis-attributes the ticket when more
    than one signal is queued on that symbol — the newest row wins even though
    the user confirmed an older one, so tickets, exit prices and P&L all land on
    the wrong signals. Levels disambiguate; the newest-row rule is the fallback.
    """
    ticket = str(mt5_ticket).strip()
    if not ticket or ticket in ("0", "None"):
        return False

    return _patch_latest_hitl(symbol, mt5_ticket=ticket, outcome=outcome, sl=sl, tp=tp)


def mark_hitl_outcome(symbol: str, outcome: str) -> bool:
    """Update the latest HITL row for symbol when confirm/reject fails or expires."""
    return _patch_latest_hitl(symbol, outcome=outcome)


def _patch_latest_hitl(symbol: str, mt5_ticket: str | None = None, outcome: str | None = None,
                       sl: float | str | None = None, tp: float | str | None = None) -> bool:
    rows = _read_rows(LOG_PATH)
    if not rows:
        return False

    candidates = [
        row for row in rows
        if str(row.get("symbol", "")).upper() == str(symbol).upper()
        and str(row.get("outcome", "")).upper() == "HITL"
        and not str(row.get("mt5_ticket", "")).strip()
    ]
    if not candidates:
        return False

    target = _match_by_levels(candidates, sl, tp) or candidates[-1]

    if mt5_ticket is not None:
        target["mt5_ticket"] = mt5_ticket
    if outcome is not None:
        target["outcome"] = outcome

    try:
        _rewrite(rows, LOG_PATH, COLUMNS)
        return True
    except (OSError, csv.Error) as e:
        log.error(f"[trade_logger] Could not patch HITL row for {symbol}: {e}")
        return False


def _match_by_levels(candidates: list[dict], sl, tp) -> dict | None:
    """
    The candidate row whose SL and TP match this fill, or None.

    Levels are rounded to the symbol's digits before the order is sent, so the
    true row is within a fraction of a pip; a looser match is a different signal
    and it is safer to fall back than to attach a ticket to the wrong trade.
    """
    if sl is None or tp is None:
        return None
    try:
        want_sl, want_tp = float(sl), float(tp)
    except (TypeError, ValueError):
        return None

    best, best_err = None, None
    for row in candidates:
        try:
            err = abs(float(row.get("sl") or 0) - want_sl) + abs(float(row.get("tp") or 0) - want_tp)
        except (TypeError, ValueError):
            continue
        if best_err is None or err < best_err:
            best, best_err = row, err

    tolerance = max(abs(want_sl), abs(want_tp), 1.0) * 1e-4
    return best if best_err is not None and best_err <= tolerance else None


def _append_row(path: Path, columns: list[str], row: dict, *, label: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists() or path.stat().st_size == 0
        payload = {col: _cell(row.get(col)) for col in columns}

        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            if is_new:
                writer.writeheader()
            writer.writerow(payload)
    except (OSError, csv.Error) as e:
        log.error(f"[trade_logger] Could not write {label}: {e}")


def _read_rows(path: Path) -> list[dict] | None:
    """Parsed CSV rows, or None if the file is missing or unreadable."""
    try:
        if not path.exists():
            return None
        with path.open("r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        log.error(f"[trade_logger] Could not read {path.name}: {e}")
        return None


def _rewrite(rows: list[dict], path: Path, columns: list[str]) -> None:
    """
    Atomic rewrite so a crash mid-write doesn't truncate the log.

    path and columns are required, never defaulted: a default would bind the
    module-level LOG_PATH at definition time, so a caller that thought it had
    redirected the log would still overwrite the real one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".csv", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({col: _cell(row.get(col)) for col in columns})
        Path(tmp_name).replace(path)
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
