"""
backtest_flow.py — Flow Scalper Backtester

Tests the Flow multi-session scalper on historical H1 + M15 data.

Run from Brave root:
    python backtest/backtest_flow.py

Results saved to:
    backtest/results/flow_results.csv
"""

import sys
import os
import csv
import logging
from datetime import datetime, timezone, time
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import MetaTrader5 as mt5
import numpy as np

from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
from flow import Flow

# ── Settings ──────────────────────────────────────────────────────────────────

SYMBOLS      = ["EURUSD", "GBPUSD"]
CANDLES_M15  = 50_000
CANDLES_H1   = 15_000
RISK_PCT     = 0.01
ACCOUNT_SIZE = 300.0

# Flow active session hours (UTC) — must match flow.py
ACTIVE_HOURS = [
    (6, 8),    # Pre-London
    (8, 11),   # London
    (11, 13),  # Bridge
    (13, 16),  # New York
]

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── Data fetch ────────────────────────────────────────────────────────────────

def fetch_data(symbol: str, timeframe: int, count: int) -> list[dict]:
    tf_name    = "H1" if timeframe == mt5.TIMEFRAME_H1 else "M15"
    cache_path = Path(f"data/{symbol}_{tf_name}_{count}.csv")
    cache_path.parent.mkdir(exist_ok=True)

    if cache_path.exists():
        print(f"  [{symbol}] {tf_name} loading from cache...")
        candles = []
        with open(cache_path) as f:
            for row in csv.DictReader(f):
                candles.append({
                    "time":   int(row["time"]),
                    "open":   float(row["open"]),
                    "high":   float(row["high"]),
                    "low":    float(row["low"]),
                    "close":  float(row["close"]),
                    "volume": int(row["volume"]),
                })
        return candles

    print(f"  [{symbol}] Fetching {count} {tf_name} candles from MT5...")
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        print(f"  [{symbol}] ERROR: No {tf_name} data returned")
        return []

    candles = [
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

    with open(cache_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time","open","high","low","close","volume"])
        writer.writeheader()
        writer.writerows(candles)

    print(f"  [{symbol}] Cached {len(candles)} {tf_name} candles")
    return candles


def get_h1_context(h1_candles: list[dict], m15_time: int, lookback: int = 60) -> list[dict] | None:
    """
    Binary search — find H1 candles up to m15_time (no lookahead).
    Returns last `lookback` H1 candles before the M15 candle time.
    """
    lo, hi = 0, len(h1_candles) - 1
    idx = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if h1_candles[mid]["time"] <= m15_time:
            idx = mid
            lo  = mid + 1
        else:
            hi  = mid - 1

    if idx < lookback:
        return None
    return h1_candles[max(0, idx - lookback + 1): idx + 1]


# ── Trade simulator ───────────────────────────────────────────────────────────

class FlowSimulator:
    MAX_CANDLES_HOLD = 32  # 8 hours on M15 — force close if not hit

    def __init__(self, account_size: float, risk_pct: float):
        self.balance    = account_size
        self.risk_pct   = risk_pct
        self.trades     = []
        self.open_trade = None

    def open(self, signal: dict, candle_idx: int) -> None:
        risk_amount = self.balance * self.risk_pct
        risk_price  = abs(signal["entry_price"] - signal["suggested_sl"])
        if risk_price == 0:
            return

        lot = risk_amount / (risk_price * 100_000)
        lot = max(0.01, round(lot, 2))

        self.open_trade = {
            **signal,
            "lot":      lot,
            "open_idx": candle_idx,
            "status":   "OPEN",
        }

    def update(self, candle: dict, candle_idx: int) -> dict | None:
        if self.open_trade is None:
            return None

        t         = self.open_trade
        sl        = t["suggested_sl"]
        tp        = t["suggested_tp"]
        entry     = t["entry_price"]
        lot       = t["lot"]
        direction = t["direction"]

        # Force close after max hold
        if candle_idx - t["open_idx"] >= self.MAX_CANDLES_HOLD:
            pnl = self._pnl(direction, entry, candle["close"], lot)
            return self._close(t, "TIMEOUT", candle["close"], pnl, candle_idx)

        if direction == "BUY":
            hit_sl = candle["low"]  <= sl
            hit_tp = candle["high"] >= tp
        else:
            hit_sl = candle["high"] >= sl
            hit_tp = candle["low"]  <= tp

        if hit_sl:
            pnl = self._pnl(direction, entry, sl, lot)
            return self._close(t, "SL", sl, pnl, candle_idx)
        if hit_tp:
            pnl = self._pnl(direction, entry, tp, lot)
            return self._close(t, "TP", tp, pnl, candle_idx)

        return None

    def _pnl(self, direction: str, entry: float, exit_price: float, lot: float) -> float:
        mult = 1 if direction == "BUY" else -1
        return mult * (exit_price - entry) * lot * 100_000

    def _close(self, trade: dict, reason: str, exit_price: float, pnl: float, idx: int) -> dict:
        self.balance += pnl
        closed = {
            **trade,
            "exit_price":  exit_price,
            "exit_reason": reason,
            "pnl":         round(pnl, 2),
            "balance":     round(self.balance, 2),
            "close_idx":   idx,
        }
        self.trades.append(closed)
        self.open_trade = None
        return closed


# ── Backtester ────────────────────────────────────────────────────────────────

class FlowBacktester:
    def __init__(self, symbol: str, m15_candles: list[dict], h1_candles: list[dict]):
        self.symbol      = symbol
        self.m15_candles = m15_candles
        self.h1_candles  = h1_candles
        self.strategy    = Flow(config={"lot_size": 0.01})
        self.sim         = FlowSimulator(ACCOUNT_SIZE, RISK_PCT)

    def _in_session(self, candle_time: datetime) -> bool:
        h = candle_time.hour
        m = candle_time.minute
        t = time(h, m)
        return any(time(s, 0) <= t < time(e, 0) for s, e in ACTIVE_HOURS)

    def run(self) -> list[dict]:
        warmup = 200
        print(f"  [{self.symbol}] Running Flow backtest on {len(self.m15_candles)} M15 candles...")

        for i in range(warmup, len(self.m15_candles)):
            candle      = self.m15_candles[i]
            candle_time = datetime.fromtimestamp(candle["time"], tz=timezone.utc)

            # Update open trade
            if self.sim.open_trade:
                self.sim.update(candle, i)
                continue

            # Session filter
            if not self._in_session(candle_time):
                continue

            # Get H1 context (no lookahead)
            h1_ctx = get_h1_context(self.h1_candles, candle["time"], lookback=60)
            if h1_ctx is None:
                continue

            # Build provided_rates
            m15_window = self.m15_candles[max(0, i - 200):i]
            provided   = {
                mt5.TIMEFRAME_H1:  h1_ctx,
                mt5.TIMEFRAME_M15: m15_window,
            }

            signal = self.strategy.analyze(self.symbol, provided_rates=provided)

            if signal:
                self.sim.open(signal, i)

        # Force-close any remaining trade
        if self.sim.open_trade:
            last = self.m15_candles[-1]
            pnl  = self.sim._pnl(
                self.sim.open_trade["direction"],
                self.sim.open_trade["entry_price"],
                last["close"],
                self.sim.open_trade["lot"],
            )
            self.sim._close(self.sim.open_trade, "END", last["close"], pnl, len(self.m15_candles)-1)

        return self.sim.trades


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_summary(all_trades: list[dict]) -> None:
    if not all_trades:
        print("\n⚠ No trades generated")
        return

    total  = len(all_trades)
    wins   = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] <= 0]
    tps    = [t for t in all_trades if t["exit_reason"] == "TP"]
    sls    = [t for t in all_trades if t["exit_reason"] == "SL"]
    outros = [t for t in all_trades if t["exit_reason"] not in ("TP", "SL")]

    win_rate     = len(wins) / total * 100
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss   = abs(sum(t["pnl"] for t in losses))
    net_pnl      = sum(t["pnl"] for t in all_trades)
    pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    running = ACCOUNT_SIZE
    peak    = ACCOUNT_SIZE
    max_dd  = 0.0
    for t in all_trades:
        running += t["pnl"]
        peak     = max(peak, running)
        dd       = (peak - running) / peak * 100
        max_dd   = max(max_dd, dd)

    total_return = net_pnl / ACCOUNT_SIZE * 100

    print("\n" + "═" * 55)
    print("  FLOW SCALPER BACKTEST RESULTS")
    print("═" * 55)
    print(f"  Total trades      : {total}")
    print(f"  Win rate          : {win_rate:.1f}%")
    print(f"  Profit factor     : {pf:.2f}")
    print(f"  Net P&L           : ${net_pnl:+.2f}")
    print(f"  Total return      : {total_return:+.1f}%")
    print(f"  Max drawdown      : {max_dd:.1f}%")
    print(f"  TP hits           : {len(tps)}")
    print(f"  SL hits           : {len(sls)}")
    print(f"  Timeouts          : {len(outros)}")
    print("─" * 55)

    symbols = sorted({t["symbol"] for t in all_trades})
    for sym in symbols:
        st  = [t for t in all_trades if t["symbol"] == sym]
        pnl = sum(t["pnl"] for t in st)
        wr  = len([t for t in st if t["pnl"] > 0]) / len(st) * 100
        print(f"  {sym:8s} : {len(st):3d} trades | WR {wr:.0f}% | P&L ${pnl:+.2f}")

    print("═" * 55)


