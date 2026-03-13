# backtest/backtest_brave.py — Brave Backtester
# ─────────────────────────────────────────────────────────────────────
# Architecture mirrors bot.py exactly.
#
# BraveBacktester  ←→  BraveBot
#   _check_signals()       same loop logic, same guards
#   _count_positions()     same: orders + positions
#   _load_active_strategy() same: strategy registry
#   execute_signal()       simulated: no MT5, uses BacktestAccount
#   _get_current_session() identical copy
#   Daily loss limiter     identical 5% logic
#
# What replaces MT5:
#   BacktestAccount   — balance, equity, P&L tracking
#   BacktestOrder     — pending STOP order waiting to trigger
#   BacktestPosition  — active trade tracking SL/TP per candle
#
# Pessimistic execution rule (same as original backtester):
#   If SL and TP both hit on the same candle → SL wins.
#
# Run from project root:
#   python backtest/backtest_brave.py
# ─────────────────────────────────────────────────────────────────────

import os
import sys
import csv
import logging
import bisect
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, time, timezone, timedelta
from typing import Optional

import numpy as np

# ── Path setup — allows importing thunder.py from project root ────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from thunder import Thunder

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────

SYMBOLS          = ["EURUSD", "GBPUSD", "XAUUSD"]
STARTING_BALANCE = 300.0       # Matches original backtest starting balance
RISK_PER_TRADE   = 0.01        # 1% risk per trade — same as bot.py
DAILY_LOSS_LIMIT = 0.05        # 5% daily loss cap — same as bot.py
MAX_CANDLES_WAIT = 100         # Pending order expires after N M15 candles
CONTRACT_SIZE    = 100_000     # Standard lot

# Point sizes — used when MT5 is not available (CSV cache mode)
POINT_SIZES = {
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "USDJPY": 0.001,
    "XAUUSD": 0.01,
    "US30":   0.01,
    "NAS100": 0.01,
}

# Strategy registry — same as bot.py
# Add new strategies here to backtest them with zero other changes
STRATEGY_REGISTRY: dict = {
    "thunder": Thunder,
}

ACTIVE_STRATEGY = "thunder"

# ─────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────

