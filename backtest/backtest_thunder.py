"""
backtest_thunder.py — Thunder Strategy Backtester
======================================================

Thunder: EMA 8/13/21 Stack Scalper
- Trend filter: H4 EMA stack alignment
- Entry:        M15 pending stop above/below 5-candle range
- Buffer:       ATR-scaled (adapts to Gold/Indices vs FX)
- Session:      London (08:00–11:00) + NY (13:00–16:00) UTC
- Risk:         Fixed 1% per trade
- RR:           Minimum 2:1

Run from terminal:
    python backtest/backtest_thunder.py

Results saved to:
    backtest/backtest_thunder_trades.csv
    backtest/backtest_thunder.log
"""

import MetaTrader5 as mt5
import numpy as np
import csv
import os
import sys
import logging
from datetime import datetime, timezone, time as dt_time

# We insert the project root and src folder to allow importing from both levels.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))

from thunder import Thunder

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "XAUUSD",
    "US30",
    "NAS100",
]

# Import credentials from config — never hardcode
try:
    from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
except ImportError:
    raise RuntimeError(
        "config.py not found. Copy config.example.py to config.py and fill in your credentials."
    )

# Data
H4_CANDLES_TOTAL  = 10_000
CHUNK_SIZE        = 10_000
H4_LOOKBACK       = 60      # Candles fed to Thunder for H4 EMA calculation
M15_LOOKBACK      = 100     # Candles fed to Thunder for M15 range detection

# Trade simulation
INITIAL_BALANCE   = 100.0
RISK_PERCENT      = 0.01    # 1% risk per trade
MAX_CANDLES_WAIT  = 100     # Max M15 candles before timing out a pending order
MAX_OUTCOME_WAIT  = 200     # Max M15 candles to wait for TP/SL after entry

# Session windows (UTC) — for logging/reporting only
LONDON_OPEN  = dt_time(8,  0)
LONDON_CLOSE = dt_time(11, 0)
NY_OPEN      = dt_time(13, 0)
NY_CLOSE     = dt_time(16, 0)

# Pip sizes per instrument
POINT_SIZES = {
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "USDJPY": 0.001,
    "XAUUSD": 0.01,
    "US30":   0.01,
    "NAS100": 0.01,
}

# Paths
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_DATA_DIR     = os.path.join(_PROJECT_ROOT, "data")

# Organizing output in a dedicated results folder for cleaner project structure.
RESULTS_DIR    = os.path.join(_SCRIPT_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)
TRADE_LOG_FILE = os.path.join(RESULTS_DIR, "backtest_thunder_trades.csv")

# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(_SCRIPT_DIR, "backtest_thunder.log"),
            mode="w",
            encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# SECTION 1 — DATA FETCHING (CHUNKED + CACHED)
# ═══════════════════════════════════════════════════════════════════════

