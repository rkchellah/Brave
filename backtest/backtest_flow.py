"""
backtest_flow.py — Flow Strategy Backtester (Production Version)
======================================================================

Flow Strategy: fxalexg's Multi-Timeframe Trend Continuation
- Entry timeframe: M30 (for engulfing/wick patterns)
- Trend analysis: H4/H1 strict alignment
- Session filter: London (08:00-11:00) + NY (13:00-16:00) UTC
- Risk management: Fixed 1% risk per trade
- Hard EOD exit: Force close at 21:00 UTC

Key Improvements:
    ✅ Chunked data fetching (10k candle batches)
    ✅ Time-machine synchronization (no lookahead bias)
    ✅ Strict M30 entry trigger detection
    ✅ Session filter enforcement
    ✅ Fixed 1% risk per trade
    ✅ Hard EOD exit at 21:00 UTC

"""

import MetaTrader5 as mt5
import numpy as np
import csv
import os
import sys
import logging
from datetime import datetime, timezone, time as dt_time

# Add parent directory to path to import Flow strategy
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from flow import flow


# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

# Trading pairs to backtest
SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "XAUUSD",
]

# Data fetching
H4_CANDLES_TO_FETCH  = 10_000           # Fetch 10k H4 candles as base
CHUNK_SIZE           = 10_000           # User requirement: 10k candle batches
H4_LOOKBACK          = 100              # Flow needs 100 H4 candles for trend analysis
H1_LOOKBACK          = 100              # Flow needs 100 H1 candles for trend analysis
M30_ENTRY_LOOKBACK   = 50               # Context for M30 pattern detection
MAX_OUTCOME_WAIT     = 200              # Max M30 candles to wait for TP/SL

# Trading parameters
INITIAL_BALANCE   = 100.0
RISK_PERCENT      = 0.01                # Fixed 1% risk per trade
SL_BUFFER_PIPS    = 15                  # Swing +/- 15 pips (fxalexg rule)
RR_RATIO          = 2.0                 # Fixed 1:2 Risk-Reward

# Session times (UTC)
LONDON_OPEN  = dt_time(8, 0)            # 08:00 UTC
LONDON_CLOSE = dt_time(11, 0)           # 11:00 UTC
NY_OPEN      = dt_time(13, 0)           # 13:00 UTC
NY_CLOSE     = dt_time(16, 0)           # 16:00 UTC
EOD_EXIT     = dt_time(21, 0)           # 21:00 UTC - Force close trades

# Pip sizes per currency pair
POINT_SIZES = {
    "EURUSD": 0.00001,  # 5-decimal
    "GBPUSD": 0.00001,  # 5-decimal
    "USDJPY": 0.001,    # 3-decimal (JPY pairs)
    "AUDUSD": 0.00001,  # 5-decimal
    "USDCAD": 0.00001,  # 5-decimal
    "XAUUSD": 0.01,     # 2-decimal (Gold)
}

# Paths
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
TRADE_LOG_FILE = os.path.join(_SCRIPT_DIR, "backtest_flow_trades.csv")

# MT5 connection (only needed if cache file doesn't exist)
MT5_LOGIN    = 68253778
MT5_PASSWORD = "Cptn3m01$"
MT5_SERVER   = "RoboForex-Pro"


# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(_SCRIPT_DIR, "backtest_flow.log"), mode="w", encoding="utf-8")
    ]
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1 — DATA FETCHING (10K CHUNK BATCHES)
# ═══════════════════════════════════════════════════════════════════════

