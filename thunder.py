import MetaTrader5 as mt5
import numpy as np
import logging
from datetime import datetime, time


class thunder:
    """
    Thunder Strategy — Supply & Demand Zone Trading Engine
    --------------------------------------------------------
    v2.0 — Top-Down Multi-Timeframe Analysis

    Execution flow:
        analyze()
            ├── _fetch_candles()            # Raw data for H4 / M30 / M15
            ├── _determine_structure()      # Trend label per timeframe
            ├── _collation_check()          # H4 + M30 alignment gate
            └── (if aligned)
                ├── find_zones()            # SD zones on M15
                ├── apply_liquidity_filter()# Validate zone quality
                └── generate_signal()       # Build the signal dict
    """

    # ─── constants ────────────────────────────────────────────────
    CANDLE_COUNT = 200                          # candles fetched per timeframe
    STRUCTURE_LOOKBACK = 50                     # candles used for trend determination
    ZONE_LOOKBACK = 100                         # candles used for zone detection
    MIN_ZONE_CANDLES = 3                        # minimum candles to form a zone
    ZONE_RETEST_THRESHOLD = 0.618               # Fibonacci retest depth (0–1)

    # Timeframe constants (used as keys internally)
    TF_H4  = mt5.TIMEFRAME_H4
    TF_M30 = mt5.TIMEFRAME_M30
    TF_M15 = mt5.TIMEFRAME_M15

    # Human-readable labels mapped to MT5 constants
    TF_LABELS = {
        mt5.TIMEFRAME_H4:  "H4",
        mt5.TIMEFRAME_M30: "M30",
        mt5.TIMEFRAME_M15: "M15",
    }

    def __init__(self, config: dict):
        """
        Parameters
        ----------
        config : dict
            Must contain at minimum:
                symbols        – list of instrument strings
                lot_size       – float, trade volume
                stop_loss_pips – int
                take_profit_pips – int
            Optional:
                structure_method – "swing" (default) | "ema_cross"
        """
        self.config = config
        self.structure_method = config.get("structure_method", "swing")

        # ── per-symbol cache: avoids redundant MT5 calls within the
        #    same 20-second bot loop iteration.
        #    Structure: { symbol: { "H4": {...}, "M30": {...}, "M15": {...} } }
        self._candle_cache: dict = {}
        self._cache_timestamp: dict = {}        # symbol → datetime of last fetch

        logging.info(" Thunder v2.0 — Multi-Timeframe Engine loaded")
        logging.info(f"   Structure method : {self.structure_method}")
        logging.info(f"   Candle count     : {self.CANDLE_COUNT}")
        logging.info(f"   Structure lookback: {self.STRUCTURE_LOOKBACK}")

    # ══════════════════════════════════════════════════════════════════
    # TRADING SESSION WINDOWS (UTC)
    # ══════════════════════════════════════════════════════════════════
    LONDON_OPEN = time(8, 0)   # 08:00 UTC
    LONDON_CLOSE = time(11, 0)  # 11:00 UTC
    NY_OPEN = time(13, 0)       # 13:00 UTC (08:00 EST)
    NY_CLOSE = time(16, 0)      # 16:00 UTC (11:00 EST)

    # ══════════════════════════════════════════════════════════════════