def fetch_data(symbol: str, timeframe: int, total_candles: int) -> list[dict]:
    """
    Fetch candles in 10k chunks, cache to CSV so you don't re-fetch
    every run. Delete the CSV in /data to force a fresh fetch.
    """
    tf_labels = {
        mt5.TIMEFRAME_M15: "m15",
        mt5.TIMEFRAME_H1:  "h1",
        mt5.TIMEFRAME_H4:  "h4",
    }
    tf_str     = tf_labels.get(timeframe, "unknown")
    cache_file = os.path.join(_DATA_DIR, f"{symbol.lower()}_{tf_str}_{total_candles}.csv")

    # ── Load from cache if available ─────────────────────────────
    if os.path.exists(cache_file):
        log.info(f"   [{symbol}] Loading {tf_str.upper()} from cache...")
        with open(cache_file, "r") as f:
            reader = csv.DictReader(f)
            candles = [
                {
                    "time":   int(row["time"]),
                    "open":   float(row["open"]),
                    "high":   float(row["high"]),
                    "low":    float(row["low"]),
                    "close":  float(row["close"]),
                    "volume": int(row["volume"]),
                }
                for row in reader
            ]
        log.info(f"   [{symbol}] Loaded {len(candles):,} candles from cache")
        return candles

    # ── Fetch from MT5 ────────────────────────────────────────────
    log.info(f"   [{symbol}] Fetching {tf_str.upper()} from MT5 ({total_candles:,} candles)...")

    if not mt5.initialize():
        raise RuntimeError("MT5 initialization failed")
    if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

    all_candles = []
    fetched     = 0

    while fetched < total_candles:
        chunk = min(CHUNK_SIZE, total_candles - fetched)
        rates = mt5.copy_rates_from_pos(symbol, timeframe, fetched, chunk)

        if rates is None or len(rates) == 0:
            log.warning(f"   [{symbol}] No data at position {fetched} — stopping")
            break

        all_candles.extend(list(rates))
        fetched += len(rates)
        log.info(f"   [{symbol}] Fetched {fetched:,}/{total_candles:,} candles")

        if len(rates) < chunk:
            break  # Broker returned less than requested — no more data

    mt5.shutdown()

    if not all_candles:
        raise RuntimeError(f"No data received for {symbol} {tf_str.upper()}")

    candles = [
        {
            "time":   int(r["time"]),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": int(r["tick_volume"]),
        }
        for r in all_candles
    ]

    # ── Cache to disk ─────────────────────────────────────────────
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(cache_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time","open","high","low","close","volume"])
        writer.writeheader()
        writer.writerows(candles)
    log.info(f"   [{symbol}] Cached {len(candles):,} candles → {cache_file}")

    return candles


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2 — TIME MACHINE SYNCHRONIZATION
# ═══════════════════════════════════════════════════════════════════════

def get_h4_context(
    current_m15_time: int,
    h4_candles: list[dict],
    lookback: int = 60
) -> list[dict] | None:
    # We use binary search to locate H4 context efficiently within large candle datasets
    # while strictly avoiding any lookahead bias by only choosing candles before the M15 time.
    lo, hi, idx = 0, len(h4_candles) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if h4_candles[mid]["time"] < current_m15_time:
            idx = mid
            lo  = mid + 1
        else:
            hi  = mid - 1

    if idx < lookback - 1:
        return None  # Not enough H4 history yet

    return h4_candles[idx - lookback + 1 : idx + 1]


def get_session(timestamp: int) -> str:
    """Return session label for a given UTC timestamp"""
    t = datetime.fromtimestamp(timestamp, tz=timezone.utc).time()
    if LONDON_OPEN <= t < LONDON_CLOSE:
        return "LONDON"
    if NY_OPEN <= t < NY_CLOSE:
        return "NY"
    return "OFF_HOURS"


def is_active_session(timestamp: int) -> bool:
    """True if timestamp falls within London or NY session"""
    t = datetime.fromtimestamp(timestamp, tz=timezone.utc).time()
    return (LONDON_OPEN <= t < LONDON_CLOSE or NY_OPEN <= t < NY_CLOSE)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3 — TRADE SIMULATOR
# ═══════════════════════════════════════════════════════════════════════

class ThunderTradeSimulator:
    """
    Simulates Thunder trades with:
    - Pending STOP orders (entry only triggered when price crosses entry level)
    - Fixed 1% risk position sizing
    - Pessimistic execution (if SL and TP both hit same candle → SL wins)
    - Pending order expiry (cancel if not triggered within MAX_CANDLES_WAIT)
    """

    def __init__(self, symbol: str, initial_balance: float, point_size: float):
        self.symbol        = symbol
        self.balance       = initial_balance
        self.point_size    = point_size
        self.closed_trades: list[dict] = []

        self.state         = "IDLE"     # IDLE | PENDING | ACTIVE
        self._direction    = None
        self._entry_price  = None
        self._sl           = None
        self._tp           = None
        self._lot_size     = None
        self._order_index  = None       # When pending was placed
        self._entry_index  = None       # When entry was triggered
        self._entry_time   = None
        self._order_time   = None

    # ── Place pending order ───────────────────────────────────────

    def place_pending(
        self,
        direction: str,
        entry_price: float,
        sl: float,
        tp: float,
        order_index: int,
        order_time: int
    ) -> None:
        if self.state != "IDLE":
            return

        sl_pips = max(abs(entry_price - sl) / self.point_size, 0.1)
        risk_amount = self.balance * RISK_PERCENT

        # $0.10 per pip per 0.01 lot (standard calculation)
        self._lot_size = risk_amount / (sl_pips * 0.10)
        self._lot_size = max(0.01, min(self._lot_size, 100.0))

        self._direction   = direction
        self._entry_price = entry_price
        self._sl          = sl
        self._tp          = tp
        self._order_index = order_index
        self._order_time  = order_time

        self.state = "PENDING"
        log.info(
            f"   [{self.symbol}] Thunder: PENDING {direction} STOP @ {entry_price:.5f} | "
            f"SL: {sl:.5f} | TP: {tp:.5f} | Lot: {self._lot_size:.2f} | Session: {get_session(order_time)}"
        )

    # ── Step through each candle ──────────────────────────────────

    def step(self, candle: dict, candle_index: int) -> None:
        if self.state == "IDLE":
            return

        if self.state == "PENDING":
            self._check_pending_trigger(candle, candle_index)
            return

        if self.state == "ACTIVE":
            self._check_outcome(candle, candle_index)

    def _check_pending_trigger(self, candle: dict, candle_index: int) -> None:
        """Check if the pending stop order gets triggered this candle"""

        # Expire if waiting too long
        if candle_index - self._order_index > MAX_CANDLES_WAIT:
            log.info(f"   [{self.symbol}] Thunder: Pending order EXPIRED (waited {MAX_CANDLES_WAIT} candles)")
            self._record_expired(candle_index, candle["time"])
            return

        triggered = False

        if self._direction == "BUY" and candle["high"] >= self._entry_price:
            triggered = True
        elif self._direction == "SELL" and candle["low"] <= self._entry_price:
            triggered = True

        if triggered:
            self._entry_index = candle_index
            self._entry_time  = candle["time"]
            self.state        = "ACTIVE"
            log.info(
                f"   [{self.symbol}] Thunder: Order TRIGGERED {self._direction} @ {self._entry_price:.5f} | "
                f"Session: {get_session(candle['time'])}"
            )
            # Immediately check outcome on the same candle
            self._check_outcome(candle, candle_index)

    def _check_outcome(self, candle: dict, candle_index: int) -> None:
        """Check if TP or SL is hit on this candle"""

        # Timeout safety
        if candle_index - self._entry_index > MAX_OUTCOME_WAIT:
            self._close(candle_index, candle["time"], "TIMEOUT",
                        self._calc_pnl(candle["close"]), candle["close"])
            return

        sl_hit = (self._direction == "BUY"  and candle["low"]  <= self._sl) or \
                 (self._direction == "SELL" and candle["high"] >= self._sl)

        tp_hit = (self._direction == "BUY"  and candle["high"] >= self._tp) or \
                 (self._direction == "SELL" and candle["low"]  <= self._tp)

        if sl_hit and tp_hit:
            # We assume SL is hit first to maintain a conservative/pessimistic backtest result.
            self._close(candle_index, candle["time"], "SL_PESSIMISTIC",
                        self._calc_pnl(self._sl), self._sl)
        elif sl_hit:
            self._close(candle_index, candle["time"], "SL_HIT",
                        self._calc_pnl(self._sl), self._sl)
        elif tp_hit:
            self._close(candle_index, candle["time"], "TP_HIT",
                        self._calc_pnl(self._tp), self._tp)

    def _calc_pnl(self, exit_price: float) -> float:
        pips = (exit_price - self._entry_price) / self.point_size
        if self._direction == "SELL":
            pips = -pips
        return pips * self._lot_size * 0.10

    def _close(self, close_index: int, close_time: int,
               outcome: str, pnl: float, exit_price: float) -> None:
        self.balance += pnl
        record = {
            "symbol":         self.symbol,
            "direction":      self._direction,
            "order_time":     datetime.fromtimestamp(self._order_time).strftime("%Y-%m-%d %H:%M"),
            "entry_time":     datetime.fromtimestamp(self._entry_time).strftime("%Y-%m-%d %H:%M"),
            "exit_time":      datetime.fromtimestamp(close_time).strftime("%Y-%m-%d %H:%M"),
            "entry_price":    self._entry_price,
            "exit_price":     exit_price,
            "sl":             self._sl,
            "tp":             self._tp,
            "lot_size":       self._lot_size,
            "outcome":        outcome,
            "pnl":            round(pnl, 2),
            "balance":        round(self.balance, 2),
            "duration_candles": close_index - self._entry_index,
            "entry_session":  get_session(self._entry_time),
            "exit_session":   get_session(close_time),
        }
        self.closed_trades.append(record)
        self.state = "IDLE"
        log.info(
            f"   [{self.symbol}] Thunder: {outcome} | P&L: ${pnl:+.2f} | "
            f"Balance: ${self.balance:.2f} | Duration: {close_index - self._entry_index} candles"
        )

    def _record_expired(self, candle_index: int, candle_time: int) -> None:
        record = {
            "symbol":         self.symbol,
            "direction":      self._direction,
            "order_time":     datetime.fromtimestamp(self._order_time).strftime("%Y-%m-%d %H:%M"),
            "entry_time":     "NEVER",
            "exit_time":      datetime.fromtimestamp(candle_time).strftime("%Y-%m-%d %H:%M"),
            "entry_price":    self._entry_price,
            "exit_price":     None,
            "sl":             self._sl,
            "tp":             self._tp,
            "lot_size":       self._lot_size,
            "outcome":        "EXPIRED",
            "pnl":            0.0,
            "balance":        round(self.balance, 2),
            "duration_candles": candle_index - self._order_index,
            "entry_session":  get_session(self._order_time),
            "exit_session":   "N/A",
        }
        self.closed_trades.append(record)
        self.state = "IDLE"

    def is_idle(self) -> bool:
        return self.state == "IDLE"


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4 — BACKTESTER
# ═══════════════════════════════════════════════════════════════════════

class ThunderBacktester:

    def __init__(self, symbol: str):
        self.symbol    = symbol
        self.strategy  = Thunder({"lot_size": 0.01})
        self.simulator = ThunderTradeSimulator(
            symbol          = symbol,
            initial_balance = INITIAL_BALANCE,
            point_size      = POINT_SIZES.get(symbol, 0.00001),
        )

    def run(self, h4_candles: list[dict], m15_candles: list[dict]) -> list[dict]:
        log.info(f"\n{'='*70}")
        log.info(f"[{self.symbol}] Thunder Backtest Starting")
        log.info(f"{'='*70}")
        log.info(f"   H4 candles:  {len(h4_candles):,}")
        log.info(f"   M15 candles: {len(m15_candles):,}")

        signals_found = 0
        start_index   = M15_LOOKBACK  # Need enough M15 context first

        for i in range(start_index, len(m15_candles)):
            current = m15_candles[i]

            # Tick the simulator first (check TP/SL/expiry on each candle)
            self.simulator.step(current, i)

            # Skip if we already have a trade on (one position at a time)
            if not self.simulator.is_idle():
                continue

            # Session filter
            if not is_active_session(current["time"]):
                continue

            # Build time-machine H4 context
            h4_window = get_h4_context(current["time"], h4_candles, H4_LOOKBACK)
            if h4_window is None:
                continue

            # Build M15 window (last M15_LOOKBACK candles up to but not including current)
            m15_window = m15_candles[max(0, i - M15_LOOKBACK) : i]

            # Run Thunder analysis
            provided_rates = {
                Thunder.TF_H4:  h4_window,
                Thunder.TF_M15: m15_window,
            }

            signal = self.strategy.analyze(self.symbol, provided_rates)

            if signal is None:
                continue

            # Signal confirmed — place pending stop order
            signals_found += 1
            session = get_session(current["time"])
            log.info(
                f"\n[{self.symbol}] Signal #{signals_found} at M15 index {i} | "
                f"{datetime.fromtimestamp(current['time'])} | {session}"
            )

            self.simulator.place_pending(
                direction   = signal["direction"],
                entry_price = signal["entry_price"],
                sl          = signal["suggested_sl"],
                tp          = signal["suggested_tp"],
                order_index = i,
                order_time  = current["time"],
            )

        log.info(f"\n[{self.symbol}] Backtest complete")
        log.info(f"   Signals found:  {signals_found}")
        log.info(f"   Trades closed:  {len(self.simulator.closed_trades)}")
        log.info(f"   Final balance:  ${self.simulator.balance:.2f}")

        return self.simulator.closed_trades


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5 — REPORTING
# ═══════════════════════════════════════════════════════════════════════

def print_summary(all_trades: list[dict]) -> None:
    if not all_trades:
        log.info("\nNo trades executed.")
        return

    log.info(f"\n{'='*70}")
    log.info("THUNDER STRATEGY — BACKTEST RESULTS")
    log.info(f"{'='*70}")

    total  = len(all_trades)
    wins   = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] < 0]
    expired = [t for t in all_trades if t["outcome"] == "EXPIRED"]
    timeouts = [t for t in all_trades if t["outcome"] == "TIMEOUT"]

    win_rate  = len(wins) / total * 100 if total else 0
    total_pnl = sum(t["pnl"] for t in all_trades)
    avg_win   = np.mean([t["pnl"] for t in wins])   if wins   else 0
    avg_loss  = np.mean([t["pnl"] for t in losses]) if losses else 0
    pf        = abs(sum(t["pnl"] for t in wins)) / abs(sum(t["pnl"] for t in losses)) \
                if losses and sum(t["pnl"] for t in losses) != 0 else float("inf")

    log.info(f"\n Overall:")
    log.info(f"   Total trades:    {total}")
    log.info(f"   Wins:            {len(wins)} ({win_rate:.1f}%)")
    log.info(f"   Losses:          {len(losses)}")
    log.info(f"   Expired orders:  {len(expired)}")
    log.info(f"   Timeouts:        {len(timeouts)}")
    log.info(f"   Total P&L:       ${total_pnl:+,.2f}")
    log.info(f"   Avg win:         ${avg_win:+.2f}")
    log.info(f"   Avg loss:        ${avg_loss:+.2f}")
    log.info(f"   Profit factor:   {pf:.2f}")

    # Per-symbol breakdown
    symbols = sorted(set(t["symbol"] for t in all_trades))
    log.info(f"\n Per-symbol breakdown:")
    for sym in symbols:
        sym_trades = [t for t in all_trades if t["symbol"] == sym]
        sym_wins   = [t for t in sym_trades if t["pnl"] > 0]
        sym_pnl    = sum(t["pnl"] for t in sym_trades)
        sym_wr     = len(sym_wins) / len(sym_trades) * 100 if sym_trades else 0
        log.info(
            f"   {sym:<8}: {len(sym_trades):>3} trades | "
            f"{len(sym_wins):>3} wins ({sym_wr:5.1f}%) | "
            f"P&L: ${sym_pnl:+.2f}"
        )

    # Per-session breakdown
    log.info(f"\n Session breakdown:")
    for session in ["LONDON", "NY", "OFF_HOURS"]:
        sess_trades = [t for t in all_trades if t.get("entry_session") == session]
        if not sess_trades:
            continue
        sess_wins = [t for t in sess_trades if t["pnl"] > 0]
        sess_pnl  = sum(t["pnl"] for t in sess_trades)
        sess_wr   = len(sess_wins) / len(sess_trades) * 100
        log.info(
            f"   {session:<10}: {len(sess_trades):>3} trades | "
            f"{len(sess_wins):>3} wins ({sess_wr:5.1f}%) | "
            f"P&L: ${sess_pnl:+.2f}"
        )

    # Equity / drawdown
    num_symbols   = len(symbols)
    initial_total = INITIAL_BALANCE * num_symbols
    balance       = initial_total
    peak          = initial_total
    max_dd        = 0.0

    for t in all_trades:
        balance += t["pnl"]
        peak     = max(peak, balance)
        dd       = (peak - balance) / peak * 100
        max_dd   = max(max_dd, dd)

    total_return = (balance - initial_total) / initial_total * 100

    log.info(f"\n Equity:")
    log.info(f"   Initial balance: ${initial_total:,.2f}")
    log.info(f"   Final balance:   ${balance:,.2f}")
    log.info(f"   Total return:    {total_return:+.2f}%")
    log.info(f"   Max drawdown:    {max_dd:.2f}%")
    log.info(f"{'='*70}\n")