def fetch_data_in_chunks(symbol: str, timeframe: int, total_candles: int) -> list[dict]:
    """
    Fetch data in 10,000 candle batches to prevent API crashes.
    Stitches batches together chronologically.
    
    Parameters
    ----------
    symbol : str
        Trading pair (e.g., "EURUSD")
    timeframe : int
        MT5 timeframe constant (e.g., mt5.TIMEFRAME_H4)
    total_candles : int
        Total number of candles to fetch
    
    Returns
    -------
    list[dict]
        Candles with keys: time, open, high, low, close, volume
    """
    # Generate cache filename
    tf_map = {
        mt5.TIMEFRAME_M30: "m30",
        mt5.TIMEFRAME_H1: "h1",
        mt5.TIMEFRAME_H4: "h4",
    }
    tf_str = tf_map.get(timeframe, "unknown")
    cache_file = os.path.join(_DATA_DIR, f"{symbol.lower()}_{tf_str}_{total_candles}.csv")
    
    # Check cache
    if os.path.exists(cache_file):
        log.info(f"    Loading {symbol} {tf_str.upper()} from cache...")
        with open(cache_file, "r") as f:
            reader = csv.DictReader(f)
            candles = [
                {
                    "time": int(row["time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                }
                for row in reader
            ]
        log.info(f"    Loaded {len(candles):,} candles from cache")
        return candles
    
    # Fetch from MT5 with chunking
    log.info(f"    Fetching {symbol} {tf_str.upper()} from MT5 ({total_candles:,} candles)...")
    
    if not mt5.initialize():
        raise RuntimeError("MT5 initialization failed")
    
    if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")
    
    all_candles = []
    
    if total_candles <= CHUNK_SIZE:
        # Single request - no chunking needed
        log.info(f"    Making single request for {total_candles:,} candles...")
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, total_candles)
        
        if rates is None or len(rates) == 0:
            mt5.shutdown()
            raise RuntimeError(f"Failed to fetch {symbol} {tf_str.upper()} data")
        
        all_candles = list(rates)
    
    else:
        # Multiple requests - chunking required
        num_chunks = (total_candles + CHUNK_SIZE - 1) // CHUNK_SIZE  # Ceiling division
        log.info(f"    Large request detected. Splitting into {num_chunks} chunks of {CHUNK_SIZE:,} candles...")
        
        start_pos = 0
        for chunk_num in range(num_chunks):
            # Calculate how many candles to fetch in this chunk
            remaining = total_candles - start_pos
            chunk_count = min(CHUNK_SIZE, remaining)
            
            log.info(f"    Fetching chunk {chunk_num + 1}/{num_chunks}: {chunk_count:,} candles from position {start_pos:,}...")
            
            rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, chunk_count)
            
            if rates is None or len(rates) == 0:
                log.warning(f"    Chunk {chunk_num + 1} returned no data. Stopping here.")
                break
            
            # Append chunk to master list
            all_candles.extend(list(rates))
            log.info(f"    Chunk {chunk_num + 1} received: {len(rates):,} candles")
            
            start_pos += chunk_count
        
        log.info(f"    Total candles fetched: {len(all_candles):,}")
    
    mt5.shutdown()
    
    if len(all_candles) == 0:
        raise RuntimeError(f"No data received for {symbol} {tf_str.upper()}")
    
    # Convert to list of dicts
    candles = [
        {
            "time": int(r["time"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["tick_volume"]),
        }
        for r in all_candles
    ]
    
    # Cache it
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(cache_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time","open","high","low","close","volume"])
        writer.writeheader()
        writer.writerows(candles)
    log.info(f"    Cached {len(candles):,} candles to {cache_file}")
    
    return candles


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2 — TIME MACHINE SYNCHRONIZATION
# ═══════════════════════════════════════════════════════════════════════

def get_trend_context(
    current_m30_time: int,
    h4_candles: list[dict],
    h1_candles: list[dict],
    h4_lookback: int = 100,
    h1_lookback: int = 100
) -> dict | None:
    """
    Returns CLOSED H4/H1 candles relative to current M30 time.
    
    CRITICAL: No lookahead bias - only use candles where time < current_m30_time
    
    Parameters
    ----------
    current_m30_time : int
        Unix timestamp of the CURRENT M30 candle
    h4_candles : list[dict]
        Full H4 dataset
    h1_candles : list[dict]
        Full H1 dataset
    h4_lookback : int
        Number of H4 candles to return
    h1_lookback : int
        Number of H1 candles to return
    
    Returns
    -------
    dict | None
        {
            "h4": list[dict],   # Last h4_lookback CLOSED H4 candles
            "h1": list[dict],   # Last h1_lookback CLOSED H1 candles
        }
        Returns None if insufficient data
    """
    
    # Helper function: Binary search for last closed candle
    def find_last_closed_index(candles: list[dict], current_time: int) -> int:
        """Find index of rightmost candle with time < current_time"""
        lo, hi = 0, len(candles) - 1
        idx = -1
        
        while lo <= hi:
            mid = (lo + hi) // 2
            if candles[mid]["time"] < current_time:
                idx = mid
                lo = mid + 1  # Look for more recent
            else:
                hi = mid - 1  # This candle is too new
        
        return idx
    
    # Find last closed H4 candle
    h4_idx = find_last_closed_index(h4_candles, current_m30_time)
    if h4_idx < h4_lookback - 1:
        return None  # Not enough H4 history
    
    h4_window = h4_candles[max(0, h4_idx - h4_lookback + 1) : h4_idx + 1]
    
    # Find last closed H1 candle
    h1_idx = find_last_closed_index(h1_candles, current_m30_time)
    if h1_idx < h1_lookback - 1:
        return None  # Not enough H1 history
    
    h1_window = h1_candles[max(0, h1_idx - h1_lookback + 1) : h1_idx + 1]
    
    return {
        "h4": h4_window,
        "h1": h1_window,
    }


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3 — STRICT STRATEGY RULES (fxalexg)
# ═══════════════════════════════════════════════════════════════════════

def is_active_session(m30_timestamp: int) -> bool:
    """
    Check if M30 candle is during London or NY session.
    
    Session Filter (fxalexg rule):
    - London: 08:00-11:00 UTC
    - NY: 13:00-16:00 UTC
    """
    candle_time = datetime.fromtimestamp(m30_timestamp, tz=timezone.utc).time()
    
    london_session = (LONDON_OPEN <= candle_time < LONDON_CLOSE)
    ny_session = (NY_OPEN <= candle_time < NY_CLOSE)
    
    return london_session or ny_session


def check_eod_exit(m30_timestamp: int) -> bool:
    """
    Check if we should force close trades (EOD rule).
    
    Source: "I would not hold throughout the daily candlestick closure"
    Hard exit at 21:00 UTC
    """
    candle_time = datetime.fromtimestamp(m30_timestamp, tz=timezone.utc).time()
    return candle_time >= EOD_EXIT


def detect_m30_sweep_reclaim(
    m30_candles: list[dict],
    aoi_level: float,
    trend: str,
    point: float
) -> dict | None:
    """
    Detect SWEEP + RECLAIM pattern on M30 candle (JeaFx Liquidity Principle).
    
    STRICT ENFORCEMENT - NO FALLBACK TO WICK/ENGULFING.
    
    SWEEP Definition:
    - Price violates AOI boundary (goes beyond it)
    - But closes INSIDE the AOI zone (reclaims)
    - Sweep must be meaningful: 0.5 to 15 pips
    
    Returns
    -------
    dict | None
        {'type': 'SWEEP_RECLAIM', 'candle_index': int, 'sweep_size_pips': float}
    """
    if len(m30_candles) < 2:
        return None
    
    MIN_SWEEP_PIPS = 0.5  # Minimum sweep to avoid noise
    MAX_SWEEP_PIPS = 15.0  # Maximum sweep to avoid breakouts
    
    # Check last 10 M30 candles for sweep pattern
    for i in range(len(m30_candles) - 10, len(m30_candles)):
        if i < 0:
            continue
        
        candle = m30_candles[i]
        
        if trend == "UPTREND":
            # BUY: Looking for sweep below support + reclaim above
            swept_below = candle["low"] < aoi_level
            reclaimed_above = candle["close"] > aoi_level
            is_bullish = candle["close"] > candle["open"]
            
            # Calculate sweep size in pips
            sweep_size_pips = (aoi_level - candle["low"]) / point
            
            # Debug logging
            log.info(
                f"      Candle {i}: Checking BUY Sweep | "
                f"Low {candle['low']:.5f} vs AOI {aoi_level:.5f} | "
                f"Swept? {swept_below} | Close {candle['close']:.5f} | "
                f"Reclaimed? {reclaimed_above} | Sweep Size: {sweep_size_pips:.1f} pips"
            )
            
            # Validate sweep
            if swept_below and reclaimed_above and is_bullish:
                # Check sweep bounds
                if MIN_SWEEP_PIPS <= sweep_size_pips <= MAX_SWEEP_PIPS:
                    log.info(
                        f"      ✅ VALID SWEEP DETECTED: {sweep_size_pips:.1f} pip sweep below support"
                    )
                    return {
                        'type': 'SWEEP_RECLAIM',
                        'candle_index': i,
                        'sweep_size_pips': sweep_size_pips,
                        'sweep_low': candle["low"]
                    }
                else:
                    log.info(
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
            log.info(
                f"      Candle {i}: Checking SELL Sweep | "
                f"High {candle['high']:.5f} vs AOI {aoi_level:.5f} | "
                f"Swept? {swept_above} | Close {candle['close']:.5f} | "
                f"Reclaimed? {reclaimed_below} | Sweep Size: {sweep_size_pips:.1f} pips"
            )
            
            # Validate sweep
            if swept_above and reclaimed_below and is_bearish:
                # Check sweep bounds
                if MIN_SWEEP_PIPS <= sweep_size_pips <= MAX_SWEEP_PIPS:
                    log.info(
                        f"      ✅ VALID SWEEP DETECTED: {sweep_size_pips:.1f} pip sweep above resistance"
                    )
                    return {
                        'type': 'SWEEP_RECLAIM',
                        'candle_index': i,
                        'sweep_size_pips': sweep_size_pips,
                        'sweep_high': candle["high"]
                    }
                else:
                    log.info(
                        f"      ❌ Sweep rejected: {sweep_size_pips:.1f} pips "
                        f"(must be {MIN_SWEEP_PIPS}-{MAX_SWEEP_PIPS} pips)"
                    )
    
    log.info("      ❌ No valid SWEEP_RECLAIM pattern found")
    return None


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4 — TRADE SIMULATOR (1% RISK + EOD EXIT)
# ═══════════════════════════════════════════════════════════════════════

class FlowTradeSimulator:
    """
    Simulates Flow strategy trades with:
    - Fixed 1% risk per trade
    - Hard EOD exit at 21:00 UTC
    - Pessimistic SL/TP execution
    """
    
    def __init__(self, symbol: str, initial_balance: float, point_size: float):
        self.symbol = symbol
        self.balance = initial_balance
        self.point_size = point_size
        
        # Trade state
        self.state = "IDLE"
        self.closed_trades = []
        
        # Current trade info
        self._direction = None
        self._entry_price = None
        self._sl = None
        self._tp = None
        self._entry_index = None
        self._entry_time = None
        self._lot_size = None
    
    def open_trade(
        self,
        direction: str,
        entry_price: float,
        sl: float,
        tp: float,
        entry_index: int,
        entry_time: int
    ) -> None:
        """Open trade with 1% risk calculation"""
        if self.state != "IDLE":
            return
        
        # Calculate position size for 1% risk
        sl_pips = abs(entry_price - sl) / self.point_size
        if sl_pips < 0.1:
            sl_pips = 0.1  # Minimum 0.1 pip to prevent division by zero
            
        risk_amount = self.balance * RISK_PERCENT
        
        # Standard lot calculation: 1 pip = $10 for 0.01 lot on XXXUSD pairs
        # For 1 standard lot (100,000 units), 1 pip = $10
        # For 0.01 lot (1,000 units), 1 pip = $0.10
        self._lot_size = risk_amount / (sl_pips * 0.10)  # Calculate lot size
        self._lot_size = max(0.01, min(self._lot_size, 10.0))  # Clamp between 0.01 and 10.0
        
        self._direction = direction
        self._entry_price = entry_price
        self._sl = sl
        self._tp = tp
        self._entry_index = entry_index
        self._entry_time = entry_time
        
        self.state = "ACTIVE"
        
        log.info(
            f"   [{self.symbol}] Flow: {self._direction} @ {self._entry_price:.5f} "
            f"| SL: {self._sl:.5f} | TP: {self._tp:.5f} | Lot: {self._lot_size:.2f} | Risk: 1%"
        )
    
    def step(self, candle: dict, candle_index: int) -> None:
        """
        Check if current M30 candle hits TP, SL, or EOD exit.
        """
        if self.state != "ACTIVE":
            return
        
        # Check EOD exit first (highest priority)
        if check_eod_exit(candle["time"]):
            exit_price = candle["close"]
            pnl = self._calculate_pnl(exit_price)
            self._close_trade(candle_index, candle["time"], "EOD_EXIT", pnl, exit_price)
            return
        
        # Check if max wait exceeded
        if candle_index - self._entry_index > MAX_OUTCOME_WAIT:
            self._close_trade(candle_index, candle["time"], "TIMEOUT", 0.0, candle["close"])
            return
        
        
        high = candle["high"]
        low = candle["low"]
        
        # NEW: Check break-even condition (1R profit)
        # Import flow strategy to access should_move_to_breakeven()
        from flow import flow
        flow_instance = flow({})
        
        be_moved = getattr(self, '_be_moved', False)
        
        if not be_moved and flow_instance.should_move_to_breakeven(
            self._entry_price, candle["close"], self._sl, self._direction
        ):
            self._sl = self._entry_price  # Move SL to entry
            self._be_moved = True
            log.info(f"   [{self.symbol}] Flow: 🔒 Moved SL to Break-Even at {self._entry_price:.5f}")
        
        sl_hit = False
        tp_hit = False
        
        if self._direction == "BUY":
            sl_hit = low <= self._sl
            tp_hit = high >= self._tp
        else:  # SELL
            sl_hit = high >= self._sl
            tp_hit = low <= self._tp
        
        # Pessimistic execution: If both hit, assume SL first
        if sl_hit and tp_hit:
            log.info(f"   [{self.symbol}] Flow: Pessimistic outcome (both SL and TP hit)")
            # Determine if BE was hit or original SL
            outcome = "BE_HIT_PESSIMISTIC" if getattr(self, '_be_moved', False) else "SL_HIT_PESSIMISTIC"
            self._close_trade(candle_index, candle["time"], outcome, self._calculate_pnl(self._sl), self._sl)
        elif sl_hit:
            # Determine if BE was hit or original SL
            outcome = "BE_HIT" if getattr(self, '_be_moved', False) else "SL_HIT"
            self._close_trade(candle_index, candle["time"], outcome, self._calculate_pnl(self._sl), self._sl)
        elif tp_hit:
            self._close_trade(candle_index, candle["time"], "TP_HIT", self._calculate_pnl(self._tp), self._tp)
    
    def _calculate_pnl(self, exit_price: float) -> float:
        """Calculate P&L in USD"""
        pips = (exit_price - self._entry_price) / self.point_size
        if self._direction == "SELL":
            pips = -pips
        return pips * self._lot_size * 0.10  # $0.10 per pip per 0.01 lot
    
    def _close_trade(
        self,
        close_index: int,
        close_time: int,
        outcome: str,
        pnl: float,
        exit_price: float
    ) -> None:
        """Record trade closure"""
        self.balance += pnl
        
        trade_record = {
            "symbol": self.symbol,
            "direction": self._direction,
            "entry_time": datetime.fromtimestamp(self._entry_time).strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": datetime.fromtimestamp(close_time).strftime("%Y-%m-%d %H:%M:%S"),
            "entry_price": self._entry_price,
            "exit_price": exit_price,
            "sl": self._sl,
            "tp": self._tp,
            "lot_size": self._lot_size,
            "outcome": outcome,
            "pnl": round(pnl, 2),
            "balance": round(self.balance, 2),
            "duration_candles": close_index - self._entry_index,
        }
        
        self.closed_trades.append(trade_record)
        self.state = "IDLE"
        
        log.info(
            f"   [{self.symbol}] Flow: {outcome} | P&L: ${pnl:+.2f} | "
            f"Balance: ${self.balance:.2f} | Duration: {close_index - self._entry_index} candles"
        )
    
    def is_idle(self) -> bool:
        return self.state == "IDLE"


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5 — FLOW BACKTESTER
# ═══════════════════════════════════════════════════════════════════════

class FlowBacktester:
    """
    Production-grade backtester for Flow strategy with strict fxalexg rules.
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.strategy = flow({"lot_size": 0.01})  # Lot size will be overridden by 1% risk
        self.simulator = FlowTradeSimulator(
            symbol=symbol,
            initial_balance=INITIAL_BALANCE,
            point_size=POINT_SIZES.get(symbol, 0.00001)
        )
    
    def run_backtest(
        self,
        h4_candles: list[dict],
        h1_candles: list[dict],
        m30_candles: list[dict]
    ) -> list[dict]:
        """
        Run Flow strategy backtest with strict rules.
        
        Returns
        -------
        list[dict]
            Closed trade records
        """
        log.info(f"\n{'='*70}")
        log.info(f"[{self.symbol}] Starting Flow Strategy Backtest")
        log.info(f"{'='*70}")
        log.info(f"[{self.symbol}] Data Loaded:")
        log.info(f"    H4:  {len(h4_candles):,} candles")
        log.info(f"    H1:  {len(h1_candles):,} candles")
        log.info(f"    M30: {len(m30_candles):,} candles")
        
        # Start from index where we have enough context
        start_index = M30_ENTRY_LOOKBACK
        signals_found = 0
        
        for i in range(start_index, len(m30_candles)):
            current_candle = m30_candles[i]
            current_time = current_candle["time"]
            
            # Update simulator (check if TP/SL/EOD hit)
            self.simulator.step(current_candle, i)
            
            # Skip if trade active
            if not self.simulator.is_idle():
                continue
            
            # ── RULE 1: SESSION FILTER ──────────────────────────
            if not is_active_session(current_time):
                continue  # Only trade during London/NY sessions
            
            # ── RULE 2: GET TREND CONTEXT (Time Machine) ────────
            context = get_trend_context(
                current_m30_time=current_time,
                h4_candles=h4_candles,
                h1_candles=h1_candles,
                h4_lookback=H4_LOOKBACK,
                h1_lookback=H1_LOOKBACK
            )
            
            if context is None:
                continue  # Not enough data
            
            # ── RULE 3: TREND ALIGNMENT (H4 == H1) ──────────────
            h4_trend = self.strategy._determine_trend(context["h4"], "H4")
            h1_trend = self.strategy._determine_trend(context["h1"], "H1")
            
            if h4_trend == "RANGING" or h1_trend == "RANGING":
                continue
            
            if h4_trend != h1_trend:
                continue  # Strict alignment required
            
            # ── RULE 4: AOI CHECK ────────────────────────────────
            point = self.simulator.point_size
            aoi = self.strategy._find_aoi(context["h1"], h4_trend, point)
            
            if aoi is None:
                continue
            
            # Check if price is within 10 pips of AOI
            current_price = current_candle["close"]
            distance_pips = abs(current_price - aoi['level']) / point
            
            if distance_pips > 10:
                continue  # Price too far from AOI
            
            # ── RULE 5: M30 ENTRY TRIGGER ────────────────────────
            # Get M30 window for pattern detection
            m30_start = max(0, i - 10)
            m30_window = m30_candles[m30_start:i+1]
            
            sweep = detect_m30_sweep_reclaim(
                m30_candles=m30_window,
                aoi_level=aoi['level'],
                trend=h4_trend,
                point=point
            )
            
            if not sweep:
                continue  # No valid sweep + reclaim pattern
            
            # ── SIGNAL CONFIRMED: Generate Trade ────────────────
            signals_found += 1
            log.info(f"\n[{self.symbol}] Signal #{signals_found} at M30 index {i} (time: {datetime.fromtimestamp(current_time)})")
            log.info(f"   Trend: {h4_trend} (H4==H1)")
            log.info(f"   AOI: {aoi['level']:.5f} ({aoi['type']}, {aoi['touches']} touches)")
            log.info(f"   Entry Pattern: {sweep['type']} ({sweep['sweep_size_pips']:.1f} pip sweep)")
            
            # Calculate SL/TP using SWEEP WICK (JeaFx logic)
            entry = current_price
            
            if h4_trend == "UPTREND":
                # SL: 2 pips below sweep wick (tighter, structure-based)
                sl = sweep['sweep_low'] - (2 * point)
                risk = entry - sl
                tp = entry + (risk * RR_RATIO)
            else:  # DOWNTREND
                # SL: 2 pips above sweep wick (tighter, structure-based)
                sl = sweep['sweep_high'] + (2 * point)
                risk = sl - entry
                tp = entry - (risk * RR_RATIO)
            
            # Open trade
            direction = "BUY" if h4_trend == "UPTREND" else "SELL"
            self.simulator.open_trade(
                direction=direction,
                entry_price=entry,
                sl=sl,
                tp=tp,
                entry_index=i,
                entry_time=current_time
            )
        
        log.info(f"\n[{self.symbol}] Backtest Complete")
        log.info(f"    Signals Found: {signals_found}")
        log.info(f"    Trades Closed: {len(self.simulator.closed_trades)}")
        log.info(f"    Final Balance: ${self.simulator.balance:.2f}")
        
        return self.simulator.closed_trades


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6 — PERFORMANCE REPORTING
# ═══════════════════════════════════════════════════════════════════════

def print_summary(all_trades: list[dict]) -> None:
    """Print comprehensive performance summary"""
    if not all_trades:
        log.info("\nNo trades executed!")
        return
    
    log.info(f"\n{'='*70}")
    log.info("FLOW STRATEGY BACKTEST SUMMARY")
    log.info(f"{'='*70}")
    
    # Overall metrics
    total_trades = len(all_trades)
    wins = [t for t in all_trades if t["outcome"] == "TP_HIT"]
    losses = [t for t in all_trades if "SL" in t["outcome"]]
    timeouts = [t for t in all_trades if t["outcome"] == "TIMEOUT"]
    eod_exits = [t for t in all_trades if t["outcome"] == "EOD_EXIT"]
    
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    total_pnl = sum(t["pnl"] for t in all_trades)
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0
    
    log.info(f"\nOverall Performance:")
    log.info(f"    Total Trades:    {total_trades}")
    log.info(f"    Wins:            {len(wins)} ({win_rate:.1f}%)")
    log.info(f"    Losses:          {len(losses)}")
    log.info(f"    Timeouts:        {len(timeouts)}")
    log.info(f"    EOD Exits:       {len(eod_exits)}")
    log.info(f"    Total P&L:       ${total_pnl:+,.2f}")
    log.info(f"    Avg Win:         ${avg_win:+.2f}")
    log.info(f"    Avg Loss:        ${avg_loss:+.2f}")
    
    # Calculate drawdown
    equity_curve = []
    running_balance = INITIAL_BALANCE * len(set(t["symbol"] for t in all_trades))
    peak_balance = running_balance
    max_drawdown = 0
    
    for trade in all_trades:
        running_balance += trade["pnl"]
        equity_curve.append(running_balance)
        
        if running_balance > peak_balance:
            peak_balance = running_balance
        
        drawdown = (peak_balance - running_balance) / peak_balance * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    
    final_balance = equity_curve[-1] if equity_curve else running_balance
    num_symbols = len(set(t["symbol"] for t in all_trades))
    initial_total = INITIAL_BALANCE * num_symbols
    total_return = ((final_balance - initial_total) / initial_total) * 100
    
    log.info(f"\nEquity Analysis:")
    log.info(f"    Initial Balance: ${initial_total:,.2f}")
    log.info(f"    Final Balance:   ${final_balance:,.2f}")
    log.info(f"    Total Return:    {total_return:+.2f}%")
    log.info(f"    Max Drawdown:    {max_drawdown:.2f}%")
    log.info(f"{'='*70}\n")


def export_trades_to_csv(trades: list[dict], filename: str) -> None:
    """Export all trades to CSV"""
    if not trades:
        return
    
    fieldnames = [
        "symbol", "direction", "entry_time", "exit_time", "entry_price", "exit_price",
        "sl", "tp", "lot_size", "outcome", "pnl", "balance", "duration_candles"
    ]
    
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)
    
    log.info(f"Trades exported to: {filename}")


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def main():
    log.info("="*70)
    log.info("FLOW STRATEGY BACKTEST - PRODUCTION VERSION")
    log.info("="*70)
    log.info(f"Strategy: fxalexg's Trend Continuation")
    log.info(f"Entry TF: M30 | Trend TF: H4/H1 (strict alignment)")
    log.info(f"Risk Management: Fixed 1% risk per trade")
    log.info(f"Session Filter: London (08:00-11:00) + NY (13:00-16:00) UTC")
    log.info(f"Hard EOD Exit: 21:00 UTC")
    log.info("="*70)
    
    all_trades = []
    
    for symbol in SYMBOLS:
        log.info(f"\n[{symbol}] Fetching data...")
        
        try:
            # Fetch H4 data (base timeframe)
            h4_candles = fetch_data_in_chunks(symbol, mt5.TIMEFRAME_H4, H4_CANDLES_TO_FETCH)
            
            # Calculate date range from H4
            h4_start_time = h4_candles[0]["time"]
            h4_end_time = h4_candles[-1]["time"]
            time_range_seconds = h4_end_time - h4_start_time
            
            # Calculate counts for H1 and M30 to cover same period (with buffer)
            h1_count = int(time_range_seconds / 3600) + 200   # H1 = 1 hour
            m30_count = int(time_range_seconds / 1800) + 400  # M30 = 30 min
            
            log.info(f"    Date range: {datetime.fromtimestamp(h4_start_time)} to {datetime.fromtimestamp(h4_end_time)}")
            log.info(f"    Calculated counts: H1={h1_count}, M30={m30_count}")
            
            # Fetch H1 and M30 for same period
            h1_candles = fetch_data_in_chunks(symbol, mt5.TIMEFRAME_H1, h1_count)
            m30_candles = fetch_data_in_chunks(symbol, mt5.TIMEFRAME_M30, m30_count)
            
            # Run backtest
            backtester = FlowBacktester(symbol)
            trades = backtester.run_backtest(h4_candles, h1_candles, m30_candles)
            all_trades.extend(trades)
            
        except Exception as e:
            log.error(f"[{symbol}] Error: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # Print summary and export
    print_summary(all_trades)
    export_trades_to_csv(all_trades, TRADE_LOG_FILE)
    
    log.info("\nBacktest complete!")


if __name__ == "__main__":
    main()
