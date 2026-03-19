"""
sentiment_gate.py — Brave Sentiment Gate

Drop-in module for bot.py.
Reads the latest sentiment from Firebase and decides whether to
allow, warn, or block a signal based on AI sentiment alignment.

Usage in bot.py (inside execute_signal, before order placement):

    from sentiment_gate import SentimentGate

    # Instantiate once in BraveBot.__init__:
    self.sentiment_gate = SentimentGate(user_id=USER_ID)

    # Call before every trade execution:
    decision = self.sentiment_gate.evaluate(symbol, signal["direction"])
    if decision.block:
        log.warning("Signal blocked by sentiment gate: %s", decision.reason)
        return
    if decision.warn:
        log.warning("Sentiment warning: %s", decision.reason)
        # Trade still executes — warn only. Bot continues.

Firebase path read:
    users/{USER_ID}/sentiment/{symbol}
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging

from firebase_admin import db

log = logging.getLogger("BraveSentiment.Gate")

# How old a sentiment reading can be before we treat it as stale
STALE_THRESHOLD_MINUTES: int = 90

# Score thresholds
BLOCK_THRESHOLD: float  = 0.55   # oppose with this strength → block trade
WARN_THRESHOLD:  float  = 0.25   # oppose with this strength → warn only


@dataclass
class GateDecision:
    allow:  bool
    warn:   bool
    block:  bool
    reason: str
    score:  float
    bias:   str


class SentimentGate:
    """
    Reads Firebase sentiment and evaluates whether a proposed trade
    direction aligns with the current AI sentiment.

    Gate is non-blocking by default (warn mode only) — set
    GATE_HARD_BLOCK = True in config.py to enable hard blocking.
    """

    def __init__(self, user_id: str, hard_block: bool = False) -> None:
        self.user_id   = user_id
        self.hard_block = hard_block

    def _fetch(self, symbol: str) -> dict | None:
        """Read latest sentiment from Firebase."""
        try:
            ref  = db.reference(f"users/{self.user_id}/sentiment/{symbol}")
            data = ref.get()
            return data if isinstance(data, dict) else None
        except Exception as exc:
            log.debug("Could not read sentiment for %s: %s", symbol, exc)
            return None

    def _is_stale(self, updated_at: str) -> bool:
        """Return True if sentiment is older than STALE_THRESHOLD_MINUTES."""
        try:
            ts  = datetime.fromisoformat(updated_at)
            age = datetime.now(timezone.utc) - ts
            return age > timedelta(minutes=STALE_THRESHOLD_MINUTES)
        except Exception:
            return True

    def evaluate(self, symbol: str, signal_direction: str) -> GateDecision:
        """
        Evaluate whether a signal direction aligns with current sentiment.

        signal_direction: "BUY" | "SELL"
        Returns GateDecision with allow/warn/block flags and reason text.
        """
        data = self._fetch(symbol)

        # No data or stale → allow with note
        if not data:
            return GateDecision(
                allow=True, warn=False, block=False,
                reason="No sentiment data — proceeding on technicals only.",
                score=0.0, bias="NEUTRAL",
            )

        updated_at = data.get("updated_at", "")
        if self._is_stale(updated_at):
            return GateDecision(
                allow=True, warn=True, block=False,
                reason=f"Sentiment data is stale (>{STALE_THRESHOLD_MINUTES}min old). Proceeding with caution.",
                score=0.0, bias="NEUTRAL",
            )

        bias:       str   = data.get("direction_bias", "NEUTRAL")
        score:      float = float(data.get("score", 0.0))
        confidence: str   = data.get("confidence", "LOW")

        # Determine opposition
        bullish_signal = signal_direction == "BUY"
        sentiment_bull = bias == "BULLISH"
        sentiment_bear = bias == "BEARISH"
        opposed = (bullish_signal and sentiment_bear) or (not bullish_signal and sentiment_bull)

        abs_score = abs(score)

        if not opposed or bias == "NEUTRAL":
            reason = (
                f"Sentiment aligned: {bias} (score {score:+.2f}, {confidence} confidence)."
                if bias != "NEUTRAL"
                else f"Sentiment neutral (score {score:+.2f}) — no conflict."
            )
            return GateDecision(
                allow=True, warn=False, block=False,
                reason=reason, score=score, bias=bias,
            )

        # Opposed — decide severity
        if abs_score >= BLOCK_THRESHOLD and confidence in ("HIGH", "MEDIUM") and self.hard_block:
            return GateDecision(
                allow=False, warn=False, block=True,
                reason=(
                    f"BLOCKED: Sentiment strongly {bias} (score {score:+.2f}, {confidence}) "
                    f"opposes {signal_direction} signal on {symbol}."
                ),
                score=score, bias=bias,
            )

        if abs_score >= WARN_THRESHOLD:
            return GateDecision(
                allow=True, warn=True, block=False,
                reason=(
                    f"WARNING: Sentiment {bias} (score {score:+.2f}, {confidence}) "
                    f"opposes {signal_direction}. Proceeding — monitor closely."
                ),
                score=score, bias=bias,
            )

        # Weak opposition — allow silently
        return GateDecision(
            allow=True, warn=False, block=False,
            reason=f"Weak sentiment opposition ({bias} {score:+.2f}) — within tolerance.",
            score=score, bias=bias,
        )
