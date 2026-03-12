import MetaTrader5 as mt5
import numpy as np
import logging
from datetime import datetime, time, timezone


class Thunder:
    """
    Thunder — EMA Stack Scalping Strategy
    ----------------------------------------
    Source: ForexFactory thread #896811 (adapted)
    Pairs:  EURUSD, GBPUSD, XAUUSD, US30, NAS100
    TF:     H4 trend filter + M15 execution

    Logic:
        1. H4 EMA stack: 8 > 13 > 21 = BUY side only
                         8 < 13 < 21 = SELL side only
                         Any other arrangement = no trade
        2. M15: Place pending stop entry ATR-scaled pips
                above the last 5-candle high (BUY)
                below the last 5-candle low (SELL)
        3. SL: Opposite extreme of the 5-candle range + ATR buffer
        4. TP: Minimum 2:1 RR from entry
        5. Session: London (08:00–11:00 UTC) + NY (13:00–16:00 UTC) only

    ATR scaling replaces the original fixed 3-pip buffer so the strategy
    adapts to Gold and Indices volatility vs tight FX spreads.
    """

    # We use MT5 timeframe constants for direct compatibility with the library's copy_rates functions.
    TF_H4  = mt5.TIMEFRAME_H4
    TF_M15 = mt5.TIMEFRAME_M15

    # ─── EMA periods (ForexFactory original) ───────────────────────
    EMA_FAST   = 8
    EMA_MID    = 13
    EMA_SLOW   = 21

    # ─── Lookbacks ─────────────────────────────────────────────────
    H4_CANDLES  = 60    # Enough for EMA 21 + history
    M15_CANDLES = 100   # Entry timeframe window

    # ─── Entry parameters ──────────────────────────────────────────
    RANGE_CANDLES      = 5      # Last N candles to define the breakout range
    ATR_PERIOD         = 14     # ATR period for dynamic buffer
    ATR_ENTRY_MULT     = 0.3    # Entry buffer = ATR × 0.3 above range high/low
    ATR_SL_MULT        = 0.5    # SL buffer = ATR × 0.5 beyond range extreme
    MIN_RR             = 2.0    # Minimum Risk:Reward to take the trade
    MIN_ATR_PIPS       = 3.0    # Skip if market is dead (ATR < 3 pips)

    # ─── Session windows (UTC) ─────────────────────────────────────
    LONDON_OPEN  = time(8, 0)
    LONDON_CLOSE = time(11, 0)
    NY_OPEN      = time(13, 0)
    NY_CLOSE     = time(16, 0)

    def __init__(self, config: dict):
        """
        Parameters
        ----------
        config : dict
            lot_size : float
        """
        self.config = config
        logging.info(" Thunder — EMA Stack Scalper loaded")
        logging.info(f"   EMAs: {self.EMA_FAST}/{self.EMA_MID}/{self.EMA_SLOW} | "
                     f"Range: {self.RANGE_CANDLES} candles | "
                     f"Min RR: {self.MIN_RR}")

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC ENTRY POINT
    # ═══════════════════════════════════════════════════════════════

    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Run Thunder analysis on a symbol.

        Returns signal dict if all conditions pass, else None.
        """
        logging.info(f"   [{symbol}] Thunder: Starting EMA stack analysis...")

        # ── Session filter (live mode only) ──────────────────────
        if provided_rates is None and not self._is_active_session():
            logging.info(f"   [{symbol}] Thunder: Outside London/NY session")
            return None

        # ── Fetch candles ─────────────────────────────────────────
        h4  = self._get_candles(symbol, self.TF_H4,  self.H4_CANDLES,  provided_rates)
        m15 = self._get_candles(symbol, self.TF_M15, self.M15_CANDLES, provided_rates)

        if h4 is None or m15 is None:
            logging.error(f"   [{symbol}] Thunder: Failed to fetch candle data")
            return None

        # ── Step 1: H4 EMA stack direction ───────────────────────
        direction = self._ema_stack_direction(h4, symbol)

        if direction is None:
            logging.info(f"   [{symbol}] Thunder: EMA stack not aligned — no trade")
            return None

        logging.info(f"   [{symbol}] Thunder: ✅ H4 EMA stack → {direction}")

        # ── Step 2: ATR filter — skip dead markets ────────────────
        point   = self._get_point(symbol, m15)
        atr     = self._calculate_atr(m15)
        atr_pips = atr / point

        if atr_pips < self.MIN_ATR_PIPS:
            logging.info(f"   [{symbol}] Thunder: Low volatility ({atr_pips:.1f} pips ATR) — skip")
            return None

        logging.info(f"   [{symbol}] Thunder: ATR = {atr_pips:.1f} pips")

        # ── Step 3: Define 5-candle breakout range ────────────────
        range_candles = m15[-(self.RANGE_CANDLES + 1):-1]  # Exclude current forming candle
        range_high = max(c["high"] for c in range_candles)
        range_low  = min(c["low"]  for c in range_candles)
        range_pips = (range_high - range_low) / point

        logging.info(f"   [{symbol}] Thunder: 5-candle range = {range_pips:.1f} pips "
                     f"({range_low:.5f} – {range_high:.5f})")

        # ── Step 4: Calculate entry, SL, TP ──────────────────────
        entry_buffer = atr * self.ATR_ENTRY_MULT
        sl_buffer    = atr * self.ATR_SL_MULT

        if direction == "BUY":
            entry = range_high + entry_buffer
            sl    = range_low  - sl_buffer
        else:  # SELL
            entry = range_low  - entry_buffer
            sl    = range_high + sl_buffer

        risk   = abs(entry - sl)
        tp     = entry + (risk * self.MIN_RR) if direction == "BUY" else entry - (risk * self.MIN_RR)
        rr     = abs(tp - entry) / risk if risk > 0 else 0

        if rr < self.MIN_RR:
            logging.info(f"   [{symbol}] Thunder: RR {rr:.2f} below minimum {self.MIN_RR} — skip")
            return None

        # ── Step 5: Build signal ──────────────────────────────────
        lot_size      = self.config.get("lot_size", 0.01)
        contract_size = 100_000
        expected_profit = abs(tp - entry) * lot_size * contract_size
        expected_loss   = risk * lot_size * contract_size

        signal = {
            "symbol":          symbol,
            "direction":       direction,
            "strategy_name":   "Thunder",
            "order_type":      "STOP",          # Pending stop entry
            "entry_price":     round(entry, 5),
            "suggested_sl":    round(sl,    5),
            "suggested_tp":    round(tp,    5),
            "risk_reward_ratio": round(rr,  2),
            "expected_profit": round(expected_profit, 2),
            "expected_loss":   round(expected_loss,   2),
            "probability":     "MEDIUM",
            "structure": {
                "h4_ema_direction": direction,
                "range_high":   round(range_high, 5),
                "range_low":    round(range_low,  5),
                "range_pips":   round(range_pips, 1),
                "atr_pips":     round(atr_pips,   1),
                "entry_buffer": round(entry_buffer / point, 1),
                "sl_buffer":    round(sl_buffer    / point, 1),
            },
        }

        logging.info(f"   [{symbol}] Thunder: ✅ Signal → {direction} | "
                     f"Entry: {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | RR: {rr:.2f}")

        return signal

    # ═══════════════════════════════════════════════════════════════
    # CORE LOGIC
    # ═══════════════════════════════════════════════════════════════

    def _ema_stack_direction(self, candles: list[dict], symbol: str) -> str | None:
        """
        Determine trend direction from H4 EMA 8/13/21 stack.

        BUY  → EMA8 > EMA13 > EMA21 (bullish stack)
        SELL → EMA8 < EMA13 < EMA21 (bearish stack)
        None → Any other arrangement (choppy, transitioning)
        """
        # We extract closes into a numpy array for faster mathematical operations using vectorization.
        closes = np.array([c["close"] for c in candles], dtype=np.float64)

        if len(closes) < self.EMA_SLOW:
            logging.warning(f"   [{symbol}] Thunder: Not enough H4 candles for EMA {self.EMA_SLOW}")
            return None

        ema8  = self._ema(closes, self.EMA_FAST)[-1]
        ema13 = self._ema(closes, self.EMA_MID)[-1]
        ema21 = self._ema(closes, self.EMA_SLOW)[-1]

        logging.info(f"   [{symbol}] Thunder: H4 EMAs — "
                     f"EMA8={ema8:.5f} | EMA13={ema13:.5f} | EMA21={ema21:.5f}")

        if ema8 > ema13 > ema21:
            return "BUY"
        if ema8 < ema13 < ema21:
            return "SELL"

        return None  # Stack not clean

    def _calculate_atr(self, candles: list[dict]) -> float:
        """ATR over the last ATR_PERIOD candles"""
        # We use high, low, and previous close to account for potential gaps between candles.
        highs  = np.array([c["high"]  for c in candles], dtype=np.float64)
        lows   = np.array([c["low"]   for c in candles], dtype=np.float64)
        closes = np.array([c["close"] for c in candles], dtype=np.float64)

        tr = np.maximum(
            highs[1:]  - lows[1:],
            np.abs(highs[1:]  - closes[:-1]),
            np.abs(lows[1:]   - closes[:-1])
        )

        return float(np.mean(tr[-self.ATR_PERIOD:]))

    # ═══════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════

    def _is_active_session(self) -> bool:
        """True if current UTC time is within London or NY session"""
        now = datetime.now(timezone.utc).time()
        return (self.LONDON_OPEN <= now < self.LONDON_CLOSE or
                self.NY_OPEN     <= now < self.NY_CLOSE)

    def _get_candles(self, symbol: str, timeframe: int, count: int,
                     provided_rates: dict | None) -> list[dict] | None:
        """Fetch candles — backtest mode uses provided_rates, live mode uses MT5"""
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
        """
        Pip size. In live mode fetch from MT5 symbol info.
        In backtest mode infer from candle price scale.
        """
        info = mt5.symbol_info(symbol)
        if info is not None:
            return info.point

        # Backtest fallback: infer from candle data
        diff = candles[-1]["high"] - candles[-1]["low"]
        if diff == 0:
            return 0.00001

        s = f"{diff:f}"
        if "." in s:
            decimals = len(s.split(".")[-1].rstrip("0"))
            if decimals > 0:
                return 10 ** (-decimals)

        return 0.00001

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Standard EMA over a 1-D array"""
        k   = 2.0 / (period + 1)
        ema = np.empty_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = data[i] * k + ema[i - 1] * (1 - k)
        return ema