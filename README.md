# Brave — Frost Trading Agent

A five-node LangGraph pipeline for safer forex execution — Frost strategy, DeepSeek news analysis, MT5 orders.

Most retail trading bots fail the same way: they enter on a signal with no context, get stopped out by news, and repeat until the account is gone. A strategy fires, an order hits the market, and nobody checks whether the Fed just spoke or three positions are already open on the same pair. The trader finds out when the daily loss limit is already blown.

This agent addresses that directly. On every cycle it scans a symbol, and if Frost finds a setup it runs news analysis and risk checks before a single order is placed. Uncertain sentiment goes to the phone — not to the market.

## What it does

"Analyse GBPUSD and execute if conditions are met."

The agent calls five nodes in sequence:

**DETECT** — Frost scans the M15 chart for price stretched from its moving average in a ranging Asian-session market. If no setup exists, the pipeline ends here.

**ANALYSE** — Finnhub fetches recent headlines for the pair. DeepSeek reads them and returns CONFIRM, OPPOSE, or UNCERTAIN with a one-sentence reason.

**RISK_CHECK** — Pure Python: daily loss limit (5%), max open positions per symbol, lot size sanity, and any Firebase HITL row still PENDING/EXECUTING on that symbol. Any failure aborts the trade.

**EXECUTE** — CONFIRM + risk pass → MT5 market order. Firebase alert written with ticket number and price.

**HITL** — genuine UNCERTAIN (news fetched, DeepSeek read it, verdict is mixed) + risk pass → signal pushed to Firebase `pending_signals`. Trader confirms or rejects from the mobile app within 3 minutes. OPPOSE aborts silently. A Finnhub timeout, DeepSeek API error, or 0 usable headlines is `analysis_unavailable`, not a judgment: AUTO skips the signal (`outcome=ANALYSIS_UNAVAILABLE`); MANUAL still asks, but the card is tagged `hitl_kind: data_unavailable` so it is not mistaken for mixed news. A second DETECT on the same symbol is skipped while a PENDING or EXECUTING row already exists, so one idea cannot occupy two confirm slots.

Each node is a plain function. Routing is conditional edges on `BraveState`. The bot loop calls `run_brave_graph(symbol, config, firebase)` once per symbol per cycle.

Every DETECT writes one row to `logs/frost_attempts.csv` (regime, MA deviation, setup confirmed, signal/no-signal) — including near-misses. News-filter pauses and max-trades skips write the same schema (`reason=news_filter_pause` / `reason=max_trades_reached`) before DETECT runs. Full setups that leave DETECT also append to `logs/trade_log.csv`. When an MT5 position later hits SL/TP, the fast-status loop back-fills `exit_price`, `exit_reason`, and `pnl` on that row.

**Active strategy: Frost** — Asian-session mean reversion (00:00–06:00 UTC, no new entries after 05:30). It fades price away from a 20-period M15 MA once deviation reaches 12–40 pips, requires a ranging market (ATR 2–15 pips, no trend slope) and a spread under 2 pips, and targets the MA. This is the opposite regime bet to the retired Flow strategy, which needed a trend. `src/flow.py` stays on disk, unused and unreferenced.

Both strategies write the same `frost_attempts.csv` schema, so their datasets stay comparable — for Frost, `h1_trend` carries RANGING/TRENDING, `aoi_distance_pips` carries signed MA deviation, and `sweep_reclaim` marks whether the mean-reversion setup confirmed. Flow's Aug 11–14 rows are archived in `logs/flow_attempts_archive_2026-08-11_to_14.csv`.

All three CSVs are written by `src/trade_logger.py` and only by it, at paths anchored to the project root through `config.LOG_DIR` — running the bot from `src/` used to create a second, unreconciled log tree. The daily loss limit lives in `src/risk.py`, which both the bot loop and the graph's RISK_CHECK call, so the two cannot drift apart.

The news filter maps each symbol to the currencies whose events should pause it. **A symbol with no mapping is not filtered** — `unmapped_symbols()` is checked at startup and names any gap in the log, because a missing row used to mean silent permission to trade straight through high-impact events.

## Running locally

