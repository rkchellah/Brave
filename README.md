# Brave — Flow Trading Agent

Most retail trading bots fail the same way: they enter on a signal with no context, get stopped out by news, and repeat until the account is gone. Brave is built differently — a LangGraph agent that runs a structured pipeline on every potential trade before a single order is placed.

## What it does

"Analyse GBPUSD and execute if conditions are met."

The agent runs four nodes in sequence:

**DETECT** — Flow strategy scans H1 trend alignment and M15 sweep+reclaim pattern. If no setup exists, the pipeline ends here.

**ANALYSE** — Finnhub fetches recent headlines for the pair. DeepSeek reads them and returns CONFIRM, OPPOSE, or UNCERTAIN with a one-sentence reason.

**RISK CHECK** — Pure Python: daily loss limit (5%), max open positions per symbol, lot size sanity. Any failure aborts the trade.

**EXECUTE or HITL** — CONFIRM passes to MT5 execution. UNCERTAIN pushes to Firebase `pending_signals` where the trader confirms or rejects from the mobile app within 3 minutes. OPPOSE aborts silently.

## Stack

| Layer | Detail |
|-------|--------|
| Orchestration | LangGraph 1.2.9 |
| Strategy | Flow — H1 fractal trend + M15 sweep+reclaim (fxalexg methodology) |
| News | Finnhub general news API + DeepSeek-chat analysis |
| Execution | MetaTrader5 Python API (RoboForex demo/live) |
| Bridge | Firebase Realtime Database |
| Mobile | React Native + Expo (Android APK via EAS) |
| Risk | 1% per trade, 5% daily loss cap, per-symbol position limits |

## Setup

```bash
git clone https://github.com/rkchellah/Brave-trading-agent.git
cd Brave
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `config.example.py` to `config.py` and fill in:

```python
MT5_LOGIN         = your_account_number
MT5_PASSWORD      = "your_password"
MT5_SERVER        = "RoboForex-Demo"
FIREBASE_USER_ID  = "your_uid"
FINNHUB_API_KEY   = "your_key"
DEEPSEEK_API_KEY  = "your_key"
```

Download your Firebase service account key and save as `serviceAccountKey.json` at the project root.

```bash
python src/setup_firebase.py
python src/bot.py
```

## Mobile app

```bash
cd brave-app
npm install --legacy-peer-deps
eas build --platform android --profile preview
```

Install the APK on your Android device. The app reads live from Firebase — no separate server needed.

## Execution modes

**AUTO** — Agent executes confirmed signals immediately. Firebase alert written with ticket number and price.

**MANUAL** — All signals go to HITL regardless of sentiment verdict. Trader confirms or rejects from the app. Signal expires in 3 minutes if no response.

Toggle from the app Settings screen or set default in `config.py`:

```python
EXECUTION_MODE = "AUTO"  # "AUTO" | "MANUAL"
```

## Flow strategy

Flow catches trend continuations at Areas of Interest on H1 with M15 entry confirmation.

Entry requires all of:

- H1 fractal trend defined (Higher High + Higher Low = UPTREND, Lower High + Lower Low = DOWNTREND)
- AOI found via zone clustering — minimum 2 touches within a 15-pip band
- M15 sweep+reclaim: price wicks through the AOI and closes back inside with a strong body (>40% of candle range)
- Active session: Pre-London (06:00–08:00), London (08:00–11:00), Bridge (11:00–13:00), NY (13:00–16:00) UTC

Exit: ATR-based SL (1.2× ATR on M15), 1.5:1 minimum RR.

Backtest (50,000 M15 candles, EURUSD + GBPUSD, ~17 months):

372 trades, 41% win rate, 1.24 profit factor, +65.8% return, 14.6% max drawdown

## What I learned building this

**LangGraph state typing on Python 3.14** — LangChain's Pydantic v1 compatibility layer throws a UserWarning on Python 3.14. Non-blocking but worth watching if LangChain drops v1 support in a future release.

**Firebase as a human-in-the-loop bridge** — Using Firebase `pending_signals` as the HITL queue works well. The bot writes, the app reads in real time, the trader responds, and the bot polls for the response. No websockets, no separate server. The 3-minute expiry prevents stale signals from executing after the market has moved.

**Flow's selectivity** — Flow generates roughly 22 trades per month across two pairs. That's intentional — the sweep+reclaim pattern is strict. On quiet days the DETECT node ends the pipeline immediately. The LangGraph architecture makes this visible in logs rather than silent.

**DeepSeek as a news filter** — At temperature 0.1, DeepSeek-chat is consistent about returning CONFIRM/OPPOSE/UNCERTAIN. Prompt structure matters: giving it explicit rules for each verdict eliminates ambiguous responses that fell outside the three expected values.

## Project structure

```
Brave/
├── src/
│   ├── bot.py           # Main loop, Firebase init, command listener
│   ├── graph.py         # LangGraph pipeline (DETECT→ANALYSE→RISK→EXECUTE/HITL)
│   ├── flow.py          # Flow strategy (H1 trend + M15 sweep+reclaim)
│   ├── news_fetcher.py  # Finnhub fetch + DeepSeek prompt formatting
│   ├── news_filter.py   # High-impact news calendar filter
│   └── send_command.py  # CLI start/stop utility
├── backtest/
│   ├── backtest_flow.py
│   └── results/
├── brave-app/           # React Native mobile app
├── config.py            # Credentials (not in repo)
├── config.example.py    # Template
└── serviceAccountKey.json  # Firebase key (not in repo)
```

## Security

Never commit `config.py` or `serviceAccountKey.json`. Both are in `.gitignore`. If either is accidentally pushed, rotate the credentials immediately — Firebase service account keys and API keys cannot be invalidated by deleting the commit.

## Author

Chella Kamina — [github.com/rkchellah](https://github.com/rkchellah)
