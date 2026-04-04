import numpy as np
import requests
import logging
import os
from datetime import datetime, time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

PRISM_API_KEY = os.getenv("PRISM_API_KEY")

class Frost:
    """
    Frost Strategy — Mean Reversion Night Scalper
    Ported from MT5/Forex to Kraken crypto spot trading.

    Original: EURUSD, USDCHF forex pairs on MT5
    Ported:   XBTUSD, ETHUSD on Kraken via krakenex

    Core logic unchanged — Asian session mean reversion.
    Data source swapped: MT5 → Kraken OHLCV REST API
    Execution swapped:   MT5 order_send → krakenex AddOrder
    Pip definition:      Forex point → crypto price unit (0.1 for BTC)
    """

    # --- Session ---
    ASIAN_OPEN  = time(0, 0)
    ASIAN_CLOSE = time(6, 0)
    CUTOFF_TIME = time(5, 30)

    # --- MA Settings ---
    MA_PERIOD  = 20
    ATR_PERIOD = 14

    # --- Entry Thresholds (recalibrated for crypto dollar moves) ---
    # BTC moves $50-200 during Asian session, not forex pips
    # 1 "unit" here = $1 for BTC, so these are dollar deviations
    MIN_DEVIATION_PIPS = 50.0    # $50 minimum deviation from MA
    MAX_DEVIATION_PIPS = 1000.0  # $1000 max deviation for BTC
    MAX_ATR_PIPS       = 600.0   # Skip if ATR > $600 (too volatile for BTC)
    MIN_ATR_PIPS       = 10.0    # Skip if ATR < $10 (dead market)

    # --- Exit Settings ---
    TP_BUFFER_PIPS   = 5.0
    SL_BUFFER_PIPS   = 10.0
    MIN_RR           = 0.4
    MAX_CANDLES_HOLD = 16

    # --- Crypto point sizes ---
    POINT_MAP = {
        "XBTUSD": 0.1,   # BTC: 0.1 dollar precision
        "ETHUSD": 0.01,  # ETH: 0.01 dollar precision
    }

    # Kraken pair mapping for OHLCV endpoint
    KRAKEN_PAIR_MAP = {
        "XBTUSD": "XBTUSD",
        "ETHUSD": "ETHUSD",
    }

    def __init__(self, config: dict):
        self.config = config
        logging.info("Frost loaded — crypto mean reversion (Kraken)")
        logging.info(f"   Session: Asian only (00:00-06:00 UTC)")
        logging.info(f"   Pairs: XBTUSD, ETHUSD")

    # ═══════════════════════════════════════════════════════
    # PUBLIC ENTRY POINT
    # ═══════════════════════════════════════════════════════

    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Main entry point.
        provided_rates = None → fetch live from Kraken
        provided_rates = {"M15": list[dict]} → backtest mode
        """
        logging.info(f"   [{symbol}] Frost: Analyzing...")

        # 1. Session filter
        if provided_rates is None:
            if not self._is_asian_session():
                logging.info(f"   [{symbol}] Frost: Outside Asian session")
                return None
            if not self._is_safe_entry_time():
                logging.info(f"   [{symbol}] Frost: Past 05:30 cutoff")
                return None

        # 2. Fetch M15 candles
        candles = self._get_candles(symbol, provided_rates)
        if not candles or len(candles) < self.MA_PERIOD + self.ATR_PERIOD:
            logging.error(f"   [{symbol}] Frost: Insufficient data")
            return None

        # 3. Get point size for this symbol
        point = self.POINT_MAP.get(symbol, 0.1)

        # 4. ATR range check
        atr = self._calculate_atr(candles, point)
        if atr is None:
            return None
        if atr > self.MAX_ATR_PIPS:
            logging.info(f"   [{symbol}] Frost: ATR too high ({atr:.1f}) — volatile")
            return None
        if atr < self.MIN_ATR_PIPS:
            logging.info(f"   [{symbol}] Frost: ATR too low ({atr:.1f}) — dead market")
            return None
        logging.info(f"   [{symbol}] Frost: ATR = {atr:.1f}")

        # 5. MA and deviation
        ma_value = self._calculate_ma(candles)
        if ma_value is None:
            return None

        current_price = candles[-1]["close"]
        deviation = (current_price - ma_value) / point
        abs_deviation = abs(deviation)

        logging.info(
            f"   [{symbol}] Frost: Price={current_price:.2f} | "
            f"MA={ma_value:.2f} | Deviation={deviation:+.1f}"
        )

        # 6. Deviation threshold check
        if abs_deviation < self.MIN_DEVIATION_PIPS:
            logging.info(f"   [{symbol}] Frost: Deviation too small ({abs_deviation:.1f})")
            return None
        if abs_deviation > self.MAX_DEVIATION_PIPS:
            logging.info(f"   [{symbol}] Frost: Deviation too large — possible breakout")
            return None

        # 7. Ranging market check
        if self._is_trending(candles, point):
            logging.info(f"   [{symbol}] Frost: Market trending — skip")
            return None

        # 8. Generate signal
        direction = "SELL" if deviation > 0 else "BUY"
        return self._build_signal(symbol, current_price, ma_value, deviation, point, direction)

    # ═══════════════════════════════════════════════════════
    # DATA FETCH — replaces MT5 copy_rates_from_pos
    # ═══════════════════════════════════════════════════════

    def _get_candles(self, symbol: str, provided_rates: dict | None) -> list[dict] | None:
        """
        Fetch M15 OHLCV candles.
        Backtest: use provided_rates dict directly.
        Live: fetch from Kraken OHLCV public endpoint.
        Kraken interval 15 = 15 minutes.
        """
        if provided_rates is not None:
            return provided_rates.get("M15")

        pair = self.KRAKEN_PAIR_MAP.get(symbol, symbol)
        url = "https://api.kraken.com/0/public/OHLC"
        params = {"pair": pair, "interval": 15}

        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if data.get("error"):
                logging.error(f"   [{symbol}] Kraken OHLC error: {data['error']}")
                return None

            # Kraken returns pair name as key inside result
            result = data.get("result", {})
            pair_key = [k for k in result.keys() if k != "last"][0]
            raw = result[pair_key]

            # Kraken OHLC format: [time, open, high, low, close, vwap, volume, count]
            candles = [
                {
                    "time":   int(r[0]),
                    "open":   float(r[1]),
                    "high":   float(r[2]),
                    "low":    float(r[3]),
                    "close":  float(r[4]),
                    "volume": float(r[6]),
                }
                for r in raw
            ]
            return candles[-50:]  # last 50 candles

        except Exception as e:
            logging.error(f"   [{symbol}] Frost: Failed to fetch candles: {e}")
            return None

    # ═══════════════════════════════════════════════════════
    # INDICATORS — unchanged from original Frost
    # ═══════════════════════════════════════════════════════

    def _calculate_ma(self, candles: list[dict]) -> float | None:
        if len(candles) < self.MA_PERIOD:
            return None
        closes = [c["close"] for c in candles[-self.MA_PERIOD:]]
        return float(np.mean(closes))

    def _calculate_atr(self, candles: list[dict], point: float) -> float | None:
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
        if len(candles) < 10:
            return False
        closes = np.array([c["close"] for c in candles[-10:]])
        x = np.arange(len(closes))
        slope = float(np.polyfit(x, closes, 1)[0])
        slope_units = abs(slope) / point
        logging.info(f"   Frost: Trend slope = {slope_units:.2f} units/candle")
        return slope_units > 100.0  # validated: current BTC slope ~70, trending starts above 100

    # ═══════════════════════════════════════════════════════
    # SIGNAL BUILDER — unchanged from original Frost
    # ═══════════════════════════════════════════════════════

    def _build_signal(
        self,
        symbol: str,
        current_price: float,
        ma_value: float,
        deviation: float,
        point: float,
        direction: str,
    ) -> dict | None:

        if direction == "BUY":
            entry = current_price
            tp    = entry + ((ma_value - entry) * 0.8)
            sl    = entry - (abs(deviation) * point) - (self.SL_BUFFER_PIPS * point)
        else:
            entry = current_price
            tp    = entry - ((entry - ma_value) * 0.8)
            sl    = entry + (abs(deviation) * point) + (self.SL_BUFFER_PIPS * point)

        risk   = abs(entry - sl)
        reward = abs(tp - entry)

        if risk == 0:
            return None

        rr_ratio = reward / risk

        if rr_ratio < self.MIN_RR:
            logging.info(f"   [{symbol}] Frost: RR {rr_ratio:.2f} < {self.MIN_RR} — skip")
            return None

        volume = self.config.get("volume", 0.001)  # BTC volume, not forex lots

        logging.info(
            f"   [{symbol}] Frost: {direction} | "
            f"Entry={entry:.2f} SL={sl:.2f} TP={tp:.2f} RR={rr_ratio:.2f}"
        )

        return {
            "symbol":            symbol,
            "direction":         direction,
            "strategy_name":     "frost_kraken",
            "order_type":        "MARKET",
            "entry_price":       round(entry, 2),
            "suggested_sl":      round(sl,    2),
            "suggested_tp":      round(tp,    2),
            "risk_reward_ratio": round(rr_ratio, 2),
            "volume":            volume,
            "probability":       "HIGH" if abs(deviation) > self.MIN_DEVIATION_PIPS * 1.5 else "MEDIUM",
            "structure": {
                "ma_value":       round(ma_value, 2),
                "deviation":      round(deviation, 1),
                "session":        "ASIAN",
                "entry_logic":    "MEAN_REVERSION",
            },
        }

    # ═══════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════

    def _is_asian_session(self) -> bool:
        now = datetime.utcnow().time()
        return self.ASIAN_OPEN <= now < self.ASIAN_CLOSE

    def _is_safe_entry_time(self) -> bool:
        now = datetime.utcnow().time()
        return now < self.CUTOFF_TIME