```bash
git clone https://github.com/rkchellah/Brave-trading-agent.git
cd Brave

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the secrets:

```
DEEPSEEK_API_KEY=your_key
FINNHUB_API_KEY=your_key
USER_ID=your_uid
MT5_LOGIN=your_account_number   # prompt default only
MT5_SERVER=RoboForex-Demo       # prompt default only
```

No password goes in any file — the bot prompts for it (hidden) at every start and never writes it to disk. Non-secret settings such as `EXECUTION_MODE` live in `config.py` and can be overridden from `.env`.

Download your Firebase service account key and save as `serviceAccountKey.json` at the project root. `.env`, `config.py` and `serviceAccountKey.json` are all in `.gitignore` — never commit them.

```bash
python src/seed_mt5_config.py   # optional — seeds broker credentials into Firebase
python src/bot.py
```

The bot creates the Firebase nodes it needs on first run.

MT5 terminal must be running with Algo Trading enabled. The bot connects to the same account as the terminal.

## Building the mobile app

```bash
cd brave-app
npm install --legacy-peer-deps
eas build --platform android --profile preview
```

Install the APK on an Android device. The app reads live from Firebase Realtime Database — no separate server. Screens: Dashboard (balance, equity, start/stop, AUTO/MANUAL), Signals (Confirm/Reject), Insights (DeepSeek verdict + Finnhub headlines), Alerts, Settings (Broker Account, Frost toggle, health including MT5 AutoTrading toolbar state).

Bot health is HEALTHY only when MT5 is connected, Firebase is reachable, the broker account allows trading, **and** the local MT5 AutoTrading toolbar toggle is on. A True→False flip on that toggle pushes an alert to the app.

## Security migration notice

The mobile app now requires Firebase Email/Password Authentication and uses the
authenticated UID for its database path. Before running it, enable that provider,
create the operator account, set the bot's `USER_ID` to that account's UID, and
deploy `firebase.database.rules.json` using the instructions in
`SECURITY_OPERATIONS.md`.

Broker passwords must not be stored in Firebase. Configure MT5 credentials only
on the trusted bot host (interactive prompt or OS/service environment secrets).
The legacy `seed_mt5_config.py` command is deliberately disabled.

## Connecting Firebase

The bot writes under `users/{USER_ID}/`:

- `bot_status` — balance, equity, running flag, active strategy
- `alerts` — executed trades and skipped signals
- `pending_signals` — HITL queue
- `brave_config` — execution mode, strategy_config
- `commands` — start/stop from the app
- `news_analysis` — per-symbol DeepSeek verdict and the Finnhub headlines behind it
- `mt5_config` — broker login, password and server, editable from the app

The app listens on the same paths. Same Firebase project, same UID. If the app shows $0.00, check Realtime Database rules — they must allow that UID to read and write.

## Broker credentials

The MT5 account can be changed from the app instead of by editing code. Settings → **Broker Account** writes login, password and server to `mt5_config`:

```
login:      00000000
password:   (plaintext)
server:     "RoboForex-Pro"
updated_at: ISO timestamp
updated_by: "mobile" | "seed_script"
```

At startup the bot resolves credentials in this order:

1. **Firebase `mt5_config`** — used only when `login`, `password` and `server` are all present and valid
2. **`config.py`** — used whenever the node is missing, empty, unreadable, or partially filled

A half-filled node falls back rather than attempting a doomed login, so a bad edit from the app can never lock the bot out of the account. The chosen source is logged at startup (`MT5 credentials source: Firebase | Login: … | Server: …`); the password is never logged on either path.

To populate the node from your existing `config.py` values once:

```bash
python src/seed_mt5_config.py          # refuses to overwrite an existing node
python src/seed_mt5_config.py --force  # overwrite deliberately
```

`src/seed_mt5_config.py` can be deleted once the node exists.

**Credentials are read once, at startup.** They are cached for the process lifetime, so an automatic reconnect after a dropped connection stays on the same account — switching brokers mid-session would leave open positions unmonitored. Changing the account requires restarting the bot, which is what the app tells the user.

Note that `mt5_config` stores the password in plaintext in Realtime Database. Anyone with database access or a leaked service account key can read it. Keep the database rules locked to your UID.

## How HITL works

When ANALYSE returns a genuine UNCERTAIN (or execution mode is MANUAL), EXECUTE is skipped. The HITL node pushes the signal to `pending_signals` with:

```
status:      PENDING
expires_at:  now + SIGNAL_EXPIRY_SECONDS   # default 180
hitl_reason: DeepSeek's one-sentence explanation
hitl_kind:   genuine_uncertainty | data_unavailable | manual_mode
```

`data_unavailable` is only used in MANUAL (AUTO skips those setups before HITL). The app Signals card shows **Mixed news** vs **Data unavailable** so the two cases cannot be confused.

The app shows Confirm / Reject. The bot polls that record. CONFIRMED proceeds to order placement. REJECTED or EXPIRED is logged and discarded. The 3-minute window exists because a Frost setup at M15 is stale once price has moved off the AOI.

AUTO vs MANUAL is read from Firebase `brave_config/execution_mode` each cycle, so the app can switch it without restarting the bot.

## Frost strategy

Frost fades price back toward its mean during the quiet Asian session, when spreads are tightest and ranges hold.

Entry requires all of:

- Asian session UTC 00:00–06:00, no new entries after 05:30 (never hold into the London open)
- Spread below 2.0 pips
- ATR between 2 and 15 pips — ranging, neither dead nor volatile
- Price 12–40 pips from the 20-period M15 MA
- No trend — regression slope under 1.5 pips/candle over the last 10 candles

Direction is the fade: above the MA sells, below it buys. Exit: TP at 80% of the distance back to the MA, SL beyond the deviation extreme plus 3 pips, minimum RR 0.4 — tight by design, carried by win rate rather than payoff.

Backtest: 527 trades, 64.5% win rate, 1.64 profit factor.

### Flow — retired 2026-08-14

The previous strategy caught trend continuations at H1 Areas of Interest with M15 sweep+reclaim confirmation, across Pre-London through NY sessions (372 trades, 41% win rate, 1.24 profit factor). Frost is the opposite regime bet. `src/flow.py` remains on disk, imported by nothing.

## Stack

- Orchestration: LangGraph 1.2.9
- Strategy: Frost — Asian-session mean reversion (MA deviation fade, Forex Fury profile)
- News: Finnhub general news API + DeepSeek-chat (`deepseek-chat`, temperature 0.1)
- Execution: MetaTrader5 Python API (RoboForex demo/live)
- Bridge: Firebase Realtime Database
- Mobile: React Native + Expo (Android APK via EAS)
- Risk: 1% per trade, 5% daily loss cap, per-symbol position limits

## What I learned building this

Three things that weren't obvious and took real time to get right.

**LangGraph state typing on Python 3.14.** LangChain's Pydantic v1 compatibility layer throws `UserWarning: Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater` on every import. Non-blocking today. If LangChain drops v1 support, `graph.py` will break at import. Tracked as a known risk — do not ignore it because the graph compiles.

**Firebase headers vs Firebase refs.** HITL failed in the first wiring pass when `pending_signals_ref` was used in `graph.py` but never created in `bot.py`. The graph compiled. DETECT ran. ANALYSE returned UNCERTAIN. HITL logged success and wrote nowhere. Fix: create `self.pending_signals_ref` during `_init_firebase`, pass it in the `firebase` dict on every `run_brave_graph` call, and verify with `inspect.getsource` that the string exists in `BraveBot` before calling the refactor done.

**DeepSeek as a news filter, not a scorer.** The previous stack ran GPT-4 + Grok sentiment scores on a background thread and gated trades with a numeric threshold. Scores drifted, the gate disagreed with the strategy, and two API keys lived in `config.py` for a service the graph replaced. Replacing that with a three-way verdict (CONFIRM / OPPOSE / UNCERTAIN) at temperature 0.1 is more useful: OPPOSE is a hard stop, UNCERTAIN is a human, CONFIRM is execute. Prompt structure matters — line 1 must be exactly one of those three words or the node defaults to UNCERTAIN.

**Never put the DeepSeek key in `graph.py`.** `config.py` is gitignored. `src/graph.py` is not. Pasting `api_key="sk-..."` into the OpenAI client is an uncommitted diff until someone runs `git add .`. Keep `api_key=DEEPSEEK_API_KEY` and load it from config. Verify with `git check-ignore -v config.py` and `Select-String -Path src/*.py -Pattern "sk-"`.

## Project structure

```
Brave/
├── src/
│   ├── bot.py              # Main loop, Firebase init, credential resolution, command listener
│   ├── graph.py            # LangGraph pipeline (DETECT→ANALYSE→RISK→EXECUTE/HITL)
│   ├── frost.py            # ACTIVE strategy (Asian-session mean reversion)
│   ├── flow.py             # Retired strategy (H1 trend + M15 sweep+reclaim)
│   ├── risk.py             # Session equity + daily loss limit
│   ├── trade_logger.py     # All CSV writes
│   ├── trade_executor.py   # Signal validation, risk-based sizing, MT5 order placement
│   ├── news_fetcher.py     # Finnhub fetch + DeepSeek prompt formatting
│   ├── news_filter.py      # High-impact news calendar filter
│   └── seed_mt5_config.py  # One-time mt5_config seed (deletable)
├── scripts/
│   └── clear_pending_signals.py   # One-time HITL queue cleanup
├── backtest/
│   ├── backtest_flow.py
│   └── results/
├── brave-app/              # React Native mobile app
├── config.py               # Credentials (not in repo)
├── config.example.py       # Template
└── serviceAccountKey.json  # Firebase key (not in repo)
```

## Security

Never commit `config.py` or `serviceAccountKey.json`. If either is accidentally pushed, rotate the credentials immediately — Firebase service account keys and API keys cannot be invalidated by deleting the commit.

## Author

Chella Kamina — [github.com/rkchellah](https://github.com/rkchellah)