os.makedirs("backtest/results", exist_ok=True)
os.makedirs("logs",             exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/backtest_brave.log", mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


# ─────────────────────────────────────────────────────────────────────
# SIMULATED MT5 OBJECTS
# ─────────────────────────────────────────────────────────────────────

@dataclass
class BacktestAccount:
    """
    Simulates mt5.account_info().
    Mirrors the fields bot.py actually reads: balance, equity, profit.
    """
    balance: float = STARTING_BALANCE
    equity:  float = STARTING_BALANCE
    profit:  float = 0.0

    # Daily session tracking — mirrors bot.py _session_start_equity logic
    _session_start_equity: float  = field(default=STARTING_BALANCE, repr=False)
    _last_session_date:    str    = field(default="",               repr=False)
    _peak_equity:          float  = field(default=STARTING_BALANCE, repr=False)
    _max_drawdown:         float  = field(default=0.0,              repr=False)

    def update_equity(self) -> None:
        self.equity = self.balance + self.profit
        dd = (self._peak_equity - self.equity) / self._peak_equity if self._peak_equity > 0 else 0
        if dd > self._max_drawdown:
            self._max_drawdown = dd
        if self.equity > self._peak_equity:
            self._peak_equity = self.equity

    def close_trade(self, pnl: float) -> None:
        self.balance += pnl
        self.profit   = 0.0
        self.update_equity()


@dataclass
class BacktestOrder:
    """
    Simulates a pending MT5 BUY_STOP or SELL_STOP order.
    Mirrors the fields bot.py reads from mt5.orders_get().
    """
    ticket:         int
    symbol:         str
    direction:      str     # "BUY" | "SELL"
    entry_price:    float
    sl:             float
    tp:             float
    lot:            float
    strategy:       str
    session:        str
    placed_at_time: int     # Unix timestamp of the candle it was placed on
    candles_waited: int = 0
    signal:         dict = field(default_factory=dict, repr=False)


@dataclass
class BacktestPosition:
    """
    Simulates an active MT5 position.
    Mirrors the fields bot.py reads from mt5.positions_get().
    """
    ticket:      int
    symbol:      str
    direction:   str
    entry_price: float
    sl:          float
    tp:          float
    lot:         float
    strategy:    str
    session:     str
    entry_time:  int
    signal:      dict = field(default_factory=dict, repr=False)


@dataclass
class ClosedTrade:
    """One completed trade — written to CSV and used in analytics."""
    ticket:       int
    symbol:       str
    direction:    str
    strategy:     str
    session:      str
    order_type:   str
    entry_price:  float
    exit_price:   float
    sl:           float
    tp:           float
    lot:          float
    pnl:          float
    outcome:      str       # "WIN" | "LOSS" | "EXPIRED"
    risk_reward:  float
    entry_time:   str
    exit_time:    str
    duration_min: int
    balance_after: float
    equity_after:  float
    # Signal metadata
    h4_ema_direction: str  = ""
    range_pips:       float = 0.0
    atr_pips:         float = 0.0


# ─────────────────────────────────────────────────────────────────────
# DATA LAYER
# ─────────────────────────────────────────────────────────────────────

def fetch_candles(symbol: str, timeframe: int, count: int = 10_000) -> list[dict]:
    """
    Fetch candles from MT5 with CSV caching.
    Same data layer as the original backtester — chunked 10k fetches.
    """
    tf_name = {
        mt5.TIMEFRAME_M15: "M15",
        mt5.TIMEFRAME_H4:  "H4",
    }.get(timeframe, str(timeframe))

    cache_path = f"data/{symbol}_{tf_name}.csv"
    os.makedirs("data", exist_ok=True)

    if os.path.exists(cache_path):
        logging.info(f"[{symbol}] Loading {tf_name} from cache: {cache_path}")
        candles = []
        with open(cache_path, newline="") as f:
            for row in csv.DictReader(f):
                candles.append({
                    "time":   int(row["time"]),
                    "open":   float(row["open"]),
                    "high":   float(row["high"]),
                    "low":    float(row["low"]),
                    "close":  float(row["close"]),
                    "volume": int(row["volume"]),
                })
        logging.info(f"[{symbol}] {tf_name}: {len(candles)} candles loaded from cache")
        return candles

    if not MT5_AVAILABLE:
        raise RuntimeError(f"No cache for {symbol} {tf_name} and MT5 not available")

    logging.info(f"[{symbol}] Fetching {tf_name} from MT5 (up to {count} candles)...")
    all_rates = []
    chunk     = 5_000
    pos       = 0

    while True:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, pos, chunk)
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        if len(rates) < chunk:
            break
        pos += len(rates)
        if pos >= count:
            break

    if not all_rates:
        raise RuntimeError(f"MT5 returned no data for {symbol} {tf_name}")

    candles = [
        {
            "time":   int(r["time"]),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": int(r["tick_volume"]),
        }
        for r in all_rates
    ]
    candles.sort(key=lambda c: c["time"])

    with open(cache_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(candles)

    logging.info(f"[{symbol}] {tf_name}: {len(candles)} candles fetched and cached")
    return candles


def get_h4_slice(h4_candles: list[dict], before_timestamp: int, count: int) -> list[dict]:
    """
    Return up to `count` H4 candles whose time < before_timestamp.
    Binary search — no lookahead bias.
    """
    times = [c["time"] for c in h4_candles]
    idx   = bisect.bisect_left(times, before_timestamp)
    start = max(0, idx - count)
    return h4_candles[start:idx]


# ─────────────────────────────────────────────────────────────────────
# BRAVE BACKTESTER — mirrors BraveBot
# ─────────────────────────────────────────────────────────────────────

class BraveBacktester:
    """
    Mirrors BraveBot from bot.py.

    Method names are intentionally identical so that reading bot.py
    and backtest_brave.py side by side makes sense.

    The only differences:
    - No MT5 live connection — candles are pre-loaded
    - No Firebase — results go to CSV + console
    - No sleep() — walks candles as fast as CPU allows
    - execute_signal() simulates order placement instead of sending to MT5
    """

    # Session windows — identical to bot.py
    LONDON_OPEN  = time(8,  0)
    LONDON_CLOSE = time(11, 0)
    NY_OPEN      = time(13, 0)
    NY_CLOSE     = time(16, 0)

    def __init__(self, strategy_name: str = ACTIVE_STRATEGY):
        self.account = BacktestAccount()

        self.pending_orders: list[BacktestOrder]   = []
        self.open_positions: list[BacktestPosition] = []
        self.closed_trades:  list[ClosedTrade]      = []

        self.active_strategy_name: str             = strategy_name
        self.strategy_instance                     = None

        self._ticket_counter    = 1
        self._daily_equity_log: list[dict] = []    # For equity curve

        logging.info("=" * 60)
        logging.info("BRAVE BACKTESTER")
        logging.info(f"Strategy:        {strategy_name}")
        logging.info(f"Starting balance: ${STARTING_BALANCE:.2f}")
        logging.info(f"Risk per trade:  {RISK_PER_TRADE * 100:.1f}%")
        logging.info(f"Daily loss limit: {DAILY_LOSS_LIMIT * 100:.1f}%")
        logging.info("=" * 60)

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY LOADING — mirrors _load_active_strategy() in bot.py
    # ═══════════════════════════════════════════════════════════════

    def _load_active_strategy(self, config: dict) -> bool:
        """Identical logic to bot.py — loads from STRATEGY_REGISTRY."""
        requested = self.active_strategy_name

        if requested not in STRATEGY_REGISTRY:
            logging.error(f"Strategy '{requested}' not in registry. Available: {list(STRATEGY_REGISTRY.keys())}")
            return False

        if self.strategy_instance is None:
            self.strategy_instance    = STRATEGY_REGISTRY[requested](config)
            self.active_strategy_name = requested
            logging.info(f"Strategy loaded: {requested}")

        return True

    # ═══════════════════════════════════════════════════════════════
    # POSITION COUNTING — identical to bot.py Phase 2 fix
    # ═══════════════════════════════════════════════════════════════

    def _count_positions(self, symbol: str | None = None) -> int:
        """
        Counts both active positions AND pending orders.
        Identical logic to the Phase 2 bug fix in bot.py.
        """
        positions = [p for p in self.open_positions  if symbol is None or p.symbol == symbol]
        orders    = [o for o in self.pending_orders   if symbol is None or o.symbol == symbol]
        return len(positions) + len(orders)

    # ═══════════════════════════════════════════════════════════════
    # SESSION — identical copy from bot.py
    # ═══════════════════════════════════════════════════════════════

    def _get_current_session(self, candle_time: int | None = None) -> str:
        """UTC session windows. Accepts candle timestamp for backtest accuracy."""
        if candle_time is not None:
            dt      = datetime.fromtimestamp(candle_time, tz=timezone.utc)
            now_utc = dt.time()
        else:
            now_utc = datetime.now(timezone.utc).time()

        if self.LONDON_OPEN <= now_utc < self.LONDON_CLOSE:
            return "LONDON"
        if self.NY_OPEN <= now_utc < self.NY_CLOSE:
            return "NEW_YORK"
        return "OTHER"

    # ═══════════════════════════════════════════════════════════════
    # SIGNAL CHECKING — mirrors _check_signals() in bot.py
    # ═══════════════════════════════════════════════════════════════

    def _check_signals(
        self,
        config:      dict,
        symbol:      str,
        m15_slice:   list[dict],
        h4_slice:    list[dict],
        candle_time: int,
        account_date: str,
    ) -> None:
        """
        Mirrors bot.py _check_signals() exactly:
            1. Load strategy
            2. Daily loss limiter
            3. Count positions/orders — skip if already in market
            4. Run strategy.analyze()
            5. Execute signal if valid
        """
        if not self._load_active_strategy(config):
            return

        # ── Daily Loss Limiter — identical to bot.py ──────────────
        if self.account._last_session_date != account_date or self.account._session_start_equity is None:
            self.account._session_start_equity = self.account.equity
            self.account._last_session_date    = account_date

        session_pnl = self.account.equity - self.account._session_start_equity
        loss_limit  = -(self.account._session_start_equity * DAILY_LOSS_LIMIT)

        if session_pnl <= loss_limit:
            # Daily loss limit hit — same behavior as bot.py: stop trading for the day
            return

        # ── Skip if position/order already exists — Phase 2 fix ───
        if self._count_positions(symbol) > 0:
            return

        # ── Run strategy ──────────────────────────────────────────
        provided_rates = {
            Thunder.TF_H4:  h4_slice,
            Thunder.TF_M15: m15_slice,
        }

        signal = self.strategy_instance.analyze(symbol, provided_rates=provided_rates)

        if signal is None:
            return

        self.execute_signal(symbol, signal, config, candle_time)

    # ═══════════════════════════════════════════════════════════════
    # EXECUTION — simulated version of bot.py execute_signal()
    # ═══════════════════════════════════════════════════════════════

    def execute_signal(
        self,
        symbol:      str,
        signal:      dict,
        config:      dict,
        candle_time: int,
    ) -> None:
        """
        Simulates bot.py execute_signal() without MT5.

        Same risk sizing formula: risk_per_trade / (price_risk * contract_size)
        Same order type handling: STOP → BacktestOrder, MARKET → BacktestPosition
        """
        direction = signal["direction"]
        entry     = signal["entry_price"]
        sl        = signal["suggested_sl"]
        tp        = signal["suggested_tp"]
        sig_type  = signal.get("order_type", "STOP")

        price_risk = abs(entry - sl)
        if price_risk == 0:
            return

        point          = POINT_SIZES.get(symbol, 0.00001)
        risk_amount    = self.account.balance * RISK_PER_TRADE
        lot            = risk_amount / (price_risk * CONTRACT_SIZE)
        lot            = max(0.01, round(lot, 2))

        ticket  = self._ticket_counter
        self._ticket_counter += 1
        session = self._get_current_session(candle_time)

        if sig_type == "STOP":
            order = BacktestOrder(
                ticket         = ticket,
                symbol         = symbol,
                direction      = direction,
                entry_price    = entry,
                sl             = sl,
                tp             = tp,
                lot            = lot,
                strategy       = signal.get("strategy_name", self.active_strategy_name),
                session        = session,
                placed_at_time = candle_time,
                signal         = signal,
            )
            self.pending_orders.append(order)

        else:
            # Market order — immediately active
            position = BacktestPosition(
                ticket      = ticket,
                symbol      = symbol,
                direction   = direction,
                entry_price = entry,
                sl          = sl,
                tp          = tp,
                lot         = lot,
                strategy    = signal.get("strategy_name", self.active_strategy_name),
                session     = session,
                entry_time  = candle_time,
                signal      = signal,
            )
            self.open_positions.append(position)

    # ═══════════════════════════════════════════════════════════════
    # ORDER & POSITION SIMULATION
    # ═══════════════════════════════════════════════════════════════

    def _process_candle(self, candle: dict) -> None:
        """
        For each M15 candle:
            1. Check if pending STOP orders trigger (price crosses entry)
            2. Check if active positions hit SL or TP
            3. Expire orders that have waited too long
        """
        high = candle["high"]
        low  = candle["low"]
        ts   = candle["time"]

        # ── Step 1: Trigger pending orders ────────────────────────
        triggered = []
        for order in self.pending_orders:
            if order.symbol not in [candle.get("symbol", order.symbol)]:
                # Multi-symbol — each call is per symbol so this is always matching
                pass

            triggered_now = (
                (order.direction == "BUY"  and high >= order.entry_price) or
                (order.direction == "SELL" and low  <= order.entry_price)
            )

            if triggered_now:
                position = BacktestPosition(
                    ticket      = order.ticket,
                    symbol      = order.symbol,
                    direction   = order.direction,
                    entry_price = order.entry_price,
                    sl          = order.sl,
                    tp          = order.tp,
                    lot         = order.lot,
                    strategy    = order.strategy,
                    session     = order.session,
                    entry_time  = ts,
                    signal      = order.signal,
                )
                self.open_positions.append(position)
                triggered.append(order)
            else:
                order.candles_waited += 1

        for order in triggered:
            self.pending_orders.remove(order)

        # ── Step 2: Expire stale orders ───────────────────────────
        expired = [o for o in self.pending_orders if o.candles_waited >= MAX_CANDLES_WAIT]
        for order in expired:
            self.pending_orders.remove(order)
            self._record_expired(order, ts)

        # ── Step 3: Check SL/TP on active positions ───────────────
        closed = []
        for pos in self.open_positions:
            sl_hit = (pos.direction == "BUY"  and low  <= pos.sl) or \
                     (pos.direction == "SELL" and high >= pos.sl)
            tp_hit = (pos.direction == "BUY"  and high >= pos.tp) or \
                     (pos.direction == "SELL" and low  <= pos.tp)

            if sl_hit or tp_hit:
                # Pessimistic rule: if both hit on the same candle, SL wins
                if sl_hit:
                    exit_price = pos.sl
                    outcome    = "LOSS"
                else:
                    exit_price = pos.tp
                    outcome    = "WIN"

                pnl = self._calculate_pnl(pos, exit_price)
                self.account.close_trade(pnl)

                duration = (ts - pos.entry_time) // 60  # minutes

                structure = pos.signal.get("structure", {})
                trade = ClosedTrade(
                    ticket            = pos.ticket,
                    symbol            = pos.symbol,
                    direction         = pos.direction,
                    strategy          = pos.strategy,
                    session           = pos.session,
                    order_type        = "STOP",
                    entry_price       = pos.entry_price,
                    exit_price        = exit_price,
                    sl                = pos.sl,
                    tp                = pos.tp,
                    lot               = pos.lot,
                    pnl               = round(pnl, 2),
                    outcome           = outcome,
                    risk_reward       = pos.signal.get("risk_reward_ratio", 0.0),
                    entry_time        = datetime.fromtimestamp(pos.entry_time).strftime("%Y-%m-%d %H:%M"),
                    exit_time         = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                    duration_min      = duration,
                    balance_after     = round(self.account.balance, 2),
                    equity_after      = round(self.account.equity,  2),
                    h4_ema_direction  = structure.get("h4_ema_direction", ""),
                    range_pips        = structure.get("range_pips",       0.0),
                    atr_pips          = structure.get("atr_pips",         0.0),
                )
                self.closed_trades.append(trade)
                closed.append(pos)

        for pos in closed:
            self.open_positions.remove(pos)

        self.account.update_equity()

    def _record_expired(self, order: BacktestOrder, ts: int) -> None:
        """Record an expired pending order — no P&L impact."""
        trade = ClosedTrade(
            ticket           = order.ticket,
            symbol           = order.symbol,
            direction        = order.direction,
            strategy         = order.strategy,
            session          = order.session,
            order_type       = "STOP",
            entry_price      = order.entry_price,
            exit_price       = 0.0,
            sl               = order.sl,
            tp               = order.tp,
            lot              = order.lot,
            pnl              = 0.0,
            outcome          = "EXPIRED",
            risk_reward      = order.signal.get("risk_reward_ratio", 0.0),
            entry_time       = datetime.fromtimestamp(order.placed_at_time).strftime("%Y-%m-%d %H:%M"),
            exit_time        = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
            duration_min     = (ts - order.placed_at_time) // 60,
            balance_after    = round(self.account.balance, 2),
            equity_after     = round(self.account.equity,  2),
            h4_ema_direction = order.signal.get("structure", {}).get("h4_ema_direction", ""),
            range_pips       = order.signal.get("structure", {}).get("range_pips",       0.0),
            atr_pips         = order.signal.get("structure", {}).get("atr_pips",         0.0),
        )
        self.closed_trades.append(trade)

    def _calculate_pnl(self, pos: BacktestPosition, exit_price: float) -> float:
        """P&L calculation matching MT5's formula for FX and Gold."""
        price_diff = (exit_price - pos.entry_price) if pos.direction == "BUY" \
                else (pos.entry_price - exit_price)
        return price_diff * pos.lot * CONTRACT_SIZE

    # ═══════════════════════════════════════════════════════════════
    # MAIN RUN LOOP — mirrors bot.py run()
    # ═══════════════════════════════════════════════════════════════

    def run(self, symbol: str, m15_candles: list[dict], h4_candles: list[dict]) -> None:
        """
        Walk M15 candles forward in time.
        Each iteration mirrors one 60-second bot loop cycle.
        """
        config = {
            "lot_size":         0.01,
            "max_trades":       3,
            "stop_loss_pips":   50,
            "take_profit_pips": 100,
        }

        H4_LOOKBACK  = Thunder.H4_CANDLES
        M15_LOOKBACK = Thunder.M15_CANDLES
        MIN_START    = max(H4_LOOKBACK, M15_LOOKBACK) + 10

        logging.info(f"[{symbol}] Starting backtest — {len(m15_candles)} M15 candles")

        for i in range(MIN_START, len(m15_candles)):
            current_candle = m15_candles[i]
            candle_time    = current_candle["time"]
            account_date   = datetime.fromtimestamp(candle_time).strftime("%Y-%m-%d")

            # ── Process open orders/positions on this candle ───────
            # Tag candle with symbol for multi-symbol clarity
            candle_with_sym = {**current_candle, "symbol": symbol}
            self._process_candle(candle_with_sym)

            # ── Run strategy analysis (mirrors bot.py _check_signals) ─
            m15_slice = m15_candles[max(0, i - M15_LOOKBACK):i]
            h4_slice  = get_h4_slice(h4_candles, candle_time, H4_LOOKBACK)

            if len(h4_slice) >= Thunder.EMA_SLOW and len(m15_slice) >= Thunder.RANGE_CANDLES + Thunder.ATR_PERIOD:
                self._check_signals(
                    config       = config,
                    symbol       = symbol,
                    m15_slice    = m15_slice,
                    h4_slice     = h4_slice,
                    candle_time  = candle_time,
                    account_date = account_date,
                )

            # Log daily equity snapshot
            if i % 96 == 0:  # ~every 24 hours of M15 candles
                self._daily_equity_log.append({
                    "date":    account_date,
                    "balance": round(self.account.balance, 2),
                    "equity":  round(self.account.equity,  2),
                })

        # Close any positions still open at end of data
        if self.open_positions or self.pending_orders:
            last_candle = m15_candles[-1]
            for order in list(self.pending_orders):
                self.pending_orders.remove(order)
                self._record_expired(order, last_candle["time"])
            for pos in list(self.open_positions):
                mid_price = (last_candle["high"] + last_candle["low"]) / 2
                pnl = self._calculate_pnl(pos, mid_price)
                self.account.close_trade(pnl)
                duration = (last_candle["time"] - pos.entry_time) // 60
                self.closed_trades.append(ClosedTrade(
                    ticket=pos.ticket, symbol=pos.symbol, direction=pos.direction,
                    strategy=pos.strategy, session=pos.session, order_type="STOP",
                    entry_price=pos.entry_price, exit_price=mid_price,
                    sl=pos.sl, tp=pos.tp, lot=pos.lot, pnl=round(pnl, 2),
                    outcome="OPEN_AT_END", risk_reward=pos.signal.get("risk_reward_ratio", 0.0),
                    entry_time=datetime.fromtimestamp(pos.entry_time).strftime("%Y-%m-%d %H:%M"),
                    exit_time=datetime.fromtimestamp(last_candle["time"]).strftime("%Y-%m-%d %H:%M"),
                    duration_min=duration, balance_after=round(self.account.balance, 2),
                    equity_after=round(self.account.equity, 2),
                ))
                self.open_positions.remove(pos)

        logging.info(f"[{symbol}] Backtest complete — {len(self.closed_trades)} trades recorded")


# ─────────────────────────────────────────────────────────────────────
# ANALYTICS — mirrors what the mobile app will display
# ─────────────────────────────────────────────────────────────────────

def print_summary(trades: list[ClosedTrade], account: BacktestAccount) -> None:
    """
    Produces the same insights the mobile app's analytics screens will show:
      - Overall performance card
      - Per-symbol breakdown
      - Per-session breakdown (London / NY / Other)
      - Daily P&L summary
      - Drawdown
      - Expiry analysis
    """
    if not trades:
        logging.info("No trades to analyze.")
        return

    completed = [t for t in trades if t.outcome in ("WIN", "LOSS")]
    wins      = [t for t in completed if t.outcome == "WIN"]
    losses    = [t for t in completed if t.outcome == "LOSS"]
    expired   = [t for t in trades    if t.outcome == "EXPIRED"]

    total_pnl     = sum(t.pnl for t in completed)
    gross_profit  = sum(t.pnl for t in wins)
    gross_loss    = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate      = (len(wins) / len(completed) * 100) if completed else 0
    total_return  = ((account.balance - STARTING_BALANCE) / STARTING_BALANCE) * 100
    avg_win       = (gross_profit / len(wins))   if wins   else 0
    avg_loss      = (gross_loss   / len(losses)) if losses else 0
    avg_duration  = sum(t.duration_min for t in completed) / len(completed) if completed else 0

    print("\n" + "═" * 60)
    print("  BRAVE BACKTEST RESULTS")
    print("═" * 60)
    print(f"  Strategy:         {ACTIVE_STRATEGY.upper()}")
    print(f"  Starting balance: ${STARTING_BALANCE:.2f}")
    print(f"  Final balance:    ${account.balance:.2f}")
    print(f"  Total return:     {total_return:+.2f}%")
    print(f"  Total P&L:        ${total_pnl:+.2f}")
    print()
    print(f"  Total trades:     {len(trades)}")
    print(f"  Completed:        {len(completed)}  (W: {len(wins)}  L: {len(losses)})")
    print(f"  Expired:          {len(expired)}")
    print(f"  Win rate:         {win_rate:.1f}%")
    print(f"  Profit factor:    {profit_factor:.2f}")
    print(f"  Max drawdown:     {account._max_drawdown * 100:.2f}%")
    print(f"  Avg win:          ${avg_win:.2f}")
    print(f"  Avg loss:        -${avg_loss:.2f}")
    print(f"  Avg duration:     {avg_duration:.0f} min")

    # ── Per-symbol ────────────────────────────────────────────────
    print("\n  ── Per-Symbol ──────────────────────────────────────────")
    symbols = sorted(set(t.symbol for t in completed))
    for sym in symbols:
        sym_trades = [t for t in completed if t.symbol == sym]
        sym_wins   = [t for t in sym_trades if t.outcome == "WIN"]
        sym_pnl    = sum(t.pnl for t in sym_trades)
        sym_wr     = (len(sym_wins) / len(sym_trades) * 100) if sym_trades else 0
        print(f"  {sym:<10} Trades: {len(sym_trades):>4}  Win: {sym_wr:>5.1f}%  P&L: ${sym_pnl:>+8.2f}")

    # ── Per-session ───────────────────────────────────────────────
    print("\n  ── Per-Session ─────────────────────────────────────────")
    for sess in ["LONDON", "NEW_YORK", "OTHER"]:
        s_trades = [t for t in completed if t.session == sess]
        s_wins   = [t for t in s_trades  if t.outcome == "WIN"]
        s_pnl    = sum(t.pnl for t in s_trades)
        s_wr     = (len(s_wins) / len(s_trades) * 100) if s_trades else 0
        print(f"  {sess:<12} Trades: {len(s_trades):>4}  Win: {s_wr:>5.1f}%  P&L: ${s_pnl:>+8.2f}")

    # ── Direction breakdown ───────────────────────────────────────
    print("\n  ── Direction ───────────────────────────────────────────")
    for direction in ["BUY", "SELL"]:
        d_trades = [t for t in completed if t.direction == direction]
        d_wins   = [t for t in d_trades  if t.outcome == "WIN"]
        d_pnl    = sum(t.pnl for t in d_trades)
        d_wr     = (len(d_wins) / len(d_trades) * 100) if d_trades else 0
        print(f"  {direction:<6}       Trades: {len(d_trades):>4}  Win: {d_wr:>5.1f}%  P&L: ${d_pnl:>+8.2f}")

    # ── Expiry analysis ───────────────────────────────────────────
    print("\n  ── Order Expiry ────────────────────────────────────────")
    total_signals = len(trades)
    expiry_rate   = (len(expired) / total_signals * 100) if total_signals else 0
    print(f"  Signals generated:  {total_signals}")
    print(f"  Orders expired:     {len(expired)}  ({expiry_rate:.1f}% of signals)")
    print(f"  Orders triggered:   {len(completed)}  ({100 - expiry_rate:.1f}% of signals)")

    print("\n  ── Backtest vs Live Template ────────────────────────────")
    print(f"  {'Metric':<22} {'Backtest':>10}  {'Live (fill in)':>14}")
    print(f"  {'Win rate':<22} {win_rate:>9.1f}%  {'____%':>14}")
    print(f"  {'Profit factor':<22} {profit_factor:>10.2f}  {'____':>14}")
    print(f"  {'Max drawdown':<22} {account._max_drawdown * 100:>9.2f}%  {'____%':>14}")
    print(f"  {'Expiry rate':<22} {expiry_rate:>9.1f}%  {'____%':>14}")
    print("═" * 60 + "\n")


def export_csv(trades: list[ClosedTrade], equity_log: list[dict], symbol: str) -> None:
    """
    Exports two files:
      1. trades_{symbol}.csv  — same format as bot.py _log_trade_to_csv()
         so backtest CSV and live CSV have identical columns
      2. equity_{symbol}.csv  — daily balance/equity for equity curve chart
    """
    os.makedirs("backtest/results", exist_ok=True)

    # Trades CSV — matches _log_trade_to_csv() fields exactly
    trades_path = f"backtest/results/trades_{symbol}.csv"
    fieldnames = [
        "timestamp", "symbol", "strategy", "direction", "order_type",
        "entry_price", "exit_price", "sl", "tp", "lot",
        "risk_reward", "ticket", "session",
        "pnl", "outcome", "duration_min",
        "account_balance", "account_equity",
        "h4_ema_direction", "range_pips", "atr_pips",
    ]

    with open(trades_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "timestamp":       t.entry_time,
                "symbol":          t.symbol,
                "strategy":        t.strategy,
                "direction":       t.direction,
                "order_type":      t.order_type,
                "entry_price":     t.entry_price,
                "exit_price":      t.exit_price,
                "sl":              t.sl,
                "tp":              t.tp,
                "lot":             t.lot,
                "risk_reward":     t.risk_reward,
                "ticket":          t.ticket,
                "session":         t.session,
                "pnl":             t.pnl,
                "outcome":         t.outcome,
                "duration_min":    t.duration_min,
                "account_balance": t.balance_after,
                "account_equity":  t.equity_after,
                "h4_ema_direction": t.h4_ema_direction,
                "range_pips":      t.range_pips,
                "atr_pips":        t.atr_pips,
            })

    logging.info(f"Trades exported → {trades_path}")

    # Equity curve CSV
    equity_path = f"backtest/results/equity_{symbol}.csv"
    with open(equity_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "balance", "equity"])
        writer.writeheader()
        writer.writerows(equity_log)

    logging.info(f"Equity curve exported → {equity_path}")


