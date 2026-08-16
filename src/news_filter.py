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

# Currencies that affect each symbol.
#
# A symbol missing from this map is NOT blocked by is_safe_to_trade — it falls
# through as "unknown, don't block". That is a fail-open path, so every symbol
# the bot can trade must appear here. Call unmapped_symbols() at startup to
# assert that; USDCAD and EURCHF shipped in SYMBOLS without entries here and
# traded through high-impact news windows unfiltered as a result.
SYMBOL_CURRENCIES: dict[str, list[str]] = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "XAUUSD": ["USD"],          # Gold is driven almost entirely by USD events
    "USDJPY": ["USD", "JPY"],
    "USDCHF": ["USD", "CHF"],
    "USDCAD": ["USD", "CAD"],
    "EURCHF": ["EUR", "CHF"],
    "EURGBP": ["EUR", "GBP"],
    "EURJPY": ["EUR", "JPY"],
    "GBPJPY": ["GBP", "JPY"],
    "AUDUSD": ["AUD", "USD"],
    "NZDUSD": ["NZD", "USD"],
    "US30":   ["USD"],
    "NAS100": ["USD"],
    "UK100":  ["GBP"],
    "GER40":  ["EUR"],
}


def unmapped_symbols(symbols: list[str]) -> list[str]:
    """
    Symbols with no SYMBOL_CURRENCIES entry — the news filter cannot gate these.

    Callers log this at startup: an unmapped symbol trades through news windows
    silently, which is exactly the failure this returns early warning of.
    """
    return [s for s in symbols if s.upper() not in SYMBOL_CURRENCIES]


def _parse_event_time(dt_str: str) -> datetime:
    """
    Parse ForexFactory date stamps to UTC-aware datetime.

    Current feed: ISO 8601 with colon offset — 2026-08-09T19:50:00-04:00
    Legacy fallback: 01-06-2025T08:30:00-0500
    """
    text = (dt_str or "").strip()
    if not text:
        raise ValueError("empty date")

    try:
        # Handles YYYY-MM-DDTHH:MM:SS±HH:MM (and Z)
        event_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # Legacy MM-DD-YYYY… with %z offset (no colon)
        event_dt = datetime.strptime(text, "%m-%d-%YT%H:%M:%S%z")

    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=timezone.utc)
    return event_dt.astimezone(timezone.utc)


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
        # True when the last fetch/parse failed — fail closed until a good load
        self._fetch_failed: bool    = False

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC
    # ═══════════════════════════════════════════════════════════════

    def is_safe_to_trade(self, symbol: str) -> bool:
        """
        Returns False if a high-impact event affecting this symbol
        falls within PAUSE_BEFORE_MIN or PAUSE_AFTER_MIN of now.

        If the calendar fetch/parse failed, returns False (fail closed)
        so a broken filter cannot silently allow trading through news.
        A genuinely empty successful load is treated as a quiet week.
        """
        self._refresh_if_stale()

        if not self._events:
            if self._fetch_failed:
                log.warning(
                    "NewsFilter: calendar fetch/parse failed — "
                    "trading paused as a precaution"
                )
                return False
            log.info(
                "NewsFilter: no high-impact events this week "
                "(calendar loaded successfully, empty or no usable rows)"
            )
            return True

        currencies = SYMBOL_CURRENCIES.get(symbol.upper(), [])
        if not currencies:
            # Fail-open by design — but loudly, so an unmapped symbol shows up
            # in the log instead of quietly trading through every news window.
            log.warning(
                f"NewsFilter: [{symbol}] not in SYMBOL_CURRENCIES — "
                "news filtering is INACTIVE for this symbol"
            )
            return True

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

            raw = resp.json()
            if not isinstance(raw, list):
                raise ValueError(f"expected a list of events, got {type(raw).__name__}")

            parsed = []
            date_failures = 0

            for item in raw:
                if not isinstance(item, dict):
                    continue

                # Feed uses "country" (e.g. USD); keep "currency" for callers
                country = (item.get("country") or item.get("currency") or "").strip().upper()
                if not country:
                    continue
                item["currency"] = country

                dt_str = item.get("date", "")
                if not dt_str:
                    date_failures += 1
                    continue

                try:
                    item["_parsed_time"] = _parse_event_time(dt_str)
                    parsed.append(item)
                except ValueError:
                    date_failures += 1
                    continue

            # Payload had rows but none survived parsing → treat as failure
            if raw and not parsed:
                raise ValueError(
                    f"parsed 0 of {len(raw)} events "
                    f"({date_failures} date/currency failures) — schema mismatch?"
                )

            self._events       = parsed
            self._last_fetch   = datetime.now(timezone.utc)
            self._fetch_failed = False

            high_impact = [e for e in parsed if e.get("impact") == "High"]
            log.info(
                f"NewsFilter: Loaded {len(parsed)} events "
                f"({len(high_impact)} high-impact) for this week"
                + (f" — skipped {date_failures} bad rows" if date_failures else "")
            )

        except requests.exceptions.RequestException as e:
            log.warning(f"NewsFilter: Failed to fetch calendar — {e}")
            self._fetch_failed = True
            self._last_fetch   = datetime.now(timezone.utc)
            # Keep existing events if we had a previous good load

        except (ValueError, TypeError) as e:
            log.warning(f"NewsFilter: Calendar payload unusable — {e}")
            self._fetch_failed = True
            self._last_fetch   = datetime.now(timezone.utc)
            # Keep existing events if we had them; otherwise _events stays empty
            # and is_safe_to_trade will fail closed
