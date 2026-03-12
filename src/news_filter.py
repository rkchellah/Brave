# news_filter.py — Economic Calendar News Filter
# ─────────────────────────────────────────────────────────────────────
# Fetches ForexFactory's weekly calendar (unofficial JSON endpoint).
# Returns True if it is safe to trade a symbol right now, False if a
# high-impact event is within the PAUSE_BEFORE or PAUSE_AFTER window.
#
# Called once per signal-check cycle — result is cached for
# CACHE_TTL_SECONDS so we don't hammer the endpoint every 60 s.
# ─────────────────────────────────────────────────────────────────────

import logging
import requests
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────
FOREXFACTORY_URL    = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
PAUSE_BEFORE_MIN    = 30          # Pause this many minutes BEFORE event
PAUSE_AFTER_MIN     = 30          # Pause this many minutes AFTER event
CACHE_TTL_SECONDS   = 300         # Re-fetch calendar every 5 minutes
REQUEST_TIMEOUT     = 10          # Seconds before giving up on the request

# Currencies that affect each symbol
SYMBOL_CURRENCIES: dict[str, list[str]] = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "XAUUSD": ["USD"],          # Gold is driven almost entirely by USD events
    "USDJPY": ["USD", "JPY"],
    "USDCHF": ["USD", "CHF"],
    "AUDUSD": ["AUD", "USD"],
    "US30":   ["USD"],
    "NAS100": ["USD"],
    "UK100":  ["GBP"],
    "GER40":  ["EUR"],
}


class NewsFilter:
    """
    Wraps ForexFactory calendar fetching and event-window checking.

    Usage:
        nf = NewsFilter()
        if not nf.is_safe_to_trade("EURUSD"):
            return None   # skip — high-impact event nearby
    """

    def __init__(self):
        self._events: list[dict]    = []
        self._last_fetch: datetime | None = None
        self._fetch_failed: bool    = False   # if endpoint is down, we trade anyway

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC
    # ═══════════════════════════════════════════════════════════════

    def is_safe_to_trade(self, symbol: str) -> bool:
        """
        Returns False if a high-impact event affecting this symbol
        falls within PAUSE_BEFORE_MIN or PAUSE_AFTER_MIN of now.

        If the calendar endpoint is unreachable, returns True so the
        bot doesn't freeze — we log a warning instead.
        """
        self._refresh_if_stale()

        if not self._events:
            # No data — don't block trading, just warn
            if self._fetch_failed:
                log.warning("NewsFilter: calendar unavailable — proceeding without news filter")
            return True

        currencies = SYMBOL_CURRENCIES.get(symbol, [])
        if not currencies:
            return True  # Unknown symbol — don't block

        now      = datetime.now(timezone.utc)
        pause_before = timedelta(minutes=PAUSE_BEFORE_MIN)
        pause_after  = timedelta(minutes=PAUSE_AFTER_MIN)

        for event in self._events:
            if event.get("impact") != "High":
                continue

            event_currency = event.get("currency", "")
            if event_currency not in currencies:
                continue

            event_time = event.get("_parsed_time")
            if event_time is None:
                continue

            # Check if now is within the pause window around this event
            window_start = event_time - pause_before
            window_end   = event_time + pause_after

            if window_start <= now <= window_end:
                log.warning(
                    f"NewsFilter: [{symbol}] HIGH-IMPACT event nearby — "
                    f"{event.get('title', 'Unknown')} ({event_currency}) "
                    f"@ {event_time.strftime('%H:%M UTC')} | "
                    f"Window: {window_start.strftime('%H:%M')}–{window_end.strftime('%H:%M')} UTC"
                )
                return False

        return True

    def next_event_for(self, symbol: str) -> dict | None:
        """
        Returns the next upcoming high-impact event for this symbol,
        or None if there are none in the current week's calendar.
        Useful for logging / status updates.
        """
        self._refresh_if_stale()

        currencies = SYMBOL_CURRENCIES.get(symbol, [])
        now        = datetime.now(timezone.utc)
        upcoming   = []

        for event in self._events:
            if event.get("impact") != "High":
                continue
            if event.get("currency", "") not in currencies:
                continue
            event_time = event.get("_parsed_time")
            if event_time and event_time > now:
                upcoming.append(event)

        if not upcoming:
            return None

        upcoming.sort(key=lambda e: e["_parsed_time"])
        return upcoming[0]

    # ═══════════════════════════════════════════════════════════════
    # INTERNAL
    # ═══════════════════════════════════════════════════════════════

    def _refresh_if_stale(self) -> None:
        now = datetime.now(timezone.utc)

        if (
            self._last_fetch is not None
            and (now - self._last_fetch).total_seconds() < CACHE_TTL_SECONDS
        ):
            return  # Still fresh

        self._fetch()

    def _fetch(self) -> None:
        try:
            log.info("NewsFilter: Fetching ForexFactory calendar...")
            resp = requests.get(FOREXFACTORY_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()

            raw: list[dict] = resp.json()
            parsed = []

            for item in raw:
                # Parse the datetime string ForexFactory provides
                # Format: "01-06-2025T08:30:00-0500"
                dt_str = item.get("date", "")
                if not dt_str:
                    continue

                try:
                    # ForexFactory times are US/Eastern — convert to UTC
                    from datetime import datetime as dt
                    import re

                    # Handle offset like -0500 or +0000
                    # Python's %z can parse -0500 format
                    event_dt = dt.strptime(dt_str, "%m-%d-%YT%H:%M:%S%z")
                    event_dt_utc = event_dt.astimezone(timezone.utc)
                    item["_parsed_time"] = event_dt_utc
                    parsed.append(item)
                except ValueError:
                    continue  # Skip malformed date entries

            self._events       = parsed
            self._last_fetch   = datetime.now(timezone.utc)
            self._fetch_failed = False

            high_impact = [e for e in parsed if e.get("impact") == "High"]
            log.info(
                f"NewsFilter: Loaded {len(parsed)} events "
                f"({len(high_impact)} high-impact) for this week"
            )

        except requests.exceptions.RequestException as e:
            log.warning(f"NewsFilter: Failed to fetch calendar — {e}")
            self._fetch_failed = True
            self._last_fetch   = datetime.now(timezone.utc)  # Don't retry immediately
            # Keep existing events if we had them
