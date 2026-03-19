# Brave — Architecture Document
> For AI agents and developers. Read this before touching any file.

---

## What Brave Is

Brave is a mobile-controlled automated trading bot for MetaTrader 5.
Think of it like a VPN app — the user picks which strategy is active from their phone,
and the bot executes that strategy automatically on their MT5 account.

The bot runs on a local machine or VPS with MT5 installed.
Firebase is the bridge between the mobile app and the bot.
The mobile app never touches MT5 directly — it only reads/writes Firebase.

---

## Project Rules

- Python only. Type hints enforced.
- No credentials ever hardcoded — always imported from `config.py`
- Comments only when necessary — explain *why*, never *what*
- Every trade action must be logged
- No strategy runs without defined SL, TP, and position sizing
- Update `README.md` when code changes
- Update `CHECKLIST.md` when tasks complete

---

## Repository Structure

```
Brave/
├── src/                      # Source code
│   ├── bot.py                # Main bot — BraveBot class (v2.0)
│   ├── thunder.py            # Thunder strategy — EMA Stack Scalper
│   ├── flow.py               # Flow strategy — Trend Continuation
│   ├── news_filter.py        # News filtering logic
│   ├── sentiment_service.py  # AI Sentiment Analysis (GPT-4 + Grok)
│   ├── manage_config.py      # CLI utility: update Firebase config
│   ├── send_command.py       # CLI utility: start/stop bot
│   └── setup_firebase.py     # One-time Firebase setup script
├── backtest/                 # Backtesting engine
│   ├── results/              # Generated — backtest CSV results
│   ├── backtest_thunder.py   # Thunder backtester
│   └── backtest_thunder.log  # Generated — backtest log
├── config.py                 # Credentials + settings (NOT in repo)
├── config.example.py         # Config template (safe to commit)
├── serviceAccountKey.json    # Firebase service account (NOT in repo)
├── data/                     # Generated — cached candle CSVs (ignored)
├── logs/                     # Generated — daily bot logs (ignored)
├── ARCHITECTURE.md           # This document
├── RULES.md                  # Detailed project rules
├── CHECKLIST.md              # Feature and maintenance tracking
└── README.md                 # User guide and setup
```

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.9+ |
| Trading | MetaTrader5 Python library |
| Database | Firebase Realtime Database (`firebase-admin`) |
| Math | NumPy |
| Remote control | Firebase command queue |
| Secrets | `config.py` + `serviceAccountKey.json` (never committed) |
| Deployment | Local machine or VPS running MT5 terminal |
| Mobile App | React Native + Expo SDK 52 |
| Mobile Database | @react-native-firebase/database |
| Mobile Navigation | @react-navigation/bottom-tabs |
| Push Notifications | expo-notifications + Firebase Cloud Messaging |
| Mobile Dev Tool | Expo Go (Android) |

---

## Core Concept: Strategy Registry

`src/bot.py` has a `STRATEGY_REGISTRY` dict that maps strategy names to classes:

```python
STRATEGY_REGISTRY = {
    "thunder": Thunder,
    "flow": Flow,
}
```

Every loop cycle, `BraveBot._load_active_strategy()` reads
`users/{USER_ID}/brave_config/active_strategy` from Firebase.
If it changed since last loop, it hot-swaps the strategy instance — no restart needed.

**To add a new strategy:**
1. Create `src/newstrategy.py` with a class that has an `analyze(symbol, provided_rates) -> dict | None` method
2. Import it at the top of `src/bot.py`
3. Add `"newstrategy": NewStrategy` to `STRATEGY_REGISTRY`
4. Mobile app can now switch to it by writing `"newstrategy"` to Firebase

---

## Firebase Data Structure

