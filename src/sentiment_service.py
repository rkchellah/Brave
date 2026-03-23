"""
sentiment_service.py — Brave AI Sentiment Microservice

Runs alongside bot.py as a separate process.
Every POLL_INTERVAL seconds it:
  1. Fetches forex news headlines via Finnhub (forex-aware, 60 req/min free)
  2. Fetches recent X/Twitter posts via Grok (xAI) for each symbol
  3. GPT-4 analyses the Finnhub headlines into structured sentiment
  4. Synthesises both into a single SentimentResult per symbol
  5. Writes results to Firebase: users/{USER_ID}/sentiment/{symbol}

Bot.py reads Firebase sentiment before executing signals (optional gate).
Mobile app reads Firebase sentiment to display AI Insights screen.

Run:
    python src/sentiment_service.py

Requires in config.py:
    OPENAI_API_KEY        str   — platform.openai.com/api-keys
    XAI_API_KEY           str   — console.x.ai
    FINNHUB_API_KEY       str   — finnhub.io/dashboard (free tier)
    FIREBASE_DATABASE_URL str
    USER_ID               str
    SYMBOLS               list[str]
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler

import finnhub
import firebase_admin
from firebase_admin import credentials, db
from openai import OpenAI

import os
import sys

# Ensure the root directory is in the path so we can import config.py
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    FIREBASE_DATABASE_URL,
    FINNHUB_API_KEY,
    OPENAI_API_KEY,
    SYMBOLS,
    USER_ID,
    XAI_API_KEY,
)

# ── Constants ────────────────────────────────────────────────────────────────

POLL_INTERVAL: int = 120          # seconds between full sentiment cycles (2 min)
NEWS_HEADLINES: int = 10          # headlines to feed GPT-4 per symbol
GROK_POSTS: int = 20              # X posts to feed Grok per symbol
SENTIMENT_TTL_MINUTES: int = 60   # app shows "stale" warning after this

# Map trading symbols → plain-English search terms for news + X
SYMBOL_KEYWORDS: dict[str, dict[str, str]] = {
    "EURUSD": {
        "news": "EUR USD euro dollar forex",
        "x":    "EURUSD euro dollar forex",
    },
    "GBPUSD": {
        "news": "GBP pound sterling BOE Bank of England Britain UK economy dollar",
        "x":    "GBPUSD pound dollar sterling",
    },
    "XAUUSD": {
        "news": "gold XAU bullion commodities safe haven precious metals Fed rates inflation",
        "x":    "XAUUSD gold price bullion",
    },
    "US30":   {
        "news": "Dow Jones US30 Wall Street",
        "x":    "US30 Dow Jones",
    },
    "NAS100": {
        "news": "Nasdaq NAS100 tech stocks",
        "x":    "NAS100 Nasdaq tech",
    },
}

# ── Logging ──────────────────────────────────────────────────────────────────

def _build_logger() -> logging.Logger:
    logger = logging.getLogger("BraveSentiment")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = TimedRotatingFileHandler(
        "sentiment_log", when="midnight", backupCount=7, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


log = _build_logger()

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class SentimentResult:
    symbol: str
    direction_bias: str          # "BULLISH" | "BEARISH" | "NEUTRAL"
    score: float                 # -1.0 (max bearish) to +1.0 (max bullish)
    confidence: str              # "HIGH" | "MEDIUM" | "LOW"
    gpt4_summary: str            # 1-2 sentence news summary
    grok_summary: str            # 1-2 sentence X/social summary
    risk_advisory: str           # Plain-English warning or all-clear
    trade_alignment: str         # "ALIGNED" | "OPPOSED" | "NEUTRAL"
    signal_direction: str        # last known bot signal direction (filled later)
    news_headlines: list[str]    # raw headlines used (for transparency)
    updated_at: str              # ISO timestamp


# ── Firebase ─────────────────────────────────────────────────────────────────

def _init_firebase() -> None:
    """Initialise Firebase Admin SDK (idempotent)."""
    if not firebase_admin._apps:
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})
        log.info("Firebase initialised")


def _write_sentiment(result: SentimentResult) -> None:
    """Write a SentimentResult to users/{USER_ID}/sentiment/{symbol}."""
    ref = db.reference(f"users/{USER_ID}/sentiment/{result.symbol}")
    ref.set(asdict(result))
    log.info(
        "Firebase ← sentiment/%s: %s %.2f (%s)",
        result.symbol,
        result.direction_bias,
        result.score,
        result.confidence,
    )


def _read_last_signal_direction(symbol: str) -> str:
    """Read the most recent bot signal direction from Firebase alerts."""
    try:
        alerts_ref = db.reference(f"users/{USER_ID}/alerts")
        alerts = alerts_ref.order_by_child("symbol").equal_to(symbol).limit_to_last(1).get()
        if alerts:
            last = list(alerts.values())[0]
            return last.get("direction", "UNKNOWN")
    except Exception as exc:
        log.debug("Could not read last signal for %s: %s", symbol, exc)
    return "UNKNOWN"


# ── News fetch (Finnhub) ──────────────────────────────────────────────────────

# Finnhub forex-aware category — covers all FX pairs and commodities
FINNHUB_FOREX_CATEGORY: str = "forex"
FINNHUB_GENERAL_CATEGORY: str = "general"

# Finnhub symbol map — for symbol-specific news lookup (company/ETF proxies)
# For pure FX pairs Finnhub uses category filtering + keyword matching
FINNHUB_SYMBOL_MAP: dict[str, str | None] = {
    "EURUSD":  None,    # use category + keyword filter
    "GBPUSD":  None,    # use category + keyword filter
    "XAUUSD":  "GLD",   # SPDR Gold ETF — proxy for gold news
    "US30":    "DIA",   # Dow Jones ETF proxy
    "NAS100":  "QQQ",   # Nasdaq ETF proxy
}

_finnhub_client: finnhub.Client | None = None


def _get_finnhub() -> finnhub.Client:
    """Return a cached Finnhub client (instantiated once)."""
    global _finnhub_client
    if _finnhub_client is None:
        _finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)
        log.info("Finnhub client initialised")
    return _finnhub_client


def _fetch_headlines(symbol: str) -> list[str]:
    """
    Fetch recent forex/market news headlines for the symbol via Finnhub.

    Strategy:
      - For FX pairs (EURUSD, GBPUSD): fetch general forex category news,
        then keyword-filter for the pair's currencies.
      - For commodity/index proxies (XAUUSD, US30, NAS100): fetch
        company news for the ETF proxy ticker for tighter relevance.

    Free tier: 60 calls/min — well within Brave's 15-min cycle.
    """
    client = _get_finnhub()
    keywords: list[str] = SYMBOL_KEYWORDS.get(symbol, {}).get("news", symbol).split()
    proxy_ticker: str | None = FINNHUB_SYMBOL_MAP.get(symbol)

    try:
        if proxy_ticker:
            # Use company news for ETF proxies — better signal-to-noise
            from datetime import timedelta
            today = datetime.now(timezone.utc)
            date_from = (today - timedelta(days=2)).strftime("%Y-%m-%d")
            date_to   = today.strftime("%Y-%m-%d")
            articles  = client.company_news(proxy_ticker, _from=date_from, to=date_to)
        else:
            # General category — forex category is sparse on free tier
            articles = client.general_news(FINNHUB_GENERAL_CATEGORY, min_id=0)

        # Extract headlines and keyword-filter for relevance
        headlines: list[str] = []
        for article in articles:
            headline: str = article.get("headline", "")
            if not headline:
                continue
            # Keep if any keyword appears in headline (case-insensitive)
            if any(kw.lower() in headline.lower() for kw in keywords):
                headlines.append(headline)
            if len(headlines) >= NEWS_HEADLINES:
                break

        # If keyword filter was too strict, fall back to top unfiltered headlines
        if not headlines and not proxy_ticker:
            headlines = [
                a["headline"] for a in articles[:NEWS_HEADLINES]
                if a.get("headline")
            ]

        log.debug("Finnhub: %d headlines for %s", len(headlines), symbol)
        return headlines

    except Exception as exc:
        log.warning("Finnhub fetch failed for %s: %s", symbol, exc)
        return []


# ── GPT-4 sentiment ──────────────────────────────────────────────────────────

def _analyse_with_gpt4(symbol: str, headlines: list[str]) -> dict:
    """
    Try Gemini first. If it fails or no key, fallback to GPT-4, then Grok.
    """
    if not headlines:
        return _neutral_gpt4(symbol)

    system_prompt = (
        "You are a professional forex and commodities analyst. "
        "You receive recent news headlines about a trading instrument and "
        "return a structured JSON sentiment analysis. "
        "Be concise, factual, and risk-aware. "
        "Never invent data not present in the headlines."
    )

    user_prompt = f"""Analyse sentiment for {symbol} based on these headlines:

