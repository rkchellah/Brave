"""
backtest_frost.py — Asian Scalper Backtester

Tests the Frost strategy on historical M15 data.
Follows the same pattern as backtest_thunder.py.

Run from Brave root:
    python backtest/backtest_frost.py

Results saved to:
    backtest/results/frost_results.csv
"""

import sys
import os
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import MetaTrader5 as mt5
import numpy as np

from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
from frost import Frost

# ── Settings ──────────────────────────────────────────────────────────────────

SYMBOLS      = ["GBPUSD", "USDCAD", "EURCHF"]
CANDLES_EACH = 50_000       # M15 candles per symbol (~3.5 months)
RISK_PCT     = 0.01         # 1% risk per trade
ACCOUNT_SIZE = 300.0        # Starting balance

logging.basicConfig(
    level=logging.WARNING,  # Suppress strategy INFO logs during backtest
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── MT5 data fetch ────────────────────────────────────────────────────────────

def fetch_data(symbol: str, count: int) -> list[dict]:
    """Fetch M15 candles with CSV cache."""
    cache_path = Path(f"data/{symbol}_M15_{count}.csv")
    cache_path.parent.mkdir(exist_ok=True)

    if cache_path.exists():
        print(f"  [{symbol}] Loading from cache...")
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

    print(f"  [{symbol}] Fetching {count} M15 candles from MT5...")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, count)
    if rates is None or len(rates) == 0:
        print(f"  [{symbol}] ERROR: No data returned")
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
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(candles)

    print(f"  [{symbol}] Cached {len(candles)} candles")
    return candles


# ── Trade simulator ───────────────────────────────────────────────────────────

class FrostSimulator:
    """
    Simulates trade execution for backtest.

    Rules:
    - Pessimistic: if SL and TP both hit on same candle, SL wins
    - Force-close any open trade at 06:00 UTC (Asian session end)
    - Max hold: MAX_CANDLES_HOLD M15 candles
    """

    MAX_CANDLES_HOLD = 16   # 4 hours on M15

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
            "lot":         lot,
            "open_idx":    candle_idx,
            "open_time":   signal.get("entry_price"),
            "status":      "OPEN",
        }

    def update(self, candle: dict, candle_idx: int) -> dict | None:
        """Check if trade should close on this candle."""
        if self.open_trade is None:
            return None

        t      = self.open_trade
        sl     = t["suggested_sl"]
        tp     = t["suggested_tp"]
        entry  = t["entry_price"]
        lot    = t["lot"]
        direction = t["direction"]

        # Force close at Asian session end (06:00 UTC)
        candle_time = datetime.fromtimestamp(candle["time"], tz=timezone.utc)
        if candle_time.hour >= 6:
            pnl = self._pnl(direction, entry, candle["close"], lot)
            return self._close(t, "FORCE_CLOSE", candle["close"], pnl, candle_idx)

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

        # Pessimistic: SL wins if both hit same candle
        if hit_sl:
            pnl = self._pnl(direction, entry, sl, lot)
            return self._close(t, "SL", sl, pnl, candle_idx)
        if hit_tp:
            pnl = self._pnl(direction, entry, tp, lot)
            return self._close(t, "TP", tp, pnl, candle_idx)

        return None

    def _pnl(self, direction: str, entry: float, exit_price: float, lot: float) -> float:
        if direction == "BUY":
            return (exit_price - entry) * lot * 100_000
        else:
            return (entry - exit_price) * lot * 100_000

    def _close(self, trade: dict, reason: str, exit_price: float, pnl: float, idx: int) -> dict:
        self.balance += pnl
        closed = {
            **trade,
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl":         round(pnl, 2),
            "balance":     round(self.balance, 2),
            "close_idx":   idx,
        }
        self.trades.append(closed)
        self.open_trade = None
        return closed


# ── Backtester ────────────────────────────────────────────────────────────────