```
users/
  {USER_ID}/
    brave_config/           ← Mobile app reads/writes this
      active_strategy       string  e.g. "thunder"
      available_strategies  array   e.g. ["thunder"]
      last_switched         string  ISO timestamp
      switched_by           string  "mobile" | "manual"

    bot_config/             ← Trading parameters
      lot_size              float   e.g. 0.1
      max_trades            int     e.g. 3
      stop_loss_pips        int     e.g. 50
      take_profit_pips      int     e.g. 100
      timeframe             string  e.g. "M15"
      symbols               array   e.g. ["EURUSD", "GBPUSD", "XAUUSD"]

    bot_status/             ← Bot writes this — mobile reads it
      is_running            bool
      active_strategy       string
      balance               float
      equity                float
      profit                float
      open_positions        int
      market_open           bool
      open_markets          array
      markets_analyzed      array
      trading_active        bool
      paused_reason         string  "DAILY_LOSS_LIMIT" | null
      pnl_at_pause          float   e.g. -500.25
      last_started          string
      last_stopped          string
      last_updated          string
      bot_version           string

    commands/               ← Mobile writes this — bot listens
      action                string  "start" | "stop"
      timestamp             string  ISO timestamp

    alerts/                 ← Bot pushes signals here — mobile reads
      {push_id}/
        symbol              string
        direction           string  "BUY" | "SELL"
        strategy_name       string
        entry_price         float
        suggested_sl        float
        suggested_tp        float
        risk_reward_ratio   float
        alert_type          string
        sent_at             string

    health/                 ← Bot writes every 10 min
      status                string  "HEALTHY" | "DEGRADED"
      mt5_connected         bool
      firebase_connected    bool
      account_trade_allowed bool
      balance               float
      equity                float
      market_status         object
      symbols_available     object
      timestamp             string

    market_status/          ← Bot writes every 60s
      overall_open          bool
      all_closed            bool
      status                object  {EURUSD: "OPEN", ...}
      timestamp             string

    trades/                 ← Bot writes on execution
      {push_id}/
        symbol, direction, entry, sl, tp, lot
        ticket, strategy, verified, session, timestamp

    sentiment/              ← SentimentService writes this
      {symbol}/
        direction_bias      string  "BULLISH" | "BEARISH" | "NEUTRAL"
        score               float   -1.0 to 1.0
        confidence          string  "HIGH" | "MEDIUM" | "LOW"
        gpt4_summary        string  News summary
        grok_summary        string  X/Social summary
        risk_advisory       string  Plain-English warning
        trade_alignment     string  "ALIGNED" | "OPPOSED"
        updated_at          string  ISO timestamp
```

---

## BraveBot Main Loop (`src/bot.py`)

```
bot.run()
  └── while True:
        ├── if not is_running → sleep, wait for Firebase start command
        ├── health_check() every 10 min
        ├── _get_config() from Firebase
        ├── _check_signals(config)
        │     ├── _load_active_strategy()   ← reads brave_config, hot-swaps if changed
        │     ├── Daily Loss Limiter        ← pauses bot if loss exceeds 5% of daily equity
        │     ├── _select_pairs() every 1hr ← scores by spread + volatility
        │     ├── _open_markets()           ← checks MT5 status
        │     └── for each open pair:
        │           ├── NewsFilter.is_safe_to_trade() ← skips during high-impact news
        │           ├── _count_positions()            ← checks positions + pending orders
        │           └── strategy.analyze(symbol)      → signal dict or None
        │                 └── if signal: execute_signal() + push to Firebase
        └── sleep CHECK_INTERVAL (60s)
```

---

## Strategy Contract

Every strategy file MUST follow this interface exactly.
`src/bot.py` calls nothing except `analyze()`.

```python
class MyStrategy:
    def __init__(self, config: dict):
        # config contains: lot_size, stop_loss_pips, take_profit_pips, etc.
        pass

    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Live mode:    provided_rates = None  → fetch from MT5
        Backtest mode: provided_rates = {
            mt5.TIMEFRAME_H4:  list[dict],
            mt5.TIMEFRAME_M15: list[dict],
        }

        Returns signal dict if all conditions pass, else None.
        """
        pass
```

**Signal dict shape** (must include all these keys):

```python
{
    "symbol":            str,    # e.g. "EURUSD"
    "direction":         str,    # "BUY" | "SELL"
    "strategy_name":     str,    # e.g. "Thunder"
    "order_type":        str,    # "MARKET" | "STOP" | "LIMIT"
    "entry_price":       float,
    "suggested_sl":      float,
    "suggested_tp":      float,
    "risk_reward_ratio": float,
    "expected_profit":   float,
    "expected_loss":     float,
    "probability":       str,    # "HIGH" | "MEDIUM" | "LOW"
    "structure":         dict,   # strategy-specific metadata
}
```

---

## Thunder Strategy (`src/thunder.py`)

**Source:** ForexFactory thread #896811 — adapted for M15/H4 and ATR scaling

**Pairs:** EURUSD, GBPUSD, XAUUSD, US30, NAS100

