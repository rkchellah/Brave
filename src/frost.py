"""
frost.py — the Frost strategy: Asian-session mean reversion.

analyze() is the whole public surface. It returns a signal dict or None, and
records why on self.last_attempt so graph.py can log near-misses to
flow_attempts.csv without the strategy knowing anything about CSVs.

Pure analysis: it reads candles and returns a verdict. It places no orders,
writes no files and touches no Firebase.
"""

import logging
from datetime import datetime, time, timezone

import MetaTrader5 as mt5
import numpy as np

log = logging.getLogger(__name__)


class Frost:
    """
    Asian Scalper — Mean Reversion Night Scalper
    ---------------------------------------------
    Type: Mean Reversion / Counter-Trend Scalping
    Inspired by: Forex Fury style night scalping logic

    Core Concept:
        During the Asian session, markets are range-bound and low volatility.
        Price tends to revert to the mean after deviating too far.
        We exploit this by entering when price is "stretched" from its MA
        and fading the move back toward equilibrium.

    Strategy Logic:
        1. Asian session only (00:00 - 06:00 UTC)
        2. Price must be in a range (low ATR, not trending)
        3. Price deviates X pips from the N-period MA on M15
        4. Spread must be below threshold (tight spread = Asian session)
        5. No high-impact news in the next 30 min (uses existing news filter)
        6. Enter in mean-reversion direction
        7. TP: MA level (price returns to mean)
        8. SL: Beyond the deviation extreme + buffer

    Why it works:
        - Asian session has the tightest spreads (low liquidity cost)
        - Range-bound markets mean price is "elastically" pulled back to mean
        - Small TP targets (10-20 pips) give high win rate (70%+ expected)
        - Hard session cutoff prevents holding through volatile London open

    Pairs (quiet, range-bound during Asia):
        EURUSD, USDCHF, EURCHF, USDCAD, GBPUSD

    Key differences from Thunder/Flow:
        - NO trend requirement — actually prefers ranging markets
        - Small TP (1:1 or less RR) but very high win rate
        - Time-critical — all positions MUST close before London open
    """

    # ─── Session ────────────────────────────────────────────────────
    ASIAN_OPEN  = time(0, 0)    # 00:00 UTC (midnight)
    ASIAN_CLOSE = time(6, 0)    # 06:00 UTC (close before London volatility)
    CUTOFF_TIME = time(5, 30)   # 05:30 UTC — no new entries after this

    # ─── MA Settings ────────────────────────────────────────────────
    MA_PERIOD   = 20            # N-period moving average on M15
    ATR_PERIOD  = 14            # ATR for range detection

    # ─── Entry Thresholds ───────────────────────────────────────────
    MIN_DEVIATION_PIPS = 12     # Minimum pip deviation from MA to enter
    MAX_DEVIATION_PIPS = 40     # Maximum — if too far it may be a breakout
    MAX_SPREAD_PIPS    = 2.0    # Only trade if spread is below this (tight Asian spread)
    MAX_ATR_PIPS       = 15.0   # Skip if ATR is too high (volatile, not ranging)
    MIN_ATR_PIPS       = 2.0    # Skip if ATR is too low (dead market)

    # ─── Exit Settings ──────────────────────────────────────────────
    TP_BUFFER_PIPS  = 2.0       # TP slightly before MA (don't chase the exact level)
    SL_BUFFER_PIPS  = 3.0       # SL beyond deviation extreme
    MIN_RR          = 0.4       # Lowered for 60% TP logic
    MAX_CANDLES_HOLD = 16       # Max candles to hold (4 hours on M15) — force close

    def __init__(self, config: dict):
        self.config = config
        # Set by every analyze() call for instrumentation (graph → trade_logger).
        # Does not affect signal decisions.
        self.last_attempt: dict | None = None
        log.info("✅ Frost loaded — Forex Fury style mean reversion")
        log.info("   Session: Asian only (00:00–06:00 UTC)")
        log.info(f"   Entry: {self.MIN_DEVIATION_PIPS}–{self.MAX_DEVIATION_PIPS} pip deviation from {self.MA_PERIOD}-MA")
        log.info(f"   Max spread: {self.MAX_SPREAD_PIPS} pips")
        log.info(f"   Pairs: GBPUSD, USDCAD, EURCHF")

    def _record_attempt(
        self,
        symbol: str,
        *,
        outcome: str,
        reason: str = "",
        h1_trend: str = "",
        aoi_distance_pips: float | None = None,
        sweep_reclaim: str = "",
    ) -> None:
        """
        Snapshot this DETECT attempt for CSV logging — no trading side effects.

        Signature and emitted keys match Flow's exactly, so both strategies write
        the same flow_attempts.csv schema and a season of data stays comparable.
        The column names are Flow's; for Frost they carry the analogous meaning:

            h1_trend           RANGING / TRENDING — Frost's regime check, which
                               gates the opposite way to Flow's (Frost needs
                               RANGING, Flow needs a trend)
            aoi_distance_pips  signed pip deviation from the MA, not distance to
                               an AOI. Sign carries direction: positive means
                               price is above the MA, so the setup is a SELL.
            sweep_reclaim      whether the mean-reversion setup confirmed, the
                               same yes/no/blank role the sweep+reclaim plays
                               for Flow

        Renaming the columns for Frost would have split the dataset in two; the
        `reason` values are already strategy-specific and disambiguate the rows.
        """
        self.last_attempt = {
            "symbol":            symbol,
            "h1_trend":          h1_trend,
            "aoi_distance_pips": (
                "" if aoi_distance_pips is None else round(float(aoi_distance_pips), 1)
            ),
            "sweep_reclaim":     sweep_reclaim,
            "outcome":           outcome,
            "reason":            reason,
        }

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC ENTRY POINT
    # ═══════════════════════════════════════════════════════════════

    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Main analysis entry point — follows Brave strategy contract exactly.

        Live mode:    provided_rates = None → fetch from MT5
        Backtest:     provided_rates = {mt5.TIMEFRAME_M15: list[dict]}

        Returns signal dict or None.
        """
        self.last_attempt = None
        log.info(f"   [{symbol}] Frost: Analyzing...")

        # ── 1. Session filter ────────────────────────────────────
        if provided_rates is None:
            if not self._is_asian_session():
                log.info(f"   [{symbol}] Frost: Outside Asian session — skipping")
                self._record_attempt(symbol, outcome="no-signal", reason="outside_session")
                return None
            if not self._is_safe_entry_time():
                log.info(f"   [{symbol}] Frost: Past cutoff time (05:30 UTC) — no new entries")
                self._record_attempt(symbol, outcome="no-signal", reason="past_entry_cutoff")
                return None

        # ── 2. Fetch M15 candles ─────────────────────────────────
        candles = self._get_candles(symbol, mt5.TIMEFRAME_M15, 50, provided_rates)
        if not candles or len(candles) < self.MA_PERIOD + self.ATR_PERIOD:
            log.error(f"   [{symbol}] Frost: Insufficient M15 data")
            self._record_attempt(symbol, outcome="no-signal", reason="insufficient_data")
            return None

        # ── 3. Get symbol info ───────────────────────────────────
        point = self._get_point(symbol, candles)

        # ── 4. Spread check (live only) ──────────────────────────
        if provided_rates is None:
            spread_pips = self._get_spread_pips(symbol, point)
            if spread_pips is None:
                self._record_attempt(symbol, outcome="no-signal", reason="no_tick")
                return None
            if spread_pips > self.MAX_SPREAD_PIPS:
                log.info(
                    f"   [{symbol}] Frost: Spread too wide "
                    f"({spread_pips:.1f} > {self.MAX_SPREAD_PIPS} pips) — skipping"
                )
                self._record_attempt(symbol, outcome="no-signal", reason="spread_too_wide")
                return None
            log.info(f"   [{symbol}] Frost: Spread OK ({spread_pips:.1f} pips)")

        # ── 5. ATR range check ───────────────────────────────────
        atr_pips = self._calculate_atr(candles, point)
        if atr_pips is None:
            self._record_attempt(symbol, outcome="no-signal", reason="atr_unavailable")
            return None

        if atr_pips > self.MAX_ATR_PIPS:
            log.info(
                f"   [{symbol}] Frost: ATR too high ({atr_pips:.1f} > {self.MAX_ATR_PIPS}) — market volatile"
            )
            self._record_attempt(symbol, outcome="no-signal", reason="atr_too_high")
            return None

        if atr_pips < self.MIN_ATR_PIPS:
            log.info(
                f"   [{symbol}] Frost: ATR too low ({atr_pips:.1f} < {self.MIN_ATR_PIPS}) — dead market"
            )
            self._record_attempt(symbol, outcome="no-signal", reason="atr_too_low")
            return None

        log.info(f"   [{symbol}] Frost: ATR = {atr_pips:.1f} pips ✅")

        # ── 6. Calculate MA and deviation ────────────────────────
        ma_value = self._calculate_ma(candles)
        if ma_value is None:
            self._record_attempt(symbol, outcome="no-signal", reason="ma_unavailable")
            return None

        current_price = candles[-1]["close"]
        deviation_pips = (current_price - ma_value) / point

        log.info(
            f"   [{symbol}] Frost: Price={current_price:.5f} | "
            f"MA={ma_value:.5f} | Deviation={deviation_pips:+.1f} pips"
        )

        # ── 7. Check deviation threshold ─────────────────────────
        abs_deviation = abs(deviation_pips)

        if abs_deviation < self.MIN_DEVIATION_PIPS:
            log.info(
                f"   [{symbol}] Frost: Deviation too small "
                f"({abs_deviation:.1f} < {self.MIN_DEVIATION_PIPS} pips) — price near MA"
            )
            self._record_attempt(
                symbol,
                outcome="no-signal",
                reason="deviation_too_small",
                aoi_distance_pips=deviation_pips,
            )
            return None

        if abs_deviation > self.MAX_DEVIATION_PIPS:
            log.info(
                f"   [{symbol}] Frost: Deviation too large "
                f"({abs_deviation:.1f} > {self.MAX_DEVIATION_PIPS} pips) — possible breakout"
            )
            self._record_attempt(
                symbol,
                outcome="no-signal",
                reason="deviation_too_large",
                aoi_distance_pips=deviation_pips,
            )
            return None

        log.info(f"   [{symbol}] Frost: Deviation {abs_deviation:.1f} pips ✅")

        # ── 8. Confirm ranging market (no clear trend) ───────────
        if self._is_trending(candles, point):
            log.info(f"   [{symbol}] Frost: Market is trending — mean reversion invalid")
            self._record_attempt(
                symbol,
                outcome="no-signal",
                reason="market_trending",
                h1_trend="TRENDING",
                aoi_distance_pips=deviation_pips,
                sweep_reclaim="no",
            )
            return None

        # ── 9. Generate mean-reversion signal ────────────────────
        direction = "SELL" if deviation_pips > 0 else "BUY"
        signal, build_reason = self._build_signal(
            symbol, current_price, ma_value, deviation_pips, point, direction
        )
        if signal is None:
            self._record_attempt(
                symbol,
                outcome="no-signal",
                reason=build_reason,
                h1_trend="RANGING",
                aoi_distance_pips=deviation_pips,
                sweep_reclaim="yes",
            )
            return None

        self._record_attempt(
            symbol,
            outcome="signal",
            reason="",
            h1_trend="RANGING",
            aoi_distance_pips=deviation_pips,
            sweep_reclaim="yes",
        )
        return signal

    # ═══════════════════════════════════════════════════════════════
    # SIGNAL BUILDER
    # ═══════════════════════════════════════════════════════════════

    def _build_signal(
        self,
        symbol: str,
        current_price: float,
        ma_value: float,
        deviation_pips: float,
        point: float,
        direction: str,
    ) -> tuple[dict | None, str]:
        """
        Build the signal dict — mean reversion toward MA.

        Returns (signal, reason). On failure signal is None and reason names the
        actual cause, so the CSV distinguishes an RR reject from a zero-risk one
        — the same split Flow makes.
        """

        if direction == "BUY":
            # Price below MA — buy back toward MA
            entry = current_price
            tp    = entry + ((ma_value - entry) * 0.8)
            sl    = entry - (abs(deviation_pips) * point) - (self.SL_BUFFER_PIPS * point)
        else:
            # Price above MA — sell back toward MA
            entry = current_price
            tp    = entry - ((entry - ma_value) * 0.8)
            sl    = entry + (abs(deviation_pips) * point) + (self.SL_BUFFER_PIPS * point)

        risk   = abs(entry - sl)
        reward = abs(tp - entry)

        if risk == 0:
            return None, "zero_risk"

        rr_ratio = reward / risk

        if rr_ratio < self.MIN_RR:
            log.info(
                f"   [{symbol}] Frost: RR {rr_ratio:.2f} < {self.MIN_RR} — skipping"
            )
            return None, "rr_below_min"

        lot_size      = self.config.get("lot_size", 0.01)
        contract_size = 100_000
        expected_profit = reward * lot_size * contract_size
        expected_loss   = risk   * lot_size * contract_size

        log.info(
            f"   [{symbol}] Frost: 🎯 {direction} signal | "
            f"Entry={entry:.5f} SL={sl:.5f} TP={tp:.5f} RR={rr_ratio:.2f}"
        )

        signal = {
            "symbol":            symbol,
            "direction":         direction,
            "strategy_name":     "Frost",
            "order_type":        "MARKET",
            "entry_price":       round(entry, 5),
            "suggested_sl":      round(sl,    5),
            "suggested_tp":      round(tp,    5),
            "risk_reward_ratio": round(rr_ratio, 2),
            "expected_profit":   round(expected_profit, 2),
            "expected_loss":     round(expected_loss,   2),
            "probability":       "HIGH" if abs(deviation_pips) > self.MIN_DEVIATION_PIPS * 1.5 else "MEDIUM",
            "structure": {
                "ma_value":        round(ma_value, 5),
                "deviation_pips":  round(deviation_pips, 1),
                "session":         "ASIAN",
                "entry_logic":     "MEAN_REVERSION",
            },
        }
        return signal, ""

    # ═══════════════════════════════════════════════════════════════
    # INDICATORS
    # ═══════════════════════════════════════════════════════════════

    def _calculate_ma(self, candles: list[dict]) -> float | None:
        """Simple moving average of last MA_PERIOD closes."""
        if len(candles) < self.MA_PERIOD:
            return None
        closes = [c["close"] for c in candles[-self.MA_PERIOD:]]
        return float(np.mean(closes))

    def _calculate_atr(self, candles: list[dict], point: float) -> float | None:
        """Average True Range in pips over ATR_PERIOD candles."""
        if len(candles) < self.ATR_PERIOD + 1:
            return None
        trs = []
        for i in range(1, self.ATR_PERIOD + 1):
            c = candles[-i]
            p = candles[-i - 1]
            tr = max(
                c["high"] - c["low"],
                abs(c["high"] - p["close"]),
                abs(c["low"]  - p["close"]),
            )
            trs.append(tr)
        return float(np.mean(trs)) / point

    def _is_trending(self, candles: list[dict], point: float) -> bool:
        """
        Simple trend detector — if price is consistently making HH/HL or LH/LL
        over the last 10 candles, the market is trending and mean reversion is risky.
        Uses a linear regression slope as a proxy.
        """
        if len(candles) < 10:
            return False
        closes = np.array([c["close"] for c in candles[-10:]])
        x      = np.arange(len(closes))
        slope  = float(np.polyfit(x, closes, 1)[0])
        # Convert slope to pips per candle
        slope_pips = abs(slope) / point
        # If price moves more than 1.5 pips per candle on average it's trending
        return slope_pips > 1.5

    # ═══════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════

    def _is_asian_session(self) -> bool:
        now = datetime.now(timezone.utc).time()
        return self.ASIAN_OPEN <= now < self.ASIAN_CLOSE

    def _is_safe_entry_time(self) -> bool:
        """No new entries after 05:30 UTC — avoid holding into London."""
        now = datetime.now(timezone.utc).time()
        return now < self.CUTOFF_TIME

    def _get_candles(
        self,
        symbol: str,
        timeframe: int,
        count: int,
        provided_rates: dict | None,
    ) -> list[dict] | None:
        if provided_rates is not None:
            candles = provided_rates.get(timeframe)
            if candles is None or len(candles) < self.MA_PERIOD + self.ATR_PERIOD + 2:
                return None
            return candles[-count:]

        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) < count:
            return None
        return [
            {
                "time":   int(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": int(r["tick_volume"]),
            }
            for r in rates
        ]

    def _get_point(self, symbol: str, candles: list[dict]) -> float:
        """Get pip point size — try MT5 first, fall back to inference."""
        try:
            info = mt5.symbol_info(symbol)
            if info:
                p = info.point
                # Convert to pip (5-decimal brokers: 1 pip = 10 points)
                return p * 10 if info.digits in (5, 3) else p
        except Exception:
            pass
        # Fallback: infer from candle data
        diff = candles[-1]["high"] - candles[-1]["low"]
        if diff == 0:
            return 0.00001
        s = f"{diff:f}"
        if "." in s:
            decimals = len(s.split(".")[-1].rstrip("0"))
            if decimals > 0:
                return 10 ** (-decimals)
        return 0.00001

    def _get_spread_pips(self, symbol: str, point: float) -> float | None:
        """Get current spread in pips from live MT5 tick."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            log.warning(f"   [{symbol}] Frost: Could not get tick data")
            return None
        return (tick.ask - tick.bid) / point