#                           THUNDER v4.0
# ══════════════════════════════════════════════════════════════════
# CHANGES (from v3.0):
#   - Corrective Arrival Filter: Skip zones approached with high momentum
#   - Extreme Zone Priority: Distinguish TYPE_LIMIT vs TYPE_CONFIRMATION
#   - Enhanced zone metadata for swing trailing (backtester implementation)
# 
# v3.0 Features (retained):
#   - Session filter: London (08:00-11:00) + NY (13:00-16:00) UTC only
#   - ATR-based volatility filter: Min 5 pips to avoid ranging markets
#   - Dynamic SL buffer: ATR * 0.5 instead of fixed 5 pips
# ══════════════════════════════════════════════════════════════════
    # ADAPTIVE RISK MANAGEMENT
    # ══════════════════════════════════════════════════════════════════
    BREAKEVEN_TRIGGER = 1.5     # Move SL to BE when profit reaches 1.5R (v3.0)
    
    # ══════════════════════════════════════════════════════════════════
    # VOLATILITY SETTINGS
    # ══════════════════════════════════════════════════════════════════
    MIN_ATR_PIPS = 5.0          # Minimum volatility to trade (avoid ranging)
    ATR_PERIOD = 14             # ATR calculation period
    ATR_SL_MULTIPLIER = 0.5     # SL buffer = ATR * 0.5
    
    # ══════════════════════════════════════════════════════════════════
    # CONTEXT FILTERS (v4.0 - JeaFx Methodology)
    # ══════════════════════════════════════════════════════════════════
    CORRECTIVE_ARRIVAL_MULTIPLIER = 1.2  # Max avg body: ATR × 1.2
    CORRECTIVE_ARRIVAL_CANDLES = 3       # Check last 3 approach candles
    CONFIRMATION_ZONE_MIN_RR = 3.0       # Min RR for TYPE_CONFIRMATION zones

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC ENTRY POINT
    # ═══════════════════════════════════════════════════════════════

    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Top-level orchestrator — v3.0 ENHANCED

        1. SESSION FILTER: Only trade during London/NY sessions
        2. VOLATILITY FILTER: Skip if ATR < 5 pips
        3. Multi-timeframe structure check
        4. Zone detection & filtering
        5. Signal generation with adaptive stops

        Returns
        -------
        dict | None
            Signal dictionary if all conditions met, else None.
        """
        # ── SESSION FILTER (Live Mode only) ──────────────────────
        # In backtest mode (provided_rates != None), skip session check
        if provided_rates is None and not self._is_active_session():
            logging.info(f"   [{symbol}] ⏰ Outside trading hours (London/NY only)")
            return None

        # ── Fetch candles ─────────────────────────────────────────
        logging.info(f"   [{symbol}] Starting Top-Down analysis...")
        candles_h4  = self._get_candles(symbol, mt5.TIMEFRAME_H4,  50, provided_rates)  # v4.0: Reduced to 50 for backtest
        candles_m30 = self._get_candles(symbol, mt5.TIMEFRAME_M30, 50, provided_rates)  # v4.0: Reduced to 50 for backtest
        candles_m15 = self._get_candles(symbol, mt5.TIMEFRAME_M15, 200, provided_rates)

        if not candles_h4 or not candles_m30 or not candles_m15:
            logging.error(f"   [{symbol}] Failed to retrieve complete candle data")
            return None

        # ── VOLATILITY FILTER (ATR Check) ────────────────────────
        atr_pips = self._calculate_atr_pips(symbol, candles_m15)
        if atr_pips < self.MIN_ATR_PIPS:
            logging.info(f"   [{symbol}] 📉 Low volatility: ATR {atr_pips:.1f} pips < {self.MIN_ATR_PIPS}")
            return None
        
        logging.info(f"   [{symbol}] ✅ ATR: {atr_pips:.1f} pips (active volatility)")

        # ── Market Structure ──────────────────────────────────────
        h4_structure  = self._determine_structure(candles_h4,  "H4")
        m30_structure = self._determine_structure(candles_m30, "M30")

        logging.info(f"   [{symbol}] H4  structure : {h4_structure}")
        logging.info(f"   [{symbol}] M30 structure : {m30_structure}")

        if h4_structure is None:
            logging.info(f"   [{symbol}]  H4 structure indeterminate — no trade")
            return None
        if m30_structure is None:
            logging.info(f"   [{symbol}]  M30 structure indeterminate — no trade")
            return None
        if h4_structure != m30_structure:
            logging.info(f"   [{symbol}]  Structure Mismatch — H4: {h4_structure} vs M30: {m30_structure} — no trade")
            return None

        logging.info(f"   [{symbol}]  Collation passed — scanning for {'DEMAND (BUY)' if h4_structure == 'UPTREND' else 'SUPPLY (SELL)'} on M15")

        # ── Detect + Filter Zones ─────────────────────────────────
        direction = "BUY" if h4_structure == "UPTREND" else "SELL"
        zones = self.find_zones(candles_m15, symbol, direction)

        logging.info(f"   [{symbol}] Found {len(zones)} {direction} zone(s) on M15")
        if not zones:
            return None

        zones_filtered = self.apply_liquidity_filter(zones, candles_m15, symbol)
        logging.info(f"   [{symbol}] Liquidity filter: {len(zones)} → {len(zones_filtered)} zone(s)")
        if not zones_filtered:
            logging.info(f"   [{symbol}] All zones eliminated by liquidity filter")
            return None
        
        # ── v4.0: Corrective Arrival Filter ───────────────────────
        zones_corrective = self._filter_corrective_arrival(
            zones_filtered, candles_m15, atr_pips, point=self._get_point_from_rates(candles_m15)
        )
        logging.info(f"   [{symbol}] Corrective arrival filter: {len(zones_filtered)} → {len(zones_corrective)} zone(s)")
        if not zones_corrective:
            logging.info(f"   [{symbol}] All zones eliminated by arrival filter (impulse entries)")
            return None

        # ── Generate Signal (with ATR-based dynamic stops) ───────
        # Pass ATR to signal generation for dynamic SL buffer
        signal = self.generate_signal(
            symbol=symbol,
            zones=zones_corrective,  # v4.0: Use corrective-filtered zones
            candles=candles_m15,
            direction=direction,
            atr_pips=atr_pips,
            point=None if provided_rates is None else self._get_point_from_rates(candles_m15)
        )

        if signal is None:
            return None

        # ── Inject MTF context ────────────────────────────────────
        signal["h4_structure"]  = h4_structure
        signal["m30_structure"] = m30_structure

        prob = signal.get("probability", "UNKNOWN")
        logging.info(f"   [{symbol}]  Signal generated — {direction} | P: {prob} | H4:{h4_structure} M30:{m30_structure}")
        return signal

    # ═══════════════════════════════════════════════════════════════
    # HELPER METHODS (V3.0)
    # ═══════════════════════════════════════════════════════════════

    def _is_active_session(self) -> bool:
        """
        Checks if the current UTC time falls within the London or New York
        trading sessions.
        """
        now_utc = datetime.utcnow().time()
        
        london_session = (self.LONDON_OPEN <= now_utc < self.LONDON_CLOSE)
        ny_session = (self.NY_OPEN <= now_utc < self.NY_CLOSE)
        
        return london_session or ny_session

    def _get_candles(self, symbol: str, timeframe: int, count: int,
                     provided_rates: dict | None) -> list[dict] | None:
        """
        Fetches candles for a specific timeframe, either from provided_rates
        (backtest mode) or MT5 (live mode).
        """
        if provided_rates is not None:
            # Backtest mode: extract from provided_rates
            candles = provided_rates.get(timeframe)
            if candles is None or len(candles) < count:
                logging.error(f"   [{symbol}]  [{self.TF_LABELS[timeframe]}] Insufficient data in provided_rates: "
                              f"got {0 if candles is None else len(candles)}, need {count}")
                return None
            return candles[-count:] # Ensure we return the requested count from the end
        else:
            # Live mode: fetch from MT5
            label = self.TF_LABELS[timeframe]
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)

            if rates is None or len(rates) < count:
                logging.error(
                    f"   [{symbol}]  [{label}] Insufficient data from MT5: "
                    f"got {0 if rates is None else len(rates)}, need {count}"
                )
                return None

            # Convert numpy structured array → plain list of dicts
            result = [
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
            logging.debug(f"   [{symbol}] [{label}] fetched {len(result)} candles")
            return result

    def _calculate_atr_pips(self, symbol: str, candles: list[dict]) -> float:
        """
        Calculates the Average True Range (ATR) in pips for the given candles.
        """
        if len(candles) < self.ATR_PERIOD:
            logging.warning(f"   [{symbol}] Not enough candles ({len(candles)}) for ATR calculation (need {self.ATR_PERIOD})")
            return 0.0

        highs = np.array([c["high"] for c in candles])
        lows = np.array([c["low"] for c in candles])
        closes = np.array([c["close"] for c in candles])

        # True Range (TR)
        tr = np.maximum(highs[1:] - lows[1:],
                        np.abs(highs[1:] - closes[:-1]),
                        np.abs(lows[1:] - closes[:-1]))

        # ATR (SMA-like calculation for simplicity, can be EMA)
        atr = np.mean(tr[-self.ATR_PERIOD:])

        # Convert ATR to pips
        point = self._get_point_from_rates(candles)
        atr_pips = atr / point
        return atr_pips

    def _get_point_from_rates(self, candles: list[dict]) -> float:
        """
        In backtest mode, MT5's symbol_info is not available.
        We can infer the 'point' value from the candle data itself.
        For most FX pairs, it's 0.00001. For JPY pairs, 0.001.
        """
        if not candles:
            return 0.00001 # Default to common FX point

        # Take the difference between high and low of a candle
        # and determine the smallest significant digit.
        # This is a heuristic and might not be perfect for all instruments.
        diff = candles[-1]["high"] - candles[-1]["low"]
        if diff == 0: # Avoid division by zero if candle is flat
            return 0.00001

        # Find the number of decimal places
        s_diff = f"{diff:f}" # Convert to string to avoid scientific notation
        if '.' in s_diff:
            decimal_places = len(s_diff.split('.')[-1].rstrip('0'))
            if decimal_places > 0:
                return 10**(-decimal_places)
        return 0.00001 # Default if no decimals or other issues

    # ═══════════════════════════════════════════════════════════════
    # STEP 1 — DATA LAYER
    # ═══════════════════════════════════════════════════════════════

    def _fetch_all_timeframes(self, symbol: str) -> dict | None:
        """
        Fetch 200 candles for H4, M30, and M15.

        Uses a lightweight cache so that multiple calls within the same
        bot loop tick (≈20 s) don't hammer the MT5 server.  Cache is
        invalidated after 15 seconds (well under the bot's loop interval).

        Returns
        -------
        dict | None
            { TF_H4: [...], TF_M30: [...], TF_M15: [...] }
            or None if ANY timeframe fails.
        """
        now = datetime.now()
        cache_ttl_seconds = 15

        # Check if we have a fresh cache for this symbol
        if (symbol in self._cache_timestamp and
                (now - self._cache_timestamp[symbol]).total_seconds() < cache_ttl_seconds):
            logging.debug(f"   [{symbol}] Using cached candle data")
            return self._candle_cache.get(symbol)

        logging.info(f"   [{symbol}] Fetching candles: H4, M30, M15...")

        result = {}
        for tf in (self.TF_H4, self.TF_M30, self.TF_M15):
            label = self.TF_LABELS[tf]
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, self.CANDLE_COUNT)

            if rates is None or len(rates) < self.STRUCTURE_LOOKBACK:
                logging.error(
                    f"   [{symbol}]  [{label}] Insufficient data: "
                    f"got {0 if rates is None else len(rates)}, "
                    f"need {self.STRUCTURE_LOOKBACK}"
                )
                return None

            # Convert numpy structured array → plain list of dicts
            result[tf] = [
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
            logging.debug(f"   [{symbol}] [{label}] fetched {len(result[tf])} candles")

        # Store in cache
        self._candle_cache[symbol] = result
        self._cache_timestamp[symbol] = now
        return result

    # ═══════════════════════════════════════════════════════════════
    # STEP 2 — STRUCTURE DETERMINATION
    # ═══════════════════════════════════════════════════════════════

    def _determine_structure(self, candles: list[dict], label: str) -> str | None:
        """
        Classify market structure as UPTREND or DOWNTREND.

        Two methods are available, selected via config["structure_method"]:

            "swing"    (default)
                Uses the classic Higher-Highs / Higher-Lows definition.
                Compares the most recent swing pivot against the previous one.
                Robust and purely price-based.

            "ema_cross"
                Price position relative to a 20/50 EMA cross.
                Faster to react but can produce false signals in chop.

        Returns
        -------
        str | None
            "UPTREND" | "DOWNTREND" | None (indeterminate / insufficient data)
        """
        if self.structure_method == "ema_cross":
            return self._structure_ema_cross(candles, label)
        return self._structure_swing(candles, label)

    # ── Swing-based structure ─────────────────────────────────────

    def _structure_swing(self, candles: list[dict], label: str) -> str | None:
        """
        Identify structure via swing high / swing low sequences.

        A swing high is a candle whose high is >= both its neighbours.
        A swing low  is a candle whose low  is <= both its neighbours.

        We collect the last N pivots and compare the two most recent of
        each type.  If the latest swing-high is higher AND the latest
        swing-low is higher than their predecessors → UPTREND (and vice
        versa).  Mixed signals → None.

        Uses STRUCTURE_LOOKBACK candles to keep the window manageable.
        """
        window = candles[-self.STRUCTURE_LOOKBACK:]

        swing_highs: list[float] = []   # values only — we just need ordering
        swing_lows:  list[float] = []

        # Walk candles 1 … n-1 (need a neighbour on each side)
        for i in range(1, len(window) - 1):
            high = window[i]["high"]
            low  = window[i]["low"]

            if high >= window[i - 1]["high"] and high >= window[i + 1]["high"]:
                swing_highs.append(high)

            if low <= window[i - 1]["low"] and low <= window[i + 1]["low"]:
                swing_lows.append(low)

        # Need at least 2 of each to compare
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            logging.warning(f"   [{label}] Not enough swing pivots "
                            f"(H:{len(swing_highs)} L:{len(swing_lows)}) — structure indeterminate")
            return None

        higher_highs = swing_highs[-1] > swing_highs[-2]
        higher_lows  = swing_lows[-1]  > swing_lows[-2]
        lower_highs  = swing_highs[-1] < swing_highs[-2]
        lower_lows   = swing_lows[-1]  < swing_lows[-2]

        if higher_highs and higher_lows:
            return "UPTREND"
        if lower_highs and lower_lows:
            return "DOWNTREND"

        # Ambiguous (e.g. higher highs but lower lows → range-bound)
        logging.info(f"   [{label}] Swing structure ambiguous "
                     f"(HH:{higher_highs} HL:{higher_lows} LH:{lower_highs} LL:{lower_lows})")
        return None

    # ── EMA-cross-based structure ─────────────────────────────────

    def _structure_ema_cross(self, candles: list[dict], label: str) -> str | None:
        """
        Classify structure by the relative position of price to a
        20-period and 50-period Exponential Moving Average.

            Price > EMA20 > EMA50  →  UPTREND
            Price < EMA20 < EMA50  →  DOWNTREND
            Otherwise              →  None (indeterminate)
        """
        closes = np.array([c["close"] for c in candles], dtype=np.float64)

        if len(closes) < 50:
            logging.warning(f"   [{label}] Not enough candles for EMA50")
            return None

        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)

        price  = closes[-1]
        ema20_val = ema20[-1]
        ema50_val = ema50[-1]

        if price > ema20_val > ema50_val:
            return "UPTREND"
        if price < ema20_val < ema50_val:
            return "DOWNTREND"

        logging.info(f"   [{label}] EMA structure indeterminate "
                     f"(price={price:.5f} ema20={ema20_val:.5f} ema50={ema50_val:.5f})")
        return None

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Standard Exponential Moving Average over a 1-D array."""
        k = 2.0 / (period + 1)
        ema = np.empty_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = data[i] * k + ema[i - 1] * (1 - k)
        return ema

    # ═══════════════════════════════════════════════════════════════
    # STEP 3 — COLLATION CHECK (ALIGNMENT GATE)
    # ═══════════════════════════════════════════════════════════════

    def _collation_check(self, symbol: str,
                         h4_structure: str | None,
                         m30_structure: str | None) -> str | None:
        """
        The hard gate that enforces Top-Down alignment.

        Truth table
        ───────────────────────────────────────────────────────
        H4          M30          Result
        ───────────────────────────────────────────────────────
        UPTREND     UPTREND      → "BUY"   (scan DEMAND on M15)
        DOWNTREND   DOWNTREND    → "SELL"  (scan SUPPLY on M15)
        UPTREND     DOWNTREND    → None    (Structure Mismatch)
        DOWNTREND   UPTREND      → None    (Structure Mismatch)
        None        *            → None    (H4 indeterminate)
        *           None         → None    (M30 indeterminate)
        ───────────────────────────────────────────────────────

        Returns
        -------
        str | None
            "BUY" | "SELL" | None
        """
        # Either timeframe indeterminate → no trade
        if h4_structure is None:
            logging.info(f"   [{symbol}]  H4 structure indeterminate — no trade")
            return None
        if m30_structure is None:
            logging.info(f"   [{symbol}]  M30 structure indeterminate — no trade")
            return None

        # Both agree
        if h4_structure == "UPTREND" and m30_structure == "UPTREND":
            return "BUY"
        if h4_structure == "DOWNTREND" and m30_structure == "DOWNTREND":
            return "SELL"

        # Disagreement → Structure Mismatch
        logging.info(
            f"   [{symbol}]  Structure Mismatch — "
            f"H4: {h4_structure} vs M30: {m30_structure} — no trade"
        )
        return None

    # ═══════════════════════════════════════════════════════════════
    # STEP 4 — M15 EXECUTION (ZONE LOGIC)
    # ═══════════════════════════════════════════════════════════════

    def find_zones(self, candles: list[dict], symbol: str,
                   direction: str) -> list[dict]:
        """
        Identify Supply (SELL) or Demand (BUY) zones on M15.

        Only scans in the direction permitted by the collation check,
        preventing counter-trend zone detection entirely.

        A DEMAND zone is formed by a sharp bullish move (base) preceded
        by consolidation.  A SUPPLY zone is the mirror for bearish moves.

        Each zone dict contains:
            zone_type   – "DEMAND" | "SUPPLY"
            high        – zone upper boundary
            low         – zone lower boundary
            base_start  – index of zone base start candle
            base_end    – index of zone base end candle
            strength    – number of base candles (more = stronger)
        """
        window = candles[-self.ZONE_LOOKBACK:]
        zones: list[dict] = []

        for i in range(self.MIN_ZONE_CANDLES, len(window) - 1):
            if direction == "BUY":
                zone = self._detect_demand_zone(window, i)
            else:
                zone = self._detect_supply_zone(window, i)

            if zone is not None:
                zones.append(zone)

        logging.info(f"   [{symbol}] Found {len(zones)} {direction} zone(s) on M15")
        return zones

    def _detect_demand_zone(self, window: list[dict], idx: int) -> dict | None:
        """
        Classic Demand zone: a consolidation base followed by a strong
        bullish impulse candle.

        Criteria:
            1. Current candle is bullish (close > open).
            2. The body of the current candle is at least 1.5× the average
               body size of the preceding base candles.
            3. At least MIN_ZONE_CANDLES of consolidation exist before it.
        """
        candle = window[idx]
        # Must be bullish impulse
        if candle["close"] <= candle["open"]:
            return None

        impulse_body = candle["close"] - candle["open"]

        # Walk backwards to find the consolidation base
        base_start = idx - 1
        base_highs = []
        base_lows  = []

        while base_start >= 0:
            c = window[base_start]
            body = abs(c["close"] - c["open"])
            # Base candle: relatively small body (less than half the impulse)
            if body < impulse_body * 0.5:
                base_highs.append(c["high"])
                base_lows.append(c["low"])
                base_start -= 1
            else:
                break  # hit a non-base candle

        base_start += 1  # step back to the first base candle
        strength  = idx - base_start

        if strength < self.MIN_ZONE_CANDLES:
            return None

        # Verify impulse body vs average base body
        avg_base_body = np.mean([
            abs(window[j]["close"] - window[j]["open"])
            for j in range(base_start, idx)
        ])
        if avg_base_body == 0 or impulse_body < 1.5 * avg_base_body:
            return None

        return {
            "zone_type":  "DEMAND",
            "high":       max(base_highs) if base_highs else candle["open"],
            "low":        min(base_lows)  if base_lows  else candle["open"],
            "base_start": base_start,
            "base_end":   idx - 1,
            "strength":   strength,
        }

    def _detect_supply_zone(self, window: list[dict], idx: int) -> dict | None:
        """
        Classic Supply zone: consolidation base followed by a strong
        bearish impulse.  Mirror logic of _detect_demand_zone.
        """
        candle = window[idx]
        # Must be bearish impulse
        if candle["close"] >= candle["open"]:
            return None

        impulse_body = candle["open"] - candle["close"]

        base_start = idx - 1
        base_highs = []
        base_lows  = []

        while base_start >= 0:
            c = window[base_start]
            body = abs(c["close"] - c["open"])
            if body < impulse_body * 0.5:
                base_highs.append(c["high"])
                base_lows.append(c["low"])
                base_start -= 1
            else:
                break

        base_start += 1
        strength  = idx - base_start

        if strength < self.MIN_ZONE_CANDLES:
            return None

        avg_base_body = np.mean([
            abs(window[j]["close"] - window[j]["open"])
            for j in range(base_start, idx)
        ])
        if avg_base_body == 0 or impulse_body < 1.5 * avg_base_body:
            return None

        return {
            "zone_type":  "SUPPLY",
            "high":       max(base_highs) if base_highs else candle["open"],
            "low":        min(base_lows)  if base_lows  else candle["open"],
            "base_start": base_start,
            "base_end":   idx - 1,
            "strength":   strength,
        }

    # ─── Liquidity filter ─────────────────────────────────────────

    def apply_liquidity_filter(self, zones: list[dict],
                               candles: list[dict],
                               symbol: str) -> list[dict]:
        """
        Eliminate low-quality zones.

        A zone passes if:
            1. Price has retested the zone (current price within the zone
               or within ZONE_RETEST_THRESHOLD of its range).
            2. The zone has not been fully consumed (price did not close
               decisively beyond the zone on a previous candle).

        The strongest zone (most base candles) is ranked first.
        """
        if not zones:
            return []

        current_price = candles[-1]["close"]
        filtered: list[dict] = []

        for zone in zones:
            zone_range = zone["high"] - zone["low"]
            if zone_range == 0:
                continue  # degenerate zone

            if zone["zone_type"] == "DEMAND":
                # Price must be near or inside the demand zone
                retest_level = zone["high"] + zone_range * self.ZONE_RETEST_THRESHOLD
                if current_price <= retest_level:
                    filtered.append(zone)
            else:  # SUPPLY
                retest_level = zone["low"] - zone_range * self.ZONE_RETEST_THRESHOLD
                if current_price >= retest_level:
                    filtered.append(zone)

        # Sort by strength (descending) so the strongest zone is first
        filtered.sort(key=lambda z: z["strength"], reverse=True)

        logging.info(f"   [{symbol}] Liquidity filter: {len(zones)} → {len(filtered)} zone(s)")
        return filtered


    # ─── v4.0 Context Filters (JeaFx Methodology) ─────────────────

    def _filter_corrective_arrival(self, zones: list[dict], candles: list[dict],
                                     atr_pips: float, point: float) -> list[dict]:
        """
        v4.0: Corrective Arrival Filter (Momentum Check)
        
        Purpose: Skip zones where price arrived with high momentum (impulse).
        We only want to enter on "corrective" arrivals (small candles/drift).
        
        Logic:
            1. Look at the last 3 candles approaching the zone
            2. Calculate average body size
            3. If Avg Body > ATR ×  1.2, SKIP (impulse, not corrective)
        
        Reasoning:
            Catching a falling knife (impulse into zone) leads to poor fills
            and immediate reversals. JeaFx methodology requires corrective arrivals.
        """
        if not zones:
            return []
        
        filtered: list[dict] = []
        atr_price = atr_pips * point  # Convert ATR from pips to price
        threshold = atr_price * self.CORRECTIVE_ARRIVAL_MULTIPLIER
        
        for zone in zones:
            # Get the last N candles (approach candles)
            approach_candles = candles[-(self.CORRECTIVE_ARRIVAL_CANDLES):]
            
            # Calculate average body size
            avg_body = np.mean([
                abs(c["close"] - c["open"]) for c in approach_candles
            ])
            
            # Check if arrival is corrective (small candles)
            if avg_body <= threshold:
                filtered.append(zone)
                # logging.info(f\"      Zone OK: Corrective arrival ({avg_body/point:.1f} pips avg body < {threshold/point:.1f} pips)\")
            # else:
                # logging.info(f\"      Zone SKIP: Impulse arrival ({avg_body/point:.1f} pips avg body > {threshold/point:.1f} pips)\")
        
        return filtered

    def _classify_zone_type(self, zone: dict, candles: list[dict]) -> str:
        """
        v4.0: Classify zone as TYPE_LIMIT (Extreme) or TYPE_CONFIRMATION (Internal)
        
        Logic:
            - TYPE_LIMIT: Zone is at/near a recent swing high/low (extreme)
            - TYPE_CONFIRMATION: Zone is internal to recent price action
        
        Method:
            Look at the last 50 candles. If zone touches the absolute high/low,
            it's an extreme zone. Otherwise, it's internal.
        
        Reasoning:
            JeaFx states limit orders should only be placed at extreme zones.
            Internal zones require manual confirmation (not suitable for automated entry).
        """
        lookback = candles[-50:] if len(candles) >= 50 else candles
        
        recent_high = max(c["high"] for c in lookback)
        recent_low = min(c["low"] for c in lookback)
        
        # Check if zone is near extremes (within 10 pips tolerance)
        tolerance = 10 * self._get_point_from_rates(candles)
        
        if zone["zone_type"] == "SUPPLY":
            # Supply zone should be near recent highs
            if abs(zone["high"] - recent_high) <= tolerance:
                return "TYPE_LIMIT"  # Extreme zone
        else:  # DEMAND
            # Demand zone should be near recent lows
            if abs(zone["low"] - recent_low) <= tolerance:
                return "TYPE_LIMIT"  # Extreme zone
        
        return "TYPE_CONFIRMATION"  # Internal zone

    # ─── Signal generation ────────────────────────────────────────


    def generate_signal(self, symbol: str, zones: list[dict],
                        candles: list[dict],
                        direction: str,
                        atr_pips: float = None,
                        point: float | None = None) -> dict | None:
        """
        Build the final signal dictionary from the best zone.

        v3.0 ENHANCEMENTS:
        - Entry at zone MIDPOINT (50% retracement)
        - Dynamic SL buffer based on ATR (ATR * 0.5) instead of fixed 5 pips
        - TP calculated to enforce MINIMUM 2:1 RR
        - Break-even trigger at 1.5R (added to signal metadata)
        - Signals with RR < 2.0 are REJECTED
        - Zone width validation (min 3 pips)
        - HIGH probability tier DISABLED

        Parameters
        ----------
        atr_pips : float | None
            ATR in pips for dynamic SL buffer. If None, fallback to 5 pips.
        point : float | None
            Pip size. If None (Live Mode) it is fetched via mt5.symbol_info.
        """
        if not zones:
            return None

        best_zone = zones[0]
        current_price = candles[-1]["close"]

        # ── Resolve pip size ──────────────────────────────────────
        if point is None:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                logging.error(f"   [{symbol}]  symbol_info returned None")
                return None
            point = symbol_info.point

        # ── ZONE WIDTH SAFETY CHECK ───────────────────────────────
        zone_width_pips = (best_zone["high"] - best_zone["low"]) / point
        MIN_ZONE_WIDTH_PIPS = 3.0
        
        if zone_width_pips < MIN_ZONE_WIDTH_PIPS:
            logging.info(f"   [{symbol}]  Zone too narrow: {zone_width_pips:.1f} pips < {MIN_ZONE_WIDTH_PIPS}")
            return None

        # ── DYNAMIC SL BUFFER (ATR-based) ─────────────────────────
        if atr_pips is not None:
            sl_buffer = point * (atr_pips * self.ATR_SL_MULTIPLIER)
            logging.info(f"   [{symbol}]  Dynamic SL buffer: {atr_pips * self.ATR_SL_MULTIPLIER:.1f} pips (ATR-based)")
        else:
            sl_buffer = point * 5  # Fallback

        # ── ENTRY STRATEGY: Zone Midpoint ─────────────────────────
        zone_mid = (best_zone["high"] + best_zone["low"]) / 2.0
        
        if direction == "BUY":
            entry = zone_mid
            sl = best_zone["low"] - sl_buffer
            
            risk = entry - sl
            min_reward = risk * 2.0
            tp = entry + min_reward

            order_type = "LIMIT"
            if current_price <= best_zone["high"]:
                order_type = "MARKET"
                entry = current_price
                risk = entry - sl
                min_reward = risk * 2.0
                tp = entry + min_reward

        else:  # SELL
            entry = zone_mid
            sl = best_zone["high"] + sl_buffer
            
            risk = sl - entry
            min_reward = risk * 2.0
            tp = entry - min_reward

            order_type = "LIMIT"
            if current_price >= best_zone["low"]:
                order_type = "MARKET"
                entry = current_price
                risk = sl - entry
                min_reward = risk * 2.0
                tp = entry - min_reward

        # ── CALCULATE ACTUAL RR ───────────────────────────────────
        reward = abs(tp - entry)
        risk_actual = abs(entry - sl)
        rr_ratio = reward / risk_actual if risk_actual > 0 else 0

        # ── HARD FILTER: Reject signals with RR < 2.0 ────────────
        MIN_RR = 2.0
        if rr_ratio < MIN_RR:
            logging.info(f"   [{symbol}]  Signal REJECTED: RR {rr_ratio:.2f} < {MIN_RR}")
            return None

        # ── BREAK-EVEN TRIGGER PRICE ──────────────────────────────
        # Calculate price level where SL should move to entry (BE)
        be_trigger_distance = risk_actual * self.BREAKEVEN_TRIGGER
        if direction == "BUY":
            be_trigger_price = entry + be_trigger_distance
        else:
            be_trigger_price = entry - be_trigger_distance

        # ── CALCULATE EXPECTED P&L ────────────────────────────────
        lot_size = self.config.get("lot_size", 0.01)
        contract_size = 100_000
        expected_profit = reward * lot_size * contract_size
        expected_loss = risk_actual * lot_size * contract_size

        # ── PROBABILITY HEURISTIC ─────────────────────────────────
        probability = self._calculate_probability(best_zone, current_price)
        
        # ── v4.0: ZONE CLASSIFICATION & RR CHECK ──────────────────
        zone_classification = self._classify_zone_type(best_zone, candles)
        
        # JeaFx Rule: TYPE_CONFIRMATION zones require RR > 3:1 (or skip)
        if zone_classification == "TYPE_CONFIRMATION" and rr_ratio < self.CONFIRMATION_ZONE_MIN_RR:
            logging.info(f"   [{symbol}]  Internal zone with insufficient RR: {rr_ratio:.2f} < {self.CONFIRMATION_ZONE_MIN_RR}")
            return None


        return {
            "direction":    direction,
            "probability":  probability,
            "zone_type":    best_zone["zone_type"],
            "order_type":   order_type,
            "entry_price":  round(entry, 5),
            "suggested_sl": round(sl, 5),
            "suggested_tp": round(tp, 5),

            # Risk/Reward metrics
            "risk_reward_ratio": round(rr_ratio, 2),
            "expected_profit":   round(expected_profit, 2),
            "expected_loss":     round(expected_loss, 2),

            # NEW: Break-even trigger
            "be_trigger_price": round(be_trigger_price, 5),
            "be_trigger_r": self.BREAKEVEN_TRIGGER,

            "structure": {
                "zone_strength":  best_zone["strength"],
                "zone_high":      round(best_zone["high"], 5),
                "zone_low":       round(best_zone["low"], 5),
                "zone_width_pips": round(zone_width_pips, 1),
                "atr_pips":       round(atr_pips, 1) if atr_pips else None,
                "risk_reward":    round(rr_ratio, 2),
                "zone_classification": zone_classification,  # v4.0: TYPE_LIMIT or TYPE_CONFIRMATION
            },
        }

    def _calculate_probability(self, zone: dict, current_price: float) -> str:
        """
        Probability scoring (FIXED v2.1):

        CHANGE: HIGH tier DISABLED due to inducement trap (3.3% WR in backtest).
        Equal Highs/Lows attract liquidity sweeps that stop out limit orders.

        New logic:
            strength ≥ 5  OR   deep retest  →  MEDIUM
            otherwise                        →  LOW

        "Deep retest" means price has penetrated at least 61.8% into the zone.
        """
        zone_range = zone["high"] - zone["low"]
        if zone_range == 0:
            return "LOW"

        if zone["zone_type"] == "DEMAND":
            retest_depth = (zone["high"] - current_price) / zone_range
        else:
            retest_depth = (current_price - zone["low"]) / zone_range

        deep_retest = retest_depth >= self.ZONE_RETEST_THRESHOLD
        strong_zone = zone["strength"] >= 5

        # FIXED: Never return HIGH (inducement trap)
        # All previously-HIGH zones now become MEDIUM
        if strong_zone or deep_retest:
            return "MEDIUM"
        return "LOW"