{chr(10).join(f'- {h}' for h in headlines)}

Return ONLY valid JSON with these exact keys:
{{
  "direction_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "score": float between -1.0 and 1.0,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "summary": "1-2 sentence summary of the news sentiment",
  "risk_advisory": "1 sentence plain-English risk warning or all-clear for a trader"
}}"""

    # 1. Try Gemini
    from config import GEMINI_API_KEY
    if GEMINI_API_KEY:
        try:
            from google import genai
            from google.genai import types
            
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    temperature=0.2,
                )
            )
            raw = response.text
            data = json.loads(raw)
            data["source"] = "Gemini"
            log.debug("Gemini result for %s: %s", symbol, data)
            return data
        except Exception as exc:
            log.warning("Gemini failed for %s: %s. Falling back to GPT-4.", symbol, exc)

    # 2. Try GPT-4
    try:
        from config import OPENAI_API_KEY
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=300,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        data["source"] = "GPT-4"
        log.debug("GPT-4 result for %s: %s", symbol, data)
        return data
    except Exception as exc:
        log.warning("GPT-4 failed for %s: %s. Falling back to Grok.", symbol, exc)
        try:
            from config import XAI_API_KEY
            grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
            response = grok_client.chat.completions.create(
                model="grok-3",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=300,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            data["source"] = "Grok"
            log.debug("Grok fallback result for %s: %s", symbol, data)
            return data
        except Exception as grok_exc:
            log.warning("Grok fallback also failed for %s: %s", symbol, grok_exc)
            return _neutral_gpt4(symbol)

def _neutral_gpt4(symbol: str) -> dict:
    return {
        "direction_bias": "NEUTRAL",
        "score":          0.0,
        "confidence":     "LOW",
        "summary":        f"No recent news data available for {symbol}.",
        "risk_advisory":  "No news data — proceed with technical signals only.",
        "source":         "Neutral (Fail)",
    }


# ── Grok (xAI) sentiment ─────────────────────────────────────────────────────

def _analyse_with_grok(symbol: str) -> dict:
    """
    Call Grok via xAI API (OpenAI-compatible).
    Grok has native access to X/Twitter — no separate scraping needed.
    Returns dict with: direction_bias, score, confidence, summary
    """
    keywords = SYMBOL_KEYWORDS.get(symbol, {}).get("x", symbol)

    client = OpenAI(
        api_key=XAI_API_KEY,
        base_url="https://api.x.ai/v1",
    )

    system_prompt = (
        "You are a trading sentiment analyst with access to real-time X (Twitter) posts. "
        "Analyse the current social sentiment for the requested trading symbol. "
        "Return only structured JSON. Be factual and brief."
    )

    user_prompt = f"""What is the current X/Twitter sentiment for {symbol} ({keywords})?
