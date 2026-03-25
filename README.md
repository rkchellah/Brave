# Brave - Flow Strategy

Automated trading bot for MetaTrader 5 focused on the **Flow** trend continuation strategy with Firebase integration.

## Features

- ✅ MT5 Integration
- ✅ Firebase Real-time Database
- ✅ Remote Control (Mobile-ready)
- ✅ Health Monitoring
- ✅ AI-Driven Sentiment Analysis (Gemini + GPT-4 Fallback)
- ✅ Auto-Trading Execution
- ✅ Dynamic 1% Risk Management
- ✅ News Filtering (ForexFactory integration)
- ✅ Daily Loss Limiter (5% session equity cap)
- ✅ Daily Log Rotation (7-day history)
- ✅ Comprehensive Logging
- ✅ Thunder Strategy (EMA Stack Scalper)
- ✅ Flow Strategy (fxalexg Trend Continuation)
- ✅ Project Rules enforced (see [RULES.md](file:///c:/Users/ECSZMLPT0067/Downloads/Back%20Up Files/Softs/SeoTools Back Up Files/bin/Projects/ME/Something Files/Projects/Brave/RULES.md))
- ✅ Architecture Document (see [ARCHITECTURE.md](file:///c:/Users/ECSZMLPT0067/Downloads/Back%20Up Files/Softs/SeoTools Back Up Files/bin/Projects/ME/Something Files/Projects/Brave/ARCHITECTURE.md))

## Setup

### Prerequisites

- Python 3.9+
- MetaTrader 5
- Firebase Account
- RoboForex Trading Account (Demo or Live)

### Installation

1. **Clone the repository:**
```bash
   git clone https://github.com/rkchellah/Brave.git
   cd Brave
```

2. **Install dependencies:**
```bash
   pip install MetaTrader5 firebase-admin numpy
```

3. **Configure the bot:**
   - Fill in your actual credentials (including `GEMINI_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`)
   - Download Firebase service account key (save as `serviceAccountKey.json`)

4. **Setup Firebase:**
```bash
   python src/setup_firebase.py
```

5. **Run the bot:**
```bash
   python src/bot.py
```

6. **Run the AI Sentiment Service (Optional):**
```bash
   python src/sentiment_service.py
```

### Mobile App Setup (brave-app)
Prerequisites: Node.js v17+, Expo Go (SDK 54) installed on your Android device.

```bash
cd brave-app
npm install
npx expo install --fix
npx expo start --clear
```
Scan the QR code with Expo Go on your phone.
Note: always run commands one line at a time in PowerShell.

## Usage

### Start/Stop Bot
```bash
python src/send_command.py start
python src/send_command.py stop
```
Commands are sent to Firebase `commands/action`. The bot listener responds within
one 60-second loop cycle. Verify the bot is running by checking
`bot_status/is_running` in Firebase Console.

### Execution Modes
The bot supports two execution modes, toggled from the mobile app or Firebase:

- **AUTO** (default) — Bot executes signals immediately, sends push notification
  as a receipt. Best when you are not actively watching the markets.
- **MANUAL** — Bot pauses at signal, sends push notification with Confirm/Reject.
  Signal expires in 3 minutes if no response — logged as `EXPIRED`.
  Best when you are actively watching and want final approval on each trade.

Set default in `config.py`:
```python
EXECUTION_MODE = "AUTO"   # "AUTO" | "MANUAL"
```

### Monitor

- Check Firebase Console for real-time status
- Review log files: `bot_log_YYYYMMDD.txt`

## Project Structure
```
Brave/
├── src/                  # Main bot and strategy logic
│   ├── bot.py             # Main entry point (Phase 2)
│   ├── thunder.py         # Thunder strategy
│   ├── flow.py            # Flow strategy
│   ├── news_filter.py     # High-impact news filter
│   ├── sentiment_service.py # AI Sentiment Analysis
│   ├── manage_config.py   # Config manager
│   ├── send_command.py    # Command utility
│   └── setup_firebase.py  # Firebase setup script
├── config.py              # Configuration (NOT in repo)
├── config.example.py      # Config template
├── serviceAccountKey.json # Firebase key (NOT in repo)
├── backtest/              # Backtesting scripts
│   └── results/           # Backtest trade results (CSV)
└── README.md             # This file
```

## Security

 **Never commit these files:**
- `config.py` (contains credentials)
- `serviceAccountKey.json` (Firebase key)
- Log files (may contain sensitive data)

## Roadmap

- [x] Phase 1: Core infrastructure (MT5, Firebase, Logging)
- [x] Phase 2: Live Trading & Stability
    - [x] **Auto-Execution**: 1% risk-based lot sizing
    - [x] **News Filter**: FF Calendar integration
    - [x] **Daily Loss Limiter**: 5% session equity protection
    - [x] **Stability**: Log rotation + SSE reconnect handling + Firebase Singleton Fix
    - [x] **Thunder Strategy**: EMA Stack Scalper (+273% backtest) (Class import fixed)
- [x] Phase 3: Mobile App — Complete
    - [x] **Stack**: React Native + Expo SDK 52 (React Native upgraded to 0.79.2)
    - [x] **Device**: Running live on Android via Expo Go
    - [x] **Firebase**: Real-time sync for signals, status, and config
    - [x] **Navigation**: Bottom tab navigation
    - [x] **Notifications**: Detailed trade receipt alerts (Ticket # + Price)
    - [x] Dashboard: Real-time P/L and Strategy sync
    - [x] Signals: Interactive Human-in-the-Loop confirm/reject
    - [x] Insights: AI Sentiment with Google Gemini & GPT-4 fallback
    - [x] Alerts: Fast execution history with MT5 ticket details
    - [x] Settings: Mutual-exclusive strategy selection (hot-swappable)
- [ ] Phase 4: Production Hardening

## Flow Strategy

### Concept
Catch trend continuations at Areas of Interest (AOI) using multi-timeframe alignment.

### Logic
- **H4/H1 Trend Alignment**: Both timeframes must show the same trend direction (Fractal Market Structure)
- **AOI Detection**: Support/Resistance zones with 3+ touches (zone clustering approach)
- **Entry Pattern**: "Sweep + Reclaim" - Price sweeps liquidity below support/above resistance and reclaims the level
- **Session Filter**: Only trades during London (08:00-11:00) and NY (13:00-16:00) UTC
- **Exit**: Fixed RR (1:2) with trailing stop at 80% of TP distance
- **Risk**: 1% per trade

### Entry Confirmation
- **Engulfing candle** at AOI, OR
- **Large body** (>75% intensity), OR
- **Pin bar** (wick >60% of candle range)

### Source
Based on fxalexg's "1 Hour Day Trading Strategy"

## License

Private - Not for distribution

## Author

Chella Kamina
