"""
news_fetcher.py — Finnhub headlines for a forex symbol, formatted for DeepSeek.

Fetch only, no scoring. The DeepSeek call in graph.py does the judging.

The general-news endpoint returns the same feed for every symbol, so it is
fetched once and cached for CACHE_TTL_SECONDS. Without that, a 60-second loop
over three symbols burns ~4300 Finnhub calls a day and hits the rate limit.
"""

import logging
import re
import threading
import time
from typing import Any

import finnhub

from config import FINNHUB_API_KEY

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300     # Re-fetch the feed at most every 5 minutes
MAX_ATTEMPTS      = 2       # One retry on transient API failure
RETRY_DELAY       = 2.0     # Seconds between attempts
_TOKEN_IN_URL     = re.compile(r"token=[^&\s]+", re.IGNORECASE)

SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "EURUSD": ["euro", "EUR", "ECB", "eurozone", "dollar", "Fed", "FOMC"],
    "GBPUSD": ["pound", "sterling", "GBP", "Bank of England", "BoE", "dollar", "Fed"],
    "XAUUSD": ["gold", "XAU", "bullion", "safe haven", "Fed", "inflation"],
    "USDJPY": ["dollar", "Fed", "yen", "JPY", "Bank of Japan", "BoJ"],
    "USDCHF": ["dollar", "Fed", "Swiss franc", "CHF", "SNB"],
    "USDCAD": ["dollar", "Fed", "Canadian", "CAD", "oil", "crude"],
    "EURCHF": ["euro", "EUR", "ECB", "Swiss franc", "CHF", "SNB"],
}

DEFAULT_KEYWORDS = ["forex", "currency", "dollar", "central bank"]

# ── Module-level feed cache (the bot is multi-threaded via Firebase listeners)
_cache_lock: threading.Lock = threading.Lock()
_cached_feed: list[dict[str, Any]] = []
_cached_at: float = 0.0
_client: finnhub.Client | None = None


def _get_client() -> finnhub.Client | None:
    """Lazily build the Finnhub client so a bad key doesn't crash at import."""
    global _client
    if _client is not None:
        return _client
    if not FINNHUB_API_KEY:
        log.warning("[news_fetcher] FINNHUB_API_KEY is empty — news disabled")
        return None
    try:
        _client = finnhub.Client(api_key=FINNHUB_API_KEY)
        return _client
    except Exception as e:
        log.error(f"[news_fetcher] Could not create Finnhub client: {e}")
        return None


def _safe_err(exc: BaseException) -> str:
    """Strip API keys out of SDK exception URLs before they hit the log."""
    return _TOKEN_IN_URL.sub("token=***", str(exc))


def _fetch_feed() -> tuple[list[dict[str, Any]], bool]:
    """
    Return (feed, ok). ok is False when the request failed and the cache is empty.

    On failure a previous feed is kept — stale headlines beat no headlines.
    """
    global _cached_feed, _cached_at

    with _cache_lock:
        if _cached_feed and (time.monotonic() - _cached_at) < CACHE_TTL_SECONDS:
            return _cached_feed, True

        client = _get_client()
        if client is None:
            return _cached_feed, bool(_cached_feed)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                news = client.general_news("general", min_id=0)
                if not isinstance(news, list):
                    log.warning(f"[news_fetcher] Unexpected payload type: {type(news).__name__}")
                    news = []

                # Keep only well-formed dicts — the API occasionally returns nulls
                feed = [a for a in news if isinstance(a, dict) and a.get("headline")]

                if feed:
                    _cached_feed = feed
                    _cached_at   = time.monotonic()
                    log.info(f"[news_fetcher] Fetched {len(feed)} articles from Finnhub")
                    return _cached_feed, True

                log.warning("[news_fetcher] Finnhub returned no usable articles")
                if _cached_feed:
                    return _cached_feed, True
                return [], False

            except Exception as e:
                log.warning(
                    f"[news_fetcher] Fetch attempt {attempt}/{MAX_ATTEMPTS} failed: {_safe_err(e)}"
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_DELAY)

        log.error("[news_fetcher] All fetch attempts failed — using cached feed if available")
        return _cached_feed, bool(_cached_feed)


def fetch_news_for_symbol(
    symbol: str, max_articles: int = 10,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Fetch Finnhub headlines filtered to `symbol`.

    Returns (articles, data_ok). data_ok is False on fetch failure or when
    nothing relevant survived the filter — ANALYSE must not treat that as
    a genuine UNCERTAIN judgment.
    """
    if not symbol:
        return [], False

    symbol   = symbol.upper()
    keywords = [k.lower() for k in SYMBOL_KEYWORDS.get(symbol, DEFAULT_KEYWORDS)]
    max_articles = max(1, min(int(max_articles or 10), 25))

    feed, feed_ok = _fetch_feed()
    if not feed_ok:
        log.info(f"[news_fetcher] {symbol}: 0 relevant articles (feed unavailable)")
        return [], False

    filtered: list[dict[str, Any]] = []
    for article in feed:
        headline = str(article.get("headline") or "")
        summary  = str(article.get("summary") or "")
        text     = f"{headline} {summary}".lower()

        if any(kw in text for kw in keywords):
            filtered.append({
                "headline": headline.strip(),
                "summary":  summary.strip()[:300],
                "url":      str(article.get("url") or ""),
                "datetime": int(article.get("datetime") or 0),
            })
            if len(filtered) >= max_articles:
                break

    log.info(f"[news_fetcher] {symbol}: {len(filtered)} relevant articles")
    return filtered, bool(filtered)


def format_headlines_for_llm(symbol: str, articles: list[dict[str, Any]]) -> str:
    """Format headlines into clean prompt text for DeepSeek."""
    if not articles:
        return f"No recent news found for {symbol}."

    lines = [f"Recent news headlines for {symbol}:"]
    for i, a in enumerate(articles, 1):
        headline = str(a.get("headline") or "").strip()
        if not headline:
            continue
        lines.append(f"{i}. {headline}")
        summary = str(a.get("summary") or "").strip()
        if summary:
            lines.append(f"   {summary[:200]}")

    return "\n".join(lines) if len(lines) > 1 else f"No recent news found for {symbol}."