Search recent posts and return ONLY valid JSON:
{{
  "direction_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "score": float between -1.0 and 1.0,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "summary": "1-2 sentence summary of social media sentiment"
}}"""

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=200,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        log.debug("Grok result for %s: %s", symbol, data)
        return data
    except Exception as exc:
        log.warning("Grok failed for %s: %s", symbol, exc)
        return _neutral_grok(symbol)


def _neutral_grok(symbol: str) -> dict:
    return {
        "direction_bias": "NEUTRAL",
        "score":          0.0,
        "confidence":     "LOW",
        "summary":        f"No X/Twitter data available for {symbol}.",
    }


# ── Synthesiser ───────────────────────────────────────────────────────────────

def _synthesise(
    symbol: str,
    gpt4: dict,
    grok: dict,
    headlines: list[str],
) -> SentimentResult:
    """
    Combine GPT-4 (news) + Grok (social) into a single SentimentResult.

    Weighting:
      - GPT-4 score: 60%  (news is more reliable for forex fundamentals)
      - Grok score:  40%  (social gives early signals but is noisier)
    """
    gpt4_score: float = float(gpt4.get("score", 0.0))
    grok_score: float = float(grok.get("score", 0.0))
    combined_score: float = round((gpt4_score * 0.6) + (grok_score * 0.4), 3)

    # Direction from combined score
    if combined_score >= 0.15:
        direction_bias = "BULLISH"
    elif combined_score <= -0.15:
        direction_bias = "BEARISH"
    else:
        direction_bias = "NEUTRAL"

    # Confidence: use the higher of the two, downgrade if they disagree
    gpt4_conf = gpt4.get("confidence", "LOW")
    grok_conf = grok.get("confidence", "LOW")
    conf_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

    gpt4_bias = gpt4.get("direction_bias", "NEUTRAL")
    grok_bias = grok.get("direction_bias", "NEUTRAL")

    if gpt4_bias != "NEUTRAL" and grok_bias != "NEUTRAL" and gpt4_bias != grok_bias:
        # Sources disagree — cap confidence at MEDIUM
        confidence = "LOW" if max(conf_rank[gpt4_conf], conf_rank[grok_conf]) <= 1 else "MEDIUM"
    else:
        rank = max(conf_rank[gpt4_conf], conf_rank[grok_conf])
        confidence = {3: "HIGH", 2: "MEDIUM", 1: "LOW"}[rank]

    # Risk advisory: prefer GPT-4 (news-sourced), fall back to generic
    risk_advisory: str = gpt4.get("risk_advisory", "No specific risk advisory available.")

    # Trade alignment (filled in after reading last bot signal)
    last_signal = _read_last_signal_direction(symbol)
    if last_signal == "UNKNOWN" or direction_bias == "NEUTRAL":
        trade_alignment = "NEUTRAL"
    elif (last_signal == "BUY" and direction_bias == "BULLISH") or \
         (last_signal == "SELL" and direction_bias == "BEARISH"):
        trade_alignment = "ALIGNED"
    else:
        trade_alignment = "OPPOSED"

    return SentimentResult(
        symbol=symbol,
        direction_bias=direction_bias,
        score=combined_score,
        confidence=confidence,
        gpt4_summary=gpt4.get("summary", ""),
        grok_summary=grok.get("summary", ""),
        risk_advisory=risk_advisory,
        trade_alignment=trade_alignment,
        signal_direction=last_signal,
        news_headlines=headlines[:5],   # store top 5 for transparency in app
        updated_at=datetime.now(timezone.utc).isoformat(),
        source=gpt4.get("source", "Unknown"),
    )


# ── AI trade reasoning ────────────────────────────────────────────────────────

def generate_trade_reasoning(
    symbol: str,
    direction: str,
    strategy_name: str,
    entry_price: float,
    sl: float,
    tp: float,
    sentiment: SentimentResult | None = None,
) -> str:
    """
    Ask GPT-4 to explain in plain English why this trade was taken.
    Called by bot.py after a signal is executed, result pushed to Firebase alert.

    Returns a 2-3 sentence plain-English explanation.
    """
    sentiment_context = ""
    if sentiment:
        sentiment_context = (
            f"Current sentiment: {sentiment.direction_bias} (score {sentiment.score:.2f}). "
            f"News: {sentiment.gpt4_summary} "
            f"Social: {sentiment.grok_summary}"
        )

    prompt = f"""A trading bot just placed this trade:
