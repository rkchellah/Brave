# Brave — Architecture Document
> Read this before touching any file. For AI agents and developers.

---

## What Brave Is

Brave is an autonomous forex trading agent. It runs the **Flow** strategy on MetaTrader 5,
checks every setup against recent news with DeepSeek, and either places the order or asks
the trader to confirm it from a mobile app.

The decision pipeline is a LangGraph state machine. The runtime around it — broker and
Firebase connections, market sessions, pair selection, health — lives in `bot.py`. Order
placement is isolated in `trade_executor.py` so live trading and human-confirmed trading
take exactly the same code path.

---

## Project Rules

- Python only. Type hints enforced.
- No credentials ever hardcoded — environment variables first, `config.py` as local fallback
- Comments explain *why*, never *what*
- Every trade action must be logged
- No strategy runs without defined SL, TP, and RR
- Update `README.md` when code changes
- Update `CHECKLIST.md` when tasks complete
- Always run one PowerShell command per line

---

## Repository Structure

```
Brave/
├── src/
│   ├── bot.py               # Main loop, MT5 + Firebase init, credential resolution
│   ├── graph.py             # LangGraph pipeline (DETECT→ANALYSE→RISK_CHECK→EXECUTE/HITL)
│   ├── flow.py              # Flow strategy — H1 trend + M15 sweep+reclaim
│   ├── trade_executor.py    # Signal validation, risk sizing, MT5 order placement
│   ├── news_fetcher.py      # Finnhub headlines + DeepSeek prompt formatting
│   ├── news_filter.py       # ForexFactory high-impact event blackout windows
│   └── seed_mt5_config.py   # One-time broker credential seed (deletable)
├── scripts/
│   └── clear_pending_signals.py   # One-time HITL queue cleanup
├── backtest/                # Backtesting scripts and CSV results
├── brave-app/               # React Native mobile app
├── logs/                    # Runtime logs and trade CSVs (auto-generated)
├── config.py                # Config and credentials (never committed)
├── requirements.txt         # Pinned Python dependencies
└── serviceAccountKey.json   # Firebase key (never committed)
```

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.9+ |
| Orchestration | LangGraph 1.2.9 |
| Trading Execution | MetaTrader 5 Python API (`MetaTrader5`) |
| Market Data | MT5 `copy_rates_from_pos()` — H1 and M15 |
| News Headlines | Finnhub (`finnhub-python`) |
| News Analysis | DeepSeek `deepseek-chat` via the OpenAI-compatible client |
| Event Calendar | ForexFactory weekly JSON (`requests`) |
| Database | Firebase Realtime Database (`firebase-admin`) |
| Math | NumPy |
| Secrets | Environment variables, `config.py` fallback |
| Mobile App | React Native + Expo (`brave-app`) |

---

## Data Flow

```
        MT5 (H1 + M15 rates)
                ↓
        flow.py — analyze()
                ↓  signal dict or None
        graph.py — DETECT
                ↓
        Finnhub headlines → DeepSeek → CONFIRM / OPPOSE / UNCERTAIN
        graph.py — ANALYSE                    ↓
                ↓                      news_analysis/ (Firebase → Insights screen)
        graph.py — RISK_CHECK
        (open positions + orders, daily loss limit, signal sanity)
                ↓
        ┌───────┴────────┐
   CONFIRM + AUTO    UNCERTAIN or MANUAL
        ↓                 ↓
  trade_executor    pending_signals/ (Firebase)
   .place_order()         ↓
        ↓          mobile app: Confirm / Reject
    MT5 order             ↓
        ↓          bot.py — _process_pending_signals()
        ↓                 └→ trade_executor.place_order()
   alerts/ + logs/trades/*.csv
```

---

## LangGraph Pipeline (`src/graph.py`)

Five nodes over a `BraveState` TypedDict. Every node catches its own exceptions and
returns a state that routes safely to `END` — one bad symbol never stops the loop.

| Node | Responsibility | Failure behaviour |
|---|---|---|
| `detect` | Run Flow, validate the signal shape | No signal or malformed → abort |
| `analyse` | Finnhub headlines → DeepSeek verdict | API down → `UNCERTAIN`, never CONFIRM/OPPOSE |
| `risk_check` | Positions+orders, daily loss, signal sanity | Any failure → abort |
| `execute` | `trade_executor.place_order()` | Structured failure → `EXECUTION_FAILED` alert |
| `hitl` | Push to `pending_signals` for confirmation | Firebase down → signal dropped, logged |