def export_csv(all_trades: list[dict]) -> None:
    Path("backtest/results").mkdir(parents=True, exist_ok=True)
    path = "backtest/results/flow_results.csv"
    if not all_trades:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_trades[0].keys())
        writer.writeheader()
        writer.writerows(all_trades)
    print(f"\n  Results saved → {path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 55)
    print("  BRAVE — Flow Scalper Backtest")
    print("=" * 55)

    if not mt5.initialize():
        print("ERROR: MT5 init failed")
        return

    if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        print(f"ERROR: MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return

    print(f"  MT5 connected — symbols: {SYMBOLS}")

    all_trades: list[dict] = []

    for symbol in SYMBOLS:
        m15 = fetch_data(symbol, mt5.TIMEFRAME_M15, CANDLES_M15)
        h1  = fetch_data(symbol, mt5.TIMEFRAME_H1,  CANDLES_H1)

        if not m15 or not h1:
            print(f"  [{symbol}] Skipping — insufficient data")
            continue

        bt     = FlowBacktester(symbol, m15, h1)
        trades = bt.run()
        all_trades.extend(trades)
        print(f"  [{symbol}] {len(trades)} trades generated")

    mt5.shutdown()
    print_summary(all_trades)
    export_csv(all_trades)


if __name__ == "__main__":
    main()