class FrostBacktester:
    """
    Time-machine backtester — no lookahead bias.
    Walks through M15 candles and calls strategy.analyze() at each step.
    """

    def __init__(self, symbol: str, candles: list[dict]):
        self.symbol   = symbol
        self.candles  = candles
        self.strategy = Frost(config={"lot_size": 0.01})
        self.sim      = FrostSimulator(ACCOUNT_SIZE, RISK_PCT)

    def run(self) -> list[dict]:
        warmup = max(Frost.MA_PERIOD, Frost.ATR_PERIOD) + 5
        print(f"  [{self.symbol}] Running backtest on {len(self.candles)} candles...")

        for i in range(warmup, len(self.candles)):
            candle = self.candles[i]

            # Update open trade first
            if self.sim.open_trade:
                closed = self.sim.update(candle, i)
                if closed:
                    continue  # Don't open new trade on same candle

            # Only analyze during Asian session (00:00–05:30 UTC)
            candle_time = datetime.fromtimestamp(candle["time"], tz=timezone.utc)
            if not (0 <= candle_time.hour < 6):
                continue
            if candle_time.hour == 5 and candle_time.minute >= 30:
                continue

            # Skip if already in a trade
            if self.sim.open_trade:
                continue

            # Provide historical candles up to current point (no lookahead)
            provided = {15: self.candles[max(0, i-200):i]}

            signal = self.strategy.analyze(self.symbol, provided_rates=provided)

            if signal:
                self.sim.open(signal, i)

        # Force-close any remaining open trade at end
        if self.sim.open_trade:
            last = self.candles[-1]
            pnl  = self.sim._pnl(
                self.sim.open_trade["direction"],
                self.sim.open_trade["entry_price"],
                last["close"],
                self.sim.open_trade["lot"],
            )
            self.sim._close(self.sim.open_trade, "END_OF_DATA", last["close"], pnl, len(self.candles) - 1)

        return self.sim.trades


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_summary(all_trades: list[dict]) -> None:
    if not all_trades:
        print("\n⚠ No trades generated — check ATR/deviation thresholds or data range")
        return

    total       = len(all_trades)
    wins        = [t for t in all_trades if t["pnl"] > 0]
    losses      = [t for t in all_trades if t["pnl"] <= 0]
    tps         = [t for t in all_trades if t["exit_reason"] == "TP"]
    sls         = [t for t in all_trades if t["exit_reason"] == "SL"]
    forces      = [t for t in all_trades if t["exit_reason"] in ("FORCE_CLOSE", "TIMEOUT")]

    win_rate    = len(wins) / total * 100 if total else 0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss   = abs(sum(t["pnl"] for t in losses))
    net_pnl      = sum(t["pnl"] for t in all_trades)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown
    running = ACCOUNT_SIZE
    peak    = ACCOUNT_SIZE
    max_dd  = 0.0
    for t in all_trades:
        running += t["pnl"]
        peak     = max(peak, running)
        dd       = (peak - running) / peak * 100
        max_dd   = max(max_dd, dd)

    total_return = (net_pnl / ACCOUNT_SIZE) * 100

    print("\n" + "═" * 55)
    print("  ASIAN SCALPER BACKTEST RESULTS")
    print("═" * 55)
    print(f"  Total trades      : {total}")
    print(f"  Win rate          : {win_rate:.1f}%")
    print(f"  Profit factor     : {profit_factor:.2f}")
    print(f"  Net P&L           : ${net_pnl:+.2f}")
    print(f"  Total return      : {total_return:+.1f}%")
    print(f"  Max drawdown      : {max_dd:.1f}%")
    print(f"  TP hits           : {len(tps)}")
    print(f"  SL hits           : {len(sls)}")
    print(f"  Force closes      : {len(forces)}")
    print("─" * 55)

    # Per symbol
    symbols = list({t["symbol"] for t in all_trades})
    for sym in sorted(symbols):
        sym_trades = [t for t in all_trades if t["symbol"] == sym]
        sym_pnl    = sum(t["pnl"] for t in sym_trades)
        sym_wins   = len([t for t in sym_trades if t["pnl"] > 0])
        sym_wr     = sym_wins / len(sym_trades) * 100 if sym_trades else 0
        print(f"  {sym:8s} : {len(sym_trades):3d} trades | WR {sym_wr:.0f}% | P&L ${sym_pnl:+.2f}")

    print("═" * 55)


def export_csv(all_trades: list[dict]) -> None:
    Path("backtest/results").mkdir(parents=True, exist_ok=True)
    path = "backtest/results/frost_results.csv"
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
    print("  BRAVE — Asian Scalper Backtest")
    print("=" * 55)

    if not mt5.initialize():
        print("ERROR: MT5 initialization failed")
        return

    authorized = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if not authorized:
        print(f"ERROR: MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return

    print(f"  MT5 connected — running on {SYMBOLS}")

    all_trades: list[dict] = []

    for symbol in SYMBOLS:
        candles = fetch_data(symbol, CANDLES_EACH)
        if not candles:
            print(f"  [{symbol}] Skipping — no data")
            continue

        bt     = FrostBacktester(symbol, candles)
        trades = bt.run()
        all_trades.extend(trades)
        print(f"  [{symbol}] {len(trades)} trades generated")

    mt5.shutdown()

    print_summary(all_trades)
    export_csv(all_trades)


if __name__ == "__main__":
    main()
