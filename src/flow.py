import MetaTrader5 as mt5
import numpy as np
import logging
from datetime import datetime, time


class flow:
    """
    Flow - Trend Continuation Strategy
    -----------------------------------
    Type: Trend Following with Multi-Timeframe Alignment
    Source: fxalexg "1 Hour Day Trading Strategy"
    
    Strategy Logic:
        1. H4 and H1 trends must ALIGN (both bullish or both bearish)
        2. Identify Area of Interest (AOI) - Support/Resistance with 3+ touches
        3. Wait for price to touch the AOI
        4. Look for rejection candle (Engulfing or strong wick)
        5. Time Filter: Only trade during London (08:00-11:00) or NY (13:00-16:00) UTC
    
    Example (UPTREND):
        - H4 = UPTREND, H1 = UPTREND ✅
        - Price pulls back to Support (AOI)
        - Bullish engulfing candle at support
        - Enter BUY during London/NY session
    """
    
    # ─── Constants ─────────────────────────────────────────────────
    H4_LOOKBACK = 50            # Candles for H4 trend
    H1_LOOKBACK = 50            # Candles for H1 trend
    AOI_ZONE_BAND_PIPS = 20     # Zone clustering band size (JeaFx: "range not line")
    MIN_AOI_TOUCHES = 3         # Minimum swing points within zone band
    MIN_MOMENTUM_RATIO = 0.5    # Engulfing body must be >= 50% of avg body (relaxed from 1.0)
    REJECTION_WICK_RATIO = 0.6  # Wick must be ≥60% of candle range
    PRICE_AT_AOI_PIPS = 10      # Price must be within 10 pips of AOI
    MIN_RR_RATIO = 2.0          # Minimum Risk:Reward
    
    # Session times (UTC)
    LONDON_OPEN = time(8, 0)    # 08:00 UTC
    LONDON_CLOSE = time(11, 0)  # 11:00 UTC
    NY_OPEN = time(13, 0)       # 13:00 UTC (08:00 EST)
    NY_CLOSE = time(16, 0)      # 16:00 UTC (11:00 EST)
    
    def __init__(self, config: dict):
        """
        Parameters
        ----------
        config : dict
            Must contain:
                lot_size : float
                stop_loss_pips : int (optional)
                take_profit_pips : int (optional)
        """
        self.config = config
        logging.info("✅ Flow - Trend Continuation Strategy loaded")
        logging.info(f"   Sessions: London (08:00-11:00) + NY (13:00-16:00) UTC")
        logging.info(f"   AOI requirement: {self.MIN_AOI_TOUCHES}+ touches")
    
    # ═══════════════════════════════════════════════════════════════
    # PUBLIC ENTRY POINT
    # ═══════════════════════════════════════════════════════════════
    
    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Main analysis entry point for Flow strategy.
        
        Returns
        -------
        dict | None
            Signal dictionary if all conditions met, else None.
        """
        logging.info(f"   [{symbol}] Flow: Starting trend continuation analysis...")
        
        # ── SESSION FILTER (Live Mode only) ──────────────────────
        if provided_rates is None and not self._is_active_session():
            logging.info(f"   [{symbol}] Flow: ⏰ Outside trading hours (London/NY only)")
            return None
        
        # ── Fetch H4 and H1 candles ──────────────────────────────
        candles_h4 = self._get_candles(symbol, mt5.TIMEFRAME_H4, self.H4_LOOKBACK, provided_rates)
        candles_h1 = self._get_candles(symbol, mt5.TIMEFRAME_H1, self.H1_LOOKBACK, provided_rates)
        
        if not candles_h4 or not candles_h1:
            logging.error(f"   [{symbol}] Flow: Insufficient H4/H1 data")
            return None
        
        
        # ── Step 1: Determine H4 & H1 Trends ─────────────────────
        h4_trend = self._determine_trend(candles_h4, "H4")
        h1_trend = self._determine_trend(candles_h1, "H1")
        
        logging.info(f"   [{symbol}] Flow: H4={h4_trend} | H1={h1_trend}")
        
        # ── Step 2: Check Trend Alignment ────────────────────────
        # Only trade if BOTH trends are clearly defined AND aligned
        if h4_trend == "RANGING" or h1_trend == "RANGING":
            logging.info(f"   [{symbol}] Flow: ❌ Trend ranging (not trading choppy markets)")
            return None
        
        if h4_trend != h1_trend:
            logging.info(f"   [{symbol}] Flow: ❌ Trend mismatch (H4:{h4_trend} vs H1:{h1_trend})")
            return None
        
        logging.info(f"   [{symbol}] Flow: ✅ Trends ALIGNED → {h4_trend}")
        
        # TODO: Step 3 - Area of Interest (AOI) Detection
        # Future implementation: Check for support/resistance with minimum 3 touches
        # For now, continue to existing AOI logic
        
        # ── Step 3: Find Area of Interest (AOI) ──────────────────
        point = self._get_point_from_rates(candles_h1)
        aoi = self._find_aoi(candles_h1, h4_trend, point)
        
        if aoi is None:
            logging.info(f"   [{symbol}] Flow: No AOI (Support/Resistance) found")
            return None
        
        logging.info(f"   [{symbol}] Flow: ✅ AOI found at {aoi['level']:.5f} ({aoi['type']}, {aoi['touches']} touches)")
        
        # ── Step 4: Check if price is at AOI ─────────────────────
        current_price = candles_h1[-1]["close"]
        distance_pips = abs(current_price - aoi['level']) / point
        
        if distance_pips > self.PRICE_AT_AOI_PIPS:
            logging.info(f"   [{symbol}] Flow: Price too far from AOI ({distance_pips:.1f} pips > {self.PRICE_AT_AOI_PIPS})")
            return None
        
        logging.info(f"   [{symbol}] Flow: ✅ Price at AOI ({distance_pips:.1f} pips)")
        
        # ── Step 5: Detect Sweep + Reclaim Pattern ──────────────
        sweep = self._detect_sweep_and_reclaim(candles_h1[-10:], aoi, h4_trend, point)
        
        if not sweep:
            logging.info(f"   [{symbol}] Flow: No sweep + reclaim pattern detected")
            return None
        
        logging.info(f"   [{symbol}] Flow: ✅ Sweep + Reclaim confirmed (direction: {sweep['direction']})")
        
        # ── Generate Signal ──────────────────────────────────────
        signal = self._generate_signal(
            symbol=symbol,
            candles=candles_h1,
            trend=h4_trend,
            aoi=aoi,
            sweep=sweep,
            point=point
        )
        
        if signal:
            logging.info(f"   [{symbol}] Flow: 🎯 Trend continuation signal → {signal['direction']}")
        
        return signal
    
    # ═══════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════════
    
    def _is_active_session(self) -> bool:
        """Check if current UTC time is during London or NY session"""
        now_utc = datetime.utcnow().time()
        
        london_session = (self.LONDON_OPEN <= now_utc < self.LONDON_CLOSE)
        ny_session = (self.NY_OPEN <= now_utc < self.NY_CLOSE)
        
        return london_session or ny_session
    
    def _get_candles(self, symbol: str, timeframe: int, count: int,
                     provided_rates: dict | None) -> list[dict] | None:
        """Fetch candles (backtest or live mode)"""
        if provided_rates is not None:
            candles = provided_rates.get(timeframe)
            if candles is None or len(candles) < count:
                return None
            return candles[-count:]
        else:
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
            if rates is None or len(rates) < count:
                return None
            
            return [
                {
                    "time": int(r["time"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": int(r["tick_volume"]),
                }
                for r in rates
            ]
    
    def _get_point_from_rates(self, candles: list[dict]) -> float:
        """Infer pip size from candle data"""
        if not candles:
            return 0.00001
        
        diff = candles[-1]["high"] - candles[-1]["low"]
        if diff == 0:
            return 0.00001
        
        s_diff = f"{diff:f}"
        if '.' in s_diff:
            decimal_places = len(s_diff.split('.')[-1].rstrip('0'))
            if decimal_places > 0:
                return 10**(-decimal_places)
        return 0.00001
    
    
    # ───────────────────────────────────────────────────────────────
    # STEP 1: TREND DETERMINATION (fxalexg Methodology)
    # ───────────────────────────────────────────────────────────────
    
    def _determine_trend(self, candles: list[dict], label: str = "") -> str:
        """
        Determine trend using 2-candle fractal swing point analysis.
        
        Fractal Definition (fxalexg):
        - Swing High: candle's high > highs of 2 candles before AND 2 after
        - Swing Low: candle's low < lows of 2 candles before AND 2 after
        
        Trend Rules:
        - UPTREND: Higher High AND Higher Low
        - DOWNTREND: Lower High AND Lower Low
        - RANGING: Mixed signals or insufficient data
        
        Parameters
        ----------
        candles : list[dict]
            OHLC candle data
        label : str
            Optional label for logging (e.g., "H4", "H1")
        
        Returns
        -------
        str
            "UPTREND" | "DOWNTREND" | "RANGING"
        """
        swing_highs: list[tuple[int, float]] = []  # (index, price)
        swing_lows: list[tuple[int, float]] = []   # (index, price)
        
        # Need at least 5 candles for 2-candle fractal detection
        if len(candles) < 5:
            if label:
                logging.debug(f"   [{label}] Insufficient data for fractal detection ({len(candles)} < 5)")
            return "RANGING"
        
        # Scan for fractals (skip first 2 and last 2 candles)
        for i in range(2, len(candles) - 2):
            high = candles[i]["high"]
            low = candles[i]["low"]
            
            # Swing High fractal: high > all 4 neighboring candles
            if (high > candles[i-1]["high"] and 
                high > candles[i-2]["high"] and
                high > candles[i+1]["high"] and 
                high > candles[i+2]["high"]):
                swing_highs.append((i, high))
            
            # Swing Low fractal: low < all 4 neighboring candles
            if (low < candles[i-1]["low"] and
                low < candles[i-2]["low"] and
                low < candles[i+1]["low"] and
                low < candles[i+2]["low"]):
                swing_lows.append((i, low))
        
        # Need at least 2 swing points of each type for trend analysis
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            if label:
                logging.debug(f"   [{label}] Not enough swing points (H:{len(swing_highs)} L:{len(swing_lows)})")
            return "RANGING"
        
        # Compare last 2 swing highs and lows
        current_high = swing_highs[-1][1]
        prev_high = swing_highs[-2][1]
        current_low = swing_lows[-1][1]
        prev_low = swing_lows[-2][1]
        
        higher_high = current_high > prev_high
        higher_low = current_low > prev_low
        lower_high = current_high < prev_high
        lower_low = current_low < prev_low
        
        # Trend classification (fxalexg: "higher high higher low = uptrend")
        if higher_high and higher_low:
            return "UPTREND"
        elif lower_high and lower_low:
            return "DOWNTREND"
        else:
            # Mixed signals (e.g., higher high but lower low, or equal levels)
            if label:
                logging.debug(f"   [{label}] Mixed swing signals (HH:{higher_high} HL:{higher_low} LH:{lower_high} LL:{lower_low})")
            return "RANGING"
    
    
    # ───────────────────────────────────────────────────────────────
    # STEP 2: AREA OF INTEREST (AOI) DETECTION
    # ───────────────────────────────────────────────────────────────
    
    def _find_aoi(self, candles: list[dict], trend: str, point: float) -> dict | None:
        """
        Find Support (for UPTREND) or Resistance (for DOWNTREND) using Zone Clustering.
        
        ZONE CLUSTERING APPROACH:
        - Use ALL candle highs/lows (not just fractals)
        - Group into 20-pip bands
        - Find bands with 3+ touches
        
        Source: JeaFx "Supply/Demand is a range, not a line"
        
        Returns
        -------
        dict | None
            {
                'level': float,      # Zone midpoint (average of touches)
                'type': 'SUPPORT' | 'RESISTANCE',
                'touches': int       # Number of candles touching this zone
            }
        """
        band_size = self.AOI_ZONE_BAND_PIPS * point  # 20 pips
        
        # Collect ALL lows (for support) or ALL highs (for resistance)
        if trend == "UPTREND":
            levels = [c["low"] for c in candles]
            aoi_type = "SUPPORT"
        else:
            levels = [c["high"] for c in candles]
            aoi_type = "RESISTANCE"
        
        if not levels:
            return None
        
        # Create zone bands by clustering levels
        min_level = min(levels)
        max_level = max(levels)
        
        # Group levels into bands
        bands = {}
        for level in levels:
            # Calculate which band this level belongs to
            band_key = int((level - min_level) / band_size)
            if band_key not in bands:
                bands[band_key] = []
            bands[band_key].append(level)
        
        # Find bands with MIN_AOI_TOUCHES or more touches
        valid_zones = []
        for band_key, band_levels in bands.items():
            if len(band_levels) >= self.MIN_AOI_TOUCHES:
                # Use average of all touches in this band as zone level
                zone_level = sum(band_levels) / len(band_levels)
                valid_zones.append({
                    'level': zone_level,
                    'touches': len(band_levels)
                })
        
        if not valid_zones:
            return None
        
        # Sort by touches (descending) and return strongest zone
        valid_zones.sort(key=lambda x: x['touches'], reverse=True)
        best_zone = valid_zones[0]
        
        return {
            'level': best_zone['level'],
            'type': aoi_type,
            'touches': best_zone['touches']
        }
    
    # ───────────────────────────────────────────────────────────────
    # STEP 3: REJECTION CANDLE DETECTION
    # ───────────────────────────────────────────────────────────────
    
    def _detect_sweep_and_reclaim(self, recent_candles: list[dict], aoi: dict,
                                   trend: str, point: float) -> dict | None:
        """
        Detect liquidity SWEEP + RECLAIM pattern (JeaFx principle) - STRICT ENFORCEMENT.
        
        NO FALLBACK LOGIC. If no sweep is detected, returns None.
        
        SWEEP Definition:
        - Price violates AOI boundary (goes beyond it)
        - But closes INSIDE the AOI zone (reclaims)
        - Sweep must be meaningful: 0.5 to 15 pips
        
        BUY Setup (Support):
            1. Candle LOW sweeps BELOW support (liquidity grab)
            2. Candle CLOSE reclaims ABOVE support (confirms rejection)
            3. Candle is BULLISH (close > open)
            4. Sweep size: 0.5 to 15 pips (not noise, not breakout)
        
        SELL Setup (Resistance):
            1. Candle HIGH sweeps ABOVE resistance (liquidity grab)
            2. Candle CLOSE reclaims BELOW resistance (confirms rejection)
            3. Candle is BEARISH (close < open)
            4. Sweep size: 0.5 to 15 pips (not noise, not breakout)
        
        Returns
        -------
        dict | None
            {
                'type': 'SWEEP_RECLAIM',
                'sweep_low': float (for BUY),
                'sweep_high': float (for SELL),
                'direction': 'BUY' | 'SELL',
                'sweep_size_pips': float
            }
            or None if no valid sweep detected
        """
        MIN_SWEEP_PIPS = 0.5  # Minimum sweep to avoid noise
        MAX_SWEEP_PIPS = 15.0  # Maximum sweep to avoid breakouts
        
        aoi_level = aoi['level']
        
        # Check last 10 candles for sweep pattern
        for i, candle in enumerate(recent_candles[-10:]):
            if trend == "UPTREND":
                # BUY: Looking for sweep below support + reclaim above
                swept_below = candle["low"] < aoi_level
                reclaimed_above = candle["close"] > aoi_level
                is_bullish = candle["close"] > candle["open"]
                
                # Calculate sweep size in pips
                sweep_size_pips = (aoi_level - candle["low"]) / point
                
                # Debug logging
                logging.info(
                    f"      Candle {i}: Checking BUY Sweep | "
                    f"Low {candle['low']:.5f} vs AOI {aoi_level:.5f} | "
                    f"Swept? {swept_below} | Close {candle['close']:.5f} | "
                    f"Reclaimed? {reclaimed_above} | Sweep Size: {sweep_size_pips:.1f} pips"
                )
                
                # Validate sweep
                if swept_below and reclaimed_above and is_bullish:
                    # Check sweep bounds
                    if MIN_SWEEP_PIPS <= sweep_size_pips <= MAX_SWEEP_PIPS:
                        logging.info(
                            f"      ✅ VALID SWEEP DETECTED: {sweep_size_pips:.1f} pip sweep below support"
                        )
                        return {
                            'type': 'SWEEP_RECLAIM',
                            'sweep_low': candle["low"],
                            'direction': 'BUY',
                            'sweep_size_pips': sweep_size_pips
                        }
                    else:
                        logging.info(
                            f"      ❌ Sweep rejected: {sweep_size_pips:.1f} pips "
                            f"(must be {MIN_SWEEP_PIPS}-{MAX_SWEEP_PIPS} pips)"
                        )
            
            elif trend == "DOWNTREND":
                # SELL: Looking for sweep above resistance + reclaim below
                swept_above = candle["high"] > aoi_level
                reclaimed_below = candle["close"] < aoi_level
                is_bearish = candle["close"] < candle["open"]
                
                # Calculate sweep size in pips
                sweep_size_pips = (candle["high"] - aoi_level) / point
                
                # Debug logging
                logging.info(
                    f"      Candle {i}: Checking SELL Sweep | "
                    f"High {candle['high']:.5f} vs AOI {aoi_level:.5f} | "
                    f"Swept? {swept_above} | Close {candle['close']:.5f} | "
                    f"Reclaimed? {reclaimed_below} | Sweep Size: {sweep_size_pips:.1f} pips"
                )
                
                # Validate sweep
                if swept_above and reclaimed_below and is_bearish:
                    # Check sweep bounds
                    if MIN_SWEEP_PIPS <= sweep_size_pips <= MAX_SWEEP_PIPS:
                        logging.info(
                            f"      ✅ VALID SWEEP DETECTED: {sweep_size_pips:.1f} pip sweep above resistance"
                        )
                        return {
                            'type': 'SWEEP_RECLAIM',
                            'sweep_high': candle["high"],
                            'direction': 'SELL',
                            'sweep_size_pips': sweep_size_pips
                        }
                    else:
                        logging.info(
                            f"      ❌ Sweep rejected: {sweep_size_pips:.1f} pips "
                            f"(must be {MIN_SWEEP_PIPS}-{MAX_SWEEP_PIPS} pips)"
                        )
        
        logging.info("      ❌ No valid SWEEP_RECLAIM pattern found")
        return None
    
    def should_move_to_breakeven(self, entry: float, current_price: float, 
                                  sl: float, direction: str) -> bool:
        """
        Check if price has moved 1:1 risk (1R) to justify moving SL to break-even.
        
        This protects profits in choppy markets.
        
        Parameters
        ----------
        entry : float
            Entry price
        current_price : float
            Current market price
        sl : float
            Original stop loss
        direction : str
            'BUY' or 'SELL'
        
        Returns
        -------
        bool
            True if should move to BE
        """
        risk = abs(entry - sl)
        
        if direction == "BUY":
            profit = current_price - entry
            return profit >= risk  # Moved 1R in profit
        else:  # SELL
            profit = entry - current_price
            return profit >= risk
    
    # ───────────────────────────────────────────────────────────────
    # SIGNAL GENERATION
    # ───────────────────────────────────────────────────────────────
    
    def _generate_signal(self, symbol: str, candles: list[dict], trend: str,
                        aoi: dict, sweep: dict, point: float) -> dict | None:
        """
        Generate the final Flow signal.
        
        Direction:
            UPTREND → BUY (continuation)
            DOWNTREND → SELL (continuation)
        """
        current_price = candles[-1]["close"]
        
        if trend == "UPTREND":
            direction = "BUY"
            entry = current_price
            # NEW: Tighter SL based on sweep wick + 2 pips buffer
            sl = sweep['sweep_low'] - (2 * point)  # 2 pips below sweep
            
            # Target next resistance (2× risk)
            risk = entry - sl
            tp = entry + (risk * 2.0)
        
        elif trend == "DOWNTREND":
            direction = "SELL"
            entry = current_price
            # NEW: Tighter SL based on sweep wick + 2 pips buffer
            sl = sweep['sweep_high'] + (2 * point)  # 2 pips above sweep
            
            # Target support (2× risk)
            risk = sl - entry
            tp = entry - (risk * 2.0)
        
        else:
            return None
        
        # Calculate Risk:Reward
        risk_actual = abs(entry - sl)
        reward = abs(tp - entry)
        rr_ratio = reward / risk_actual if risk_actual > 0 else 0
        
        # Reject if RR < 2.0
        if rr_ratio < self.MIN_RR_RATIO:
            logging.info(f"   [{symbol}] Flow: ❌ RR too low: {rr_ratio:.2f} < {self.MIN_RR_RATIO}")
            return None
        
        # Calculate expected P&L
        lot_size = self.config.get('lot_size', 0.01)
        contract_size = 100_000
        expected_profit = reward * lot_size * contract_size
        expected_loss = risk_actual * lot_size * contract_size
        
        return {
            "symbol": symbol,
            "direction": direction,
            "probability": "HIGH",  # Trend continuation with multi-TF alignment
            "strategy_name": "flow",
            "entry_price": round(entry, 5),
            "suggested_sl": round(sl, 5),
            "suggested_tp": round(tp, 5),
            "order_type": "MARKET",
            "zone_type": "TREND_CONTINUATION",
            "risk_reward_ratio": round(rr_ratio, 2),
            "expected_profit": round(expected_profit, 2),
            "expected_loss": round(expected_loss, 2),
            "structure": {
                "h4_trend": trend,
                "h1_trend": trend,
                "aoi_level": round(aoi['level'], 5),
                "aoi_type": aoi['type'],
                "aoi_touches": aoi['touches'],
                "entry_type": sweep['type'],
                "sweep_low": round(sweep.get('sweep_low', 0), 5),
                "sweep_high": round(sweep.get('sweep_high', 0), 5)
            }
        }