Routing:

```
detect     → no signal              → END
analyse    → OPPOSE                 → END
analyse    → CONFIRM / UNCERTAIN    → risk_check
risk_check → fail                   → END
risk_check → MANUAL mode            → hitl
risk_check → pass + UNCERTAIN       → hitl
risk_check → pass + CONFIRM         → execute
```

The compiled graph is cached at module level — compiling per symbol per cycle is waste.

A DeepSeek outage degrades to `UNCERTAIN` rather than `CONFIRM` or `OPPOSE`, so an API
failure can neither green-light nor silently block trades on its own: it routes to a human.

---

## Execution Layer (`src/trade_executor.py`)

One code path for both AUTO and MANUAL execution, so risk sizing and price handling
cannot drift apart between them.

Guarantees:

- **Signal validated first** — required fields present, prices numeric and positive,
  SL/TP on the correct side of entry for the direction
- **Risk-based sizing** — `RISK_PER_TRADE_PCT` of balance across the entry→SL distance,
  clamped to the symbol's `volume_min` / `volume_max` / `volume_step` and to `LOT_SIZE`
- **Digit rounding** — price, SL and TP rounded to `symbol_info.digits`; unrounded values
  are silently dropped by the broker on symbols like XAUUSD
- **Stops level respected** — orders inside `trade_stops_level` are rejected locally
- **Never unprotected** — an order is never sent without both SL and TP
- **Live price re-check** — SL/TP re-validated against the fill price, not the stale signal
- **Filling mode fallback** — IOC → FOK → RETURN on `TRADE_RETCODE_INVALID_FILL`
- **Transient retry** — requote, price-changed, price-off, timeout and connection retcodes
  get one retry at a refreshed price
- **Never raises** — callers receive `{ok, ticket, price, lot, retcode, error}`

Every execution appends to `logs/trades/trades_YYYY-MM-DD.csv`.

---

## Broker Credential Resolution

Broker credentials are editable from the mobile app (Settings → Broker Account), which
writes `mt5_config`. `config.py` remains the backup source.

```
BraveBot.__init__
  └─ _init_firebase()            ← must come first; credentials live in Firebase
  └─ _init_mt5()
       └─ _resolve_mt5_credentials()   ← cached for the process lifetime
            └─ _read_mt5_credentials()
                 ├─ Firebase mt5_config    → used only if login + password + server
                 │                            are ALL present and valid
                 └─ config.py              → used if the node is missing, empty,
                                              unreadable, or partially filled
       └─ mt5.login(login, password, server)
```

Design constraints:

- **Partial config never wins.** A half-filled node falls back to `config.py` instead of
  attempting a doomed login, so a bad edit from the app cannot lock the bot out.
- **Resolved once per process.** `_reconnect_mt5()` reuses the cached tuple. Re-reading on
  reconnect could silently move a running bot to a different broker account while positions
  are open, leaving them unmonitored.
- **Changing accounts requires a restart.** Hot-reloading would mean `mt5.shutdown()` +
  `mt5.initialize()` mid-session. The app states this limitation in the Broker Account card.
- **The password is never logged.** Only the source, login and server appear in the log.

Security note: `mt5_config` holds the password in plaintext. Realtime Database rules must
restrict this subtree to the owning UID.

---

## Strategy Contract

Every strategy must follow this interface. `graph.py` calls only `analyze()`.

```python
class MyStrategy:
    def __init__(self, config: dict):
        pass

    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Live mode:     provided_rates = None → fetch from MT5
        Backtest mode: provided_rates = {mt5.TIMEFRAME_H1: [...], mt5.TIMEFRAME_M15: [...]}
        Returns signal dict or None.
        """
        pass
```

**Signal dict shape:**
```python
{
    "symbol":            str,    # e.g. "EURUSD"
    "direction":         str,    # "BUY" | "SELL"
    "strategy_name":     str,    # e.g. "Flow"
    "order_type":        str,    # "MARKET"
    "entry_price":       float,
    "suggested_sl":      float,
    "suggested_tp":      float,
    "risk_reward_ratio": float,
    "probability":       str,    # "HIGH" | "MEDIUM"
    "structure":         dict,   # strategy metadata
}
```

`trade_executor.validate_signal()` enforces the first seven fields at two points — in
DETECT and again in RISK_CHECK — before anything reaches the broker.

---

## Candle Dict Format

All candles throughout the codebase use this shape:

