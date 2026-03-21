import MetaTrader5 as mt5
import numpy as np
import logging
from datetime import datetime, time


class Flow:
    """
    Flow — Multi-Session Scalper
    -----------------------------
    Type: Trend Continuation Scalper (Structure + Sweep + Reclaim)
    Evolved from: fxalexg "1 Hour Day Trading Strategy"

    Role in Brave bundle:
        Thunder catches breakouts during London/NY.
        Frost catches mean reversion during Asia.
        Flow fills the gaps — pre-London drift, London/NY pullbacks,
        and the London-NY bridge — by scalping pullbacks to structure
        at M15 level across all active sessions.

    Session coverage (UTC):
        06:00 – 08:00  Pre-London drift
        08:00 – 11:00  London (alongside Thunder)
        11:00 – 13:00  London-NY bridge
        13:00 – 16:00  New York (alongside Thunder)

    Why Flow + Thunder together work:
        Thunder enters on M15 breakout of a 5-candle range.
        Flow enters on M15 pullback to H1 structure after a move.
        They rarely compete for the same entry — Thunder breaks out,
        Flow fades the retest.

    Logic:
        1. H1 trend must be defined (UPTREND or DOWNTREND) — no ranging
        2. Find strongest AOI on H1 (2+ touches — faster than original 3)
        3. Wait for M15 sweep + reclaim of that AOI
        4. ATR-based SL (1.5× ATR on M15) — consistent sizing
        5. TP at 1.5:1 RR — scalper target, higher win rate
        6. No entry within 30 min of high-impact news (news_filter handles this)

    Key differences from original Flow:
        - H4 trend removed — H1 only for speed
        - M15 entry candles instead of H1
        - AOI min touches: 2 (was 3)
        - RR: 1.5 minimum (was 2.0)
        - ATR-based SL (was sweep-wick based)
        - Sessions extended to cover pre-London and bridge
    """

    # ─── Timeframes ────────────────────────────────────────────────
    H1_LOOKBACK  = 60     # H1 candles for trend + AOI detection
    M15_LOOKBACK = 30     # M15 candles for entry signal

    # ─── AOI settings ──────────────────────────────────────────────
    AOI_ZONE_BAND_PIPS = 15    # Zone clustering band (tighter for scalping)
    MIN_AOI_TOUCHES    = 2     # Reduced from 3 — faster signals

    # ─── Entry settings ────────────────────────────────────────────
    PRICE_AT_AOI_PIPS  = 8     # Price must be within 8 pips of AOI
    MIN_SWEEP_PIPS     = 0.5   # Minimum sweep to avoid noise
    MAX_SWEEP_PIPS     = 12.0  # Maximum sweep to avoid breakouts

    # ─── Exit settings ─────────────────────────────────────────────
    ATR_PERIOD   = 14
    ATR_SL_MULT  = 1.5    # SL = 1.5 × ATR on M15
    MIN_RR       = 1.5    # Reduced from 2.0 for scalping

    # ─── Session windows (UTC) ─────────────────────────────────────
    SESSIONS = [
        (time(6,  0), time(8,  0)),   # Pre-London drift
        (time(8,  0), time(11, 0)),   # London
        (time(11, 0), time(13, 0)),   # London-NY bridge
        (time(13, 0), time(16, 0)),   # New York
    ]

    def __init__(self, config: dict):
        self.config = config
        logging.info("✅ Flow — Multi-Session Scalper loaded")
        logging.info("   Sessions: Pre-London(06-08) London(08-11) Bridge(11-13) NY(13-16) UTC")
        logging.info(f"   Entry: M15 sweep+reclaim | H1 trend filter | ATR SL")
        logging.info(f"   RR: {self.MIN_RR} minimum | AOI: {self.MIN_AOI_TOUCHES}+ touches")

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC ENTRY POINT
    # ═══════════════════════════════════════════════════════════════

    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Main analysis — follows Brave strategy contract exactly.

        Live mode:    provided_rates = None → fetch from MT5
        Backtest:     provided_rates = {
                          mt5.TIMEFRAME_H1:  list[dict],
                          mt5.TIMEFRAME_M15: list[dict],
                      }
        """
        logging.info(f"   [{symbol}] Flow: Starting multi-session scalp analysis...")

        # ── 1. Session filter (live only) ────────────────────────
        if provided_rates is None and not self._is_active_session():
            logging.info(f"   [{symbol}] Flow: Outside session window — skipping")
            return None

        # ── 2. Fetch candles ─────────────────────────────────────
        h1_candles  = self._get_candles(symbol, mt5.TIMEFRAME_H1,  self.H1_LOOKBACK,  provided_rates)
        m15_candles = self._get_candles(symbol, mt5.TIMEFRAME_M15, self.M15_LOOKBACK, provided_rates)

        if not h1_candles or not m15_candles:
            logging.error(f"   [{symbol}] Flow: Insufficient H1/M15 data")
            return None

        # ── 3. Get pip point ─────────────────────────────────────
        point = self._get_point(symbol, h1_candles)

        # ── 4. H1 trend filter ───────────────────────────────────
        h1_trend = self._determine_trend(h1_candles)
        logging.info(f"   [{symbol}] Flow: H1 trend = {h1_trend}")

        if h1_trend == "RANGING":
            logging.info(f"   [{symbol}] Flow: H1 ranging — no scalp setup")
            return None

        # ── 5. Find AOI on H1 ────────────────────────────────────
        aoi = self._find_aoi(h1_candles, h1_trend, point)
        if aoi is None:
            logging.info(f"   [{symbol}] Flow: No AOI found on H1")
            return None

        logging.info(
            f"   [{symbol}] Flow: AOI at {aoi['level']:.5f} "
            f"({aoi['type']}, {aoi['touches']} touches)"
        )

        # ── 6. Check M15 price is near AOI ───────────────────────
        current_price = m15_candles[-1]["close"]
        distance_pips = abs(current_price - aoi["level"]) / point

        if distance_pips > self.PRICE_AT_AOI_PIPS:
            logging.info(
                f"   [{symbol}] Flow: Price {distance_pips:.1f} pips from AOI "
                f"(max {self.PRICE_AT_AOI_PIPS}) — too far"
            )
            return None

        logging.info(f"   [{symbol}] Flow: Price {distance_pips:.1f} pips from AOI ✅")

        # ── 7. M15 sweep + reclaim ───────────────────────────────
        sweep = self._detect_sweep(m15_candles[-10:], aoi, h1_trend, point)
        if sweep is None:
            logging.info(f"   [{symbol}] Flow: No M15 sweep+reclaim pattern")
            return None

        logging.info(
            f"   [{symbol}] Flow: ✅ Sweep+reclaim confirmed "
            f"({sweep['direction']}, {sweep['sweep_size_pips']:.1f} pips)"
        )

        # ── 8. ATR-based SL ──────────────────────────────────────
        atr_pips = self._calculate_atr(m15_candles, point)
        if atr_pips is None or atr_pips < 1.0:
            logging.info(f"   [{symbol}] Flow: ATR too low — dead market")
            return None

        # ── 9. Build signal ──────────────────────────────────────
        return self._build_signal(symbol, current_price, aoi, sweep, atr_pips, point, h1_trend)

    # ═══════════════════════════════════════════════════════════════
    # SIGNAL BUILDER
    # ═══════════════════════════════════════════════════════════════

    def _build_signal(
        self,
        symbol: str,
        current_price: float,
        aoi: dict,
        sweep: dict,
        atr_pips: float,
        point: float,
        trend: str,
    ) -> dict | None:
        """Build scalp signal with ATR-based SL and 1.5:1 TP."""

        direction = sweep["direction"]
        entry     = current_price
        sl_distance = atr_pips * self.ATR_SL_MULT * point

        if direction == "BUY":
            sl = entry - sl_distance
            tp = entry + (sl_distance * self.MIN_RR)
        else:
            sl = entry + sl_distance
            tp = entry - (sl_distance * self.MIN_RR)

        risk   = abs(entry - sl)
        reward = abs(tp - entry)

        if risk == 0:
            return None

        rr_ratio = reward / risk

        if rr_ratio < self.MIN_RR:
            logging.info(f"   [{symbol}] Flow: RR {rr_ratio:.2f} < {self.MIN_RR} — skipping")
            return None

        lot_size      = self.config.get("lot_size", 0.01)
        contract_size = 100_000
        expected_profit = reward * lot_size * contract_size
        expected_loss   = risk   * lot_size * contract_size

        logging.info(
            f"   [{symbol}] Flow: 🎯 {direction} scalp | "
            f"Entry={entry:.5f} SL={sl:.5f} TP={tp:.5f} "
            f"RR={rr_ratio:.2f} ATR={atr_pips:.1f}pips"
        )

        return {
            "symbol":            symbol,
            "direction":         direction,
            "strategy_name":     "Flow",
            "order_type":        "MARKET",
            "entry_price":       round(entry, 5),
            "suggested_sl":      round(sl,    5),
            "suggested_tp":      round(tp,    5),
            "risk_reward_ratio": round(rr_ratio, 2),
            "expected_profit":   round(expected_profit, 2),
            "expected_loss":     round(expected_loss,   2),
            "probability":       "HIGH" if aoi["touches"] >= 3 else "MEDIUM",
            "structure": {
                "h1_trend":      trend,
                "aoi_level":     round(aoi["level"], 5),
                "aoi_type":      aoi["type"],
                "aoi_touches":   aoi["touches"],
                "entry_type":    "SWEEP_RECLAIM_M15",
                "sweep_pips":    round(sweep["sweep_size_pips"], 1),
                "atr_pips":      round(atr_pips, 1),
                "session":       self._current_session(),
            },
        }

    # ═══════════════════════════════════════════════════════════════
    # INDICATORS
    # ═══════════════════════════════════════════════════════════════

    def _determine_trend(self, candles: list[dict]) -> str:
        """
        Fractal swing point trend detection on H1.
        UPTREND = Higher High + Higher Low
        DOWNTREND = Lower High + Lower Low
        RANGING = mixed
        """
        swing_highs: list[tuple[int, float]] = []
        swing_lows:  list[tuple[int, float]] = []

        if len(candles) < 5:
            return "RANGING"

        for i in range(2, len(candles) - 2):
            h = candles[i]["high"]
            l = candles[i]["low"]

            if (h > candles[i-1]["high"] and h > candles[i-2]["high"] and
                    h > candles[i+1]["high"] and h > candles[i+2]["high"]):
                swing_highs.append((i, h))

            if (l < candles[i-1]["low"] and l < candles[i-2]["low"] and
                    l < candles[i+1]["low"] and l < candles[i+2]["low"]):
                swing_lows.append((i, l))

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "RANGING"

        hh = swing_highs[-1][1] > swing_highs[-2][1]
        hl = swing_lows[-1][1]  > swing_lows[-2][1]
        lh = swing_highs[-1][1] < swing_highs[-2][1]
        ll = swing_lows[-1][1]  < swing_lows[-2][1]

        if hh and hl:
            return "UPTREND"
        elif lh and ll:
            return "DOWNTREND"
        return "RANGING"

    def _find_aoi(self, candles: list[dict], trend: str, point: float) -> dict | None:
        """
        Zone clustering AOI on H1.
        Support for uptrend, resistance for downtrend.
        Min 2 touches (reduced from 3 for scalping speed).
        """
        band_size = self.AOI_ZONE_BAND_PIPS * point

        levels   = [c["low"] for c in candles]  if trend == "UPTREND" else [c["high"] for c in candles]
        aoi_type = "SUPPORT" if trend == "UPTREND" else "RESISTANCE"

        if not levels:
            return None

        min_level = min(levels)
        bands: dict[int, list[float]] = {}

        for level in levels:
            key = int((level - min_level) / band_size)
            bands.setdefault(key, []).append(level)

        valid = [
            {"level": sum(v) / len(v), "touches": len(v)}
            for v in bands.values()
            if len(v) >= self.MIN_AOI_TOUCHES
        ]

        if not valid:
            return None

        valid.sort(key=lambda x: x["touches"], reverse=True)
        best = valid[0]
        return {"level": best["level"], "type": aoi_type, "touches": best["touches"]}

    def _detect_sweep(
        self,
        recent_m15: list[dict],
        aoi: dict,
        trend: str,
        point: float,
    ) -> dict | None:
        """
        Detect M15 sweep + reclaim of the H1 AOI.
        Same logic as original Flow but applied to M15 candles.
        """
        aoi_level = aoi["level"]

        for candle in recent_m15[-10:]:
            if trend == "UPTREND":
                swept    = candle["low"]   < aoi_level
                reclaimed = candle["close"] > aoi_level
                bullish   = candle["close"] > candle["open"]
                sweep_pips = (aoi_level - candle["low"]) / point

                if swept and reclaimed and bullish:
                    if self.MIN_SWEEP_PIPS <= sweep_pips <= self.MAX_SWEEP_PIPS:
                        return {
                            "type":            "SWEEP_RECLAIM",
                            "direction":       "BUY",
                            "sweep_low":       candle["low"],
                            "sweep_size_pips": sweep_pips,
                        }

            elif trend == "DOWNTREND":
                swept    = candle["high"]  > aoi_level
                reclaimed = candle["close"] < aoi_level
                bearish   = candle["close"] < candle["open"]
                sweep_pips = (candle["high"] - aoi_level) / point

                if swept and reclaimed and bearish:
                    if self.MIN_SWEEP_PIPS <= sweep_pips <= self.MAX_SWEEP_PIPS:
                        return {
                            "type":             "SWEEP_RECLAIM",
                            "direction":        "SELL",
                            "sweep_high":       candle["high"],
                            "sweep_size_pips":  sweep_pips,
                        }

        return None

    def _calculate_atr(self, candles: list[dict], point: float) -> float | None:
        """ATR in pips over ATR_PERIOD M15 candles."""
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

    # ═══════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════

    def _is_active_session(self) -> bool:
        now = datetime.utcnow().time()
        return any(start <= now < end for start, end in self.SESSIONS)

    def _current_session(self) -> str:
        now = datetime.utcnow().time()
        labels = ["PRE_LONDON", "LONDON", "BRIDGE", "NEW_YORK"]
        for (start, end), label in zip(self.SESSIONS, labels):
            if start <= now < end:
                return label
        return "OTHER"

    def _get_candles(
        self,
        symbol: str,
        timeframe: int,
        count: int,
        provided_rates: dict | None,
    ) -> list[dict] | None:
        if provided_rates is not None:
            candles = provided_rates.get(timeframe)
            if candles is None or len(candles) < count:
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
        try:
            info = mt5.symbol_info(symbol)
            if info:
                p = info.point
                return p * 10 if info.digits in (5, 3) else p
        except Exception:
            pass
        diff = candles[-1]["high"] - candles[-1]["low"]
        if diff == 0:
            return 0.00001
        s = f"{diff:f}"
        if "." in s:
            decimals = len(s.split(".")[-1].rstrip("0"))
            if decimals > 0:
                return 10 ** (-decimals)
        return 0.00001