def export_csv(trades: list[dict], path: str) -> None:
    if not trades:
        return
    fieldnames = [
        "symbol", "direction", "order_time", "entry_time", "exit_time",
        "entry_price", "exit_price", "sl", "tp", "lot_size",
        "outcome", "pnl", "balance", "duration_candles",
        "entry_session", "exit_session",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)
    log.info(f"Trades exported → {path}")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    log.info("=" * 70)
    log.info("THUNDER STRATEGY BACKTEST")
    log.info("=" * 70)
    log.info(f"Symbols:  {', '.join(SYMBOLS)}")
    log.info(f"Session:  London (08:00–11:00) + NY (13:00–16:00) UTC")
    log.info(f"Risk:     1% per trade | Min RR: 2:1")
    log.info("=" * 70)

    all_trades: list[dict] = []

    for symbol in SYMBOLS:
        log.info(f"\n[{symbol}] Fetching data...")

        try:
            h4_candles = fetch_data(symbol, mt5.TIMEFRAME_H4,  H4_CANDLES_TOTAL)

            # Calculate M15 count to cover the same date range
            h4_start = h4_candles[0]["time"]
            h4_end   = h4_candles[-1]["time"]
            span     = h4_end - h4_start
            m15_count = int(span / 900) + 500  # 900s = 15 min, +500 buffer

            log.info(
                f"[{symbol}] Date range: "
                f"{datetime.fromtimestamp(h4_start)} → {datetime.fromtimestamp(h4_end)}"
            )
            log.info(f"[{symbol}] Fetching {m15_count:,} M15 candles...")

            m15_candles = fetch_data(symbol, mt5.TIMEFRAME_M15, m15_count)

            backtester = ThunderBacktester(symbol)
            trades     = backtester.run(h4_candles, m15_candles)
            all_trades.extend(trades)

        except Exception as e:
            log.error(f"[{symbol}] Error: {e}")
            import traceback
            traceback.print_exc()
            continue

    print_summary(all_trades)
    export_csv(all_trades, TRADE_LOG_FILE)
    log.info("Done.")


if __name__ == "__main__":
    main()