**Logic:**
1. H4 EMA 8/13/21 stack — must be strictly aligned (8>13>21 = BUY, 8<13<21 = SELL)
2. ATR filter on M15 — skip if ATR < 3 pips (dead market)
3. 5-candle breakout range on M15 — last 5 closed candles
4. Entry: pending STOP ATR×0.3 above range high (BUY) or below range low (SELL)
5. SL: opposite extreme of range + ATR×0.5 buffer
6. TP: minimum 2:1 RR from entry
7. Session filter: London 08:00–11:00 UTC + NY 13:00–16:00 UTC only

**Key constants:**

```python
EMA_FAST   = 8
EMA_MID    = 13
EMA_SLOW   = 21
RANGE_CANDLES      = 5      # 5-candle range definition
ATR_PERIOD         = 14
ATR_ENTRY_MULT     = 0.3    # Entry buffer
ATR_SL_MULT        = 0.5    # SL buffer
MIN_RR             = 2.0
MIN_ATR_PIPS       = 3.0
```

---

## Backtester Contract (`backtest/backtest_thunder.py`)

All backtester files follow this pattern:

```
fetch_data()          Chunked 10k candle fetching + CSV caching
get_h4_context()      Binary search time-machine — no lookahead bias
ThunderTradeSimulator Pending STOP orders, 1% risk sizing, pessimistic execution
ThunderBacktester     Orchestrates time-machine + strategy.analyze() + simulator
print_summary()       Win rate, profit factor, per-symbol, per-session, drawdown
export_csv()          All trades to CSV
main()                Entry point — loops over SYMBOLS
```

**Run from terminal:**
```bash
python backtest/backtest_thunder.py
```

**Pessimistic execution rule:** If SL and TP both hit on the same candle, SL wins.
This is intentional — it's the conservative assumption.

**Pending order expiry:** If a pending STOP order is not triggered within
`MAX_CANDLES_WAIT` (100) candles, it expires and is recorded as `EXPIRED`.

---

## Config File (`config.py`)

```python
# MT5
MT5_LOGIN    = 12345678          # int
MT5_PASSWORD = "yourpassword"    # str
MT5_SERVER   = "RoboForex-Pro"   # str

# Firebase
FIREBASE_DATABASE_URL = "https://your-project.firebaseio.com"
USER_ID = "RcB4T6930SVvE4Lt9mCSs6nbG1G2"

# Trading defaults
SYMBOLS          = ["EURUSD", "GBPUSD", "XAUUSD", "US30", "NAS100"]
TIMEFRAME        = "M15"
LOT_SIZE         = 0.1
MAX_TRADES       = 3
STOP_LOSS_PIPS   = 50
TAKE_PROFIT_PIPS = 100
```

---

## Candle Dict Format

All candles throughout the codebase use this exact shape:

```python
{
    "time":   int,    # Unix timestamp
    "open":   float,
    "high":   float,
    "low":    float,
    "close":  float,
    "volume": int,    # tick_volume from MT5
}
```

---

## Pip Size Reference

```python
POINT_SIZES = {
    "EURUSD": 0.00001,   # 5-decimal FX
    "GBPUSD": 0.00001,
    "USDJPY": 0.001,     # 3-decimal JPY pairs
    "XAUUSD": 0.01,      # Gold
    "US30":   0.01,      # Indices
    "NAS100": 0.01,
}
```

---

## Known Constraints

- MT5 must be running and logged in on the same machine as the bot
- Firebase free tier — no cost but has rate limits (not an issue at current scale)
- Geographic latency from Lusaka to Firebase region adds ~150–300ms to Firebase calls
  (not a problem for a 60-second loop bot)
- Windows PowerShell is the dev environment — all scripts are tested there
- `data/` CSV cache: delete a file to force a fresh MT5 fetch on next backtest run

---

## What Does NOT Exist Yet

- Mobile app screens (Phase 3 in progress) — project scaffolded, screens being built
- Ringer strategy — not started
- Second scalping strategy for Brave bundle — pending Thunder backtest results
- Auto-restart on crash — needs systemd or equivalent for production VPS

---

## Adding a Second Strategy (Future)

1. Create `src/newstrategy.py` with `class NewStrategy`
2. Must follow the Strategy Contract above — `__init__(config)` + `analyze(symbol, provided_rates)`
3. In `src/bot.py`: `from newstrategy import NewStrategy` and add `"newstrategy": NewStrategy` to `STRATEGY_REGISTRY`
4. In `backtest/`: create `backtest_newstrategy.py` following the same backtester pattern
5. Update `brave_config/available_strategies` in Firebase to include `"newstrategy"`
6. Mobile app can now offer NewStrategy as a switchable option