# ─────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if MT5_AVAILABLE:
        if not mt5.initialize():
            logging.warning("MT5 init failed — will use cached CSV data only")
        else:
            try:
                from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
                mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
                info = mt5.account_info()
                if info:
                    logging.info(f"MT5 connected — Account: {info.login}")
            except ImportError:
                logging.warning("config.py not found — MT5 connected but not logged in")

    all_trades:     list[ClosedTrade] = []
    combined_equity: list[dict]       = []

    for symbol in SYMBOLS:
        logging.info(f"\n{'─' * 60}")
        logging.info(f" Backtesting {symbol}")
        logging.info(f"{'─' * 60}")

        try:
            m15_candles = fetch_candles(symbol, mt5.TIMEFRAME_M15 if MT5_AVAILABLE else 16408)
            h4_candles  = fetch_candles(symbol, mt5.TIMEFRAME_H4  if MT5_AVAILABLE else 16388)
        except Exception as e:
            logging.error(f"[{symbol}] Failed to load data: {e}")
            continue

        backtester = BraveBacktester(strategy_name=ACTIVE_STRATEGY)
        backtester.run(symbol, m15_candles, h4_candles)

        export_csv(backtester.closed_trades, backtester._daily_equity_log, symbol)
        all_trades.extend(backtester.closed_trades)
        combined_equity.extend(backtester._daily_equity_log)

    # Combined summary across all symbols
    if all_trades:
        # Use a combined account for final summary metrics
        combined_account         = BacktestAccount(balance=STARTING_BALANCE)
        for t in all_trades:
            if t.outcome in ("WIN", "LOSS"):
                combined_account.close_trade(t.pnl)
        combined_account._max_drawdown = max(
            (t.balance_after for t in all_trades),
            default=STARTING_BALANCE
        )
        # Recalculate drawdown properly
        peak   = STARTING_BALANCE
        max_dd = 0.0
        bal    = STARTING_BALANCE
        for t in sorted(all_trades, key=lambda x: x.entry_time):
            if t.outcome in ("WIN", "LOSS"):
                bal += t.pnl
                if bal > peak:
                    peak = bal
                dd = (peak - bal) / peak if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
        combined_account._max_drawdown = max_dd
        combined_account.balance       = STARTING_BALANCE + sum(
            t.pnl for t in all_trades if t.outcome in ("WIN", "LOSS")
        )

        print_summary(all_trades, combined_account)
        export_csv(all_trades, combined_equity, "ALL_SYMBOLS")

    if MT5_AVAILABLE:
        mt5.shutdown()


if __name__ == "__main__":
    main()