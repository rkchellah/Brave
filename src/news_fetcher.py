"""
news_fetcher.py
Fetches Finnhub headlines for a forex symbol and formats them for DeepSeek.
Replaces sentiment_service.py — fetch only, no scoring.
"""

import finnhub
import logging
from datetime import datetime, timedelta
from config import FINNHUB_API_KEY

SYMBOL_KEYWORDS = {
    "EURUSD": ["euro", "EUR", "ECB", "eurozone", "dollar", "Fed", "FOMC"],
    "GBPUSD": ["pound", "sterling", "GBP", "Bank of England", "BoE", "dollar", "Fed"],
    "XAUUSD": ["gold", "XAU", "bullion", "safe haven", "Fed", "inflation"],
    "USDCHF": ["dollar", "Fed", "Swiss franc", "CHF", "SNB"],
    "USDCAD": ["dollar", "Fed", "Canadian", "CAD", "oil", "crude"],
    "EURCHF": ["euro", "EUR", "ECB", "Swiss franc", "CHF", "SNB"],
}

def fetch_news_for_symbol(symbol: str, max_articles: int = 10) -> list[dict]:
    """Fetch and filter Finnhub headlines relevant to a forex symbol."""
    try:
        client   = finnhub.Client(api_key=FINNHUB_API_KEY)
        keywords = SYMBOL_KEYWORDS.get(symbol, ["forex", "currency"])

        news = client.general_news("general", min_id=0)
        if not news:
            logging.warning(f"[news_fetcher] No news returned from Finnhub for {symbol}")
            return []

        filtered = []
        for article in news:
            text = (article.get("headline", "") + " " + article.get("summary", "")).lower()
            if any(kw.lower() in text for kw in keywords):
                filtered.append({
                    "headline": article.get("headline", ""),
                    "summary":  article.get("summary", "")[:300],
                    "url":      article.get("url", ""),
                    "datetime": article.get("datetime", 0),
                })
            if len(filtered) >= max_articles:
                break

        logging.info(f"[news_fetcher] {symbol}: {len(filtered)} relevant articles")
        return filtered

    except Exception as e:
        logging.error(f"[news_fetcher] Failed: {e}")
        return []


def format_headlines_for_llm(symbol: str, articles: list[dict]) -> str:
    """Format headlines into clean text for DeepSeek prompt."""
    if not articles:
        return f"No recent news found for {symbol}."

    lines = [f"Recent news headlines for {symbol}:"]
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. {a['headline']}")
        if a["summary"]:
            lines.append(f"   {a['summary'][:200]}")
    return "\n".join(lines)