Symbol: {symbol}
Direction: {direction}
Strategy: {strategy_name}
Entry: {entry_price}, SL: {sl}, TP: {tp}
{sentiment_context}

In 2-3 plain-English sentences, explain why this trade makes sense technically and fundamentally.
Write as if explaining to a non-expert trader. Be honest if sentiment opposes the direction."""

    # 1. Try Gemini
    from config import GEMINI_API_KEY
    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=genai.types.GenerateContentConfig(temperature=0.4)
            )
            return response.text.strip()
        except Exception as exc:
            log.warning("Trade reasoning (Gemini) failed for %s: %s. Falling back to GPT-4.", symbol, exc)

    # 2. Try GPT-4
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=150,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        log.warning("Trade reasoning (GPT-4) failed for %s: %s. Falling back to Grok.", symbol, exc)
        try:
            from config import XAI_API_KEY
            grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
            response = grok_client.chat.completions.create(
                model="grok-3",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=150,
            )
            return response.choices[0].message.content.strip()
        except Exception as grok_exc:
            log.warning("Grok fallback reasoning failed for %s: %s", symbol, grok_exc)
            return f"{strategy_name} signal on {symbol}. Check chart for technical confirmation."


# ── Main loop ─────────────────────────────────────────────────────────────────

class SentimentService:
    def __init__(self) -> None:
        _init_firebase()
        # Only process symbols that have keyword mappings
        self.symbols: list[str] = [s for s in SYMBOLS if s in SYMBOL_KEYWORDS]
        log.info(
            "SentimentService ready. Symbols: %s. Cycle: %ds",
            self.symbols,
            POLL_INTERVAL,
        )

    def run_cycle(self) -> None:
        """Run one full sentiment cycle across all symbols."""
        log.info("── Sentiment cycle start ──")
        for symbol in self.symbols:
            try:
                log.info("Analysing %s...", symbol)

                # 1. Fetch news headlines
                headlines = _fetch_headlines(symbol)

                # 2. GPT-4 analyses news
                gpt4_result = _analyse_with_gpt4(symbol, headlines)

                # 3. Grok analyses X/Twitter
                grok_result = _analyse_with_grok(symbol)

                # 4. Synthesise into one result
                result = _synthesise(symbol, gpt4_result, grok_result, headlines)

                # 5. Write to Firebase
                _write_sentiment(result)

                log.info(
                    "%s → %s %.2f | alignment: %s",
                    symbol,
                    result.direction_bias,
                    result.score,
                    result.trade_alignment,
                )

            except Exception as exc:
                log.error("Cycle failed for %s: %s", symbol, exc, exc_info=True)

        log.info("── Sentiment cycle complete ──")

    def run(self) -> None:
        """Main blocking loop."""
        while True:
            try:
                self.run_cycle()
            except Exception as exc:
                log.error("Service error: %s", exc, exc_info=True)
            log.info("Sleeping %ds until next cycle...", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    service = SentimentService()
    service.run()