```python
{
    "time":   int,    # Unix timestamp
    "open":   float,
    "high":   float,
    "low":    float,
    "close":  float,
    "volume": float,  # tick volume from MT5
}
```

---

## Flow Strategy (`src/flow.py`)

**Type:** Trend continuation scalper (structure + sweep + reclaim)
**Timeframes:** H1 for trend and structure, M15 for entry
**Sessions (UTC):** Pre-London 06:00–08:00, London 08:00–11:00, Bridge 11:00–13:00, NY 13:00–16:00

Entry requires all of:

1. H1 fractal trend defined — HH+HL = UPTREND, LH+LL = DOWNTREND. No ranging.
2. Area of Interest via zone clustering — minimum 2 touches within a 15-pip band
3. M15 sweep + reclaim of that AOI — wick through, close back inside, body >40% of range
4. Active session window
5. No high-impact news within ±30 minutes (`news_filter.py`)

Exit: ATR-based SL (1.2× ATR on M15), 1.5:1 minimum RR.

---

## Firebase Data Structure

Everything the bot and the mobile app exchange lives under `users/{USER_ID}/`:

```
users/{USER_ID}/
  bot_status/          balance, equity, profit, open_positions,
                       session_pnl, is_running, active_strategy, execution_mode
  brave_config/        execution_mode (AUTO|MANUAL), strategy_config/flow/{enabled, max_trades}
  commands/            action: start|stop
  health/              mt5_connected, firebase_connected, account_trade_allowed, status
  market_status/       per-symbol OPEN|CLOSED|RESTRICTED|UNAVAILABLE
  alerts/{push_id}/    TRADE_EXECUTED | EXECUTION_FAILED — ticket, filled_price, lot, error
  pending_signals/     HITL queue — status PENDING|CONFIRMED|EXECUTING|EXECUTED|
                       REJECTED|EXPIRED|FAILED, expires_at
  news_analysis/{sym}/ verdict, reason, headlines[], article_count, news_source, model
  mt5_config/          login, password, server, updated_at, updated_by
```

### HITL signal lifecycle

```
PENDING ──user confirms──→ CONFIRMED ──bot claims──→ EXECUTING ──→ EXECUTED
   │                                                      └──────→ FAILED
   ├──user rejects───────→ REJECTED
   └──expires_at passed──→ EXPIRED
```

The bot claims a signal (`EXECUTING`) *before* sending the order. If that write fails it
skips the signal — executing without being able to record the outcome would re-execute the
same signal on the next cycle.

---

## Configuration (`config.py` + `.env`)

Resolution order is **environment variable → `.env` → `config.py` default**. `config.py`
loads `.env` from the project root at import time via its own `load_dotenv()` (no
dependency), and never overwrites a variable already set in the environment. Secrets go in
`.env`; `config.py` holds only non-secret defaults. `validate_config()` runs at startup,
raising `ConfigError` on values that make trading unsafe and returning warnings for the rest.

```
MT5_LOGIN, MT5_PASSWORD, MT5_SERVER          # broker — all three set = no startup prompt
                                             # otherwise credentials.py prompts on the terminal;
                                             # Firebase mt5_config / config.py only when headless
FIREBASE_DATABASE_URL, FIREBASE_CREDENTIALS, USER_ID
SYMBOLS, TIMEFRAME, LOT_SIZE, MAX_TRADES
RISK_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT
DEEPSEEK_API_KEY, FINNHUB_API_KEY
EXECUTION_MODE, SIGNAL_EXPIRY_SECONDS
```

Never commit `.env`, `config.py` or `serviceAccountKey.json` — all three are in
`.gitignore`. `.env.example` is the committed template and must stay free of real values.

---

## Known Constraints and Issues

- **LangChain Pydantic v1 on Python 3.14.** Importing `langgraph` emits
  `UserWarning: Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater`.
  Non-blocking today; if LangChain drops v1 support, `graph.py` breaks at import.

- **Broker credentials require a restart.** By design — see Broker Credential Resolution.

- **`mt5_config` stores the password in plaintext.** Realtime Database rules must be
  scoped to the owning UID.

- **Finnhub general-news is not symbol-specific.** The feed is fetched once and cached for
  5 minutes, then keyword-filtered per symbol. Without the cache a 60-second loop over
  three symbols exceeds the free-tier rate limit.

- **MT5 terminal must be running** with Algo Trading enabled, on the same account.

- **Windows only.** The MetaTrader5 Python package has no Linux or macOS build.
