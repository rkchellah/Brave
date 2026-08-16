"""
risk.py — the single definition of Brave's daily loss limit.

bot.py and graph.py both gate on "have we lost too much today". They used to
answer that question two different ways: bot.py compared equity against the
equity the trading day opened at, graph.py compared equity against *balance*.
The second measures unrealised drawdown on whatever happens to be open, not
the day's loss, so the two gates disagreed — one could block a trade the other
would allow. Both now call in here.

Session state is module-level so the two callers share one opening equity for
the process. It resets when the local calendar date rolls over, matching the
behaviour bot.py had before this module existed.
"""

from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

_session_start_equity: float | None = None
_session_date: str | None = None


def session_metrics(account) -> tuple[float, float]:
    """
    Session P&L and percentage against the equity the day opened at.

    Returns (0.0, 0.0) when account info is unavailable rather than raising —
    callers are on the trading hot path and treat "unknown" as "not a loss".
    """
    global _session_start_equity, _session_date

    if not account:
        return 0.0, 0.0

    today = datetime.now().date().isoformat()
    if _session_date != today or _session_start_equity is None:
        _session_start_equity = account.equity
        _session_date = today
        log.info(f"Daily session started — Equity: ${_session_start_equity:.2f}")

    if not _session_start_equity:
        return 0.0, 0.0

    session_pnl = account.equity - _session_start_equity
    session_pct = session_pnl / _session_start_equity * 100
    return session_pnl, session_pct


def session_start_equity() -> float | None:
    """Opening equity for the current session, or None before the first read."""
    return _session_start_equity


def daily_loss_limit_hit(account, limit_pct: float) -> tuple[bool, str]:
    """
    Whether the session drawdown has reached limit_pct of opening equity.

    Returns (hit, reason). reason is empty unless hit is True.
    """
    if not account:
        return False, ""

    session_pnl, _ = session_metrics(account)
    start_equity = _session_start_equity
    if not start_equity:
        return False, ""

    loss_limit = -(start_equity * limit_pct)
    if session_pnl > loss_limit:
        return False, ""

    return True, (
        f"Daily loss limit hit (${session_pnl:.2f} <= ${loss_limit:.2f}, "
        f"{limit_pct:.1%} of ${start_equity:.2f} opening equity)"
    )
