# MT5 Trading Bot - Phase 1

Mobile-ready automated trading bot for MetaTrader 5 with Firebase integration.

## Features

- ✅ MT5 Integration
- ✅ Firebase Real-time Database
- ✅ Remote Control (Mobile-ready)
- ✅ Health Monitoring
- ✅ Trade Verification
- ✅ Comprehensive Logging

## Setup

### Prerequisites

- Python 3.9+
- MetaTrader 5
- Firebase Account
- RoboForex Trading Account (Demo or Live)

### Installation

1. **Clone the repository:**
```bash
   git clone https://github.com/rkchellah/mt5-trading-bot.git
   cd mt5-trading-bot
```

2. **Install dependencies:**
```bash
   pip install MetaTrader5 firebase-admin numpy
```

3. **Configure the bot:**
   - Copy `config.example.py` to `config.py`
   - Fill in your actual credentials
   - Download Firebase service account key (save as `serviceAccountKey.json`)

4. **Setup Firebase:**
```bash
   python setup_firebase.py
```

5. **Run the bot:**
```bash
   python bot.py
```

## Usage

### Start/Stop Bot
```bash
python send_command.py start
python send_command.py stop
```

### Monitor

- Check Firebase Console for real-time status
- Review log files: `bot_log_YYYYMMDD.txt`

## Project Structure
```
mt5-trading-bot/
├── bot.py                  # Main bot
├── config.py              # Configuration (NOT in repo)
├── config.example.py      # Config template
├── setup_firebase.py      # Firebase setup script
├── send_command.py        # Command utility
├── serviceAccountKey.json # Firebase key (NOT in repo)
├── test_mt5.py           # MT5 connection test
├── test_firebase.py      # Firebase connection test
└── README.md             # This file
```

## Security

 **Never commit these files:**
- `config.py` (contains credentials)
- `serviceAccountKey.json` (Firebase key)
- Log files (may contain sensitive data)

## Roadmap

- [x] Phase 1: Core infrastructure (MT5, Firebase, Logging)
- [x] Phase 2: Strategy Implementation & Backtesting
    - [x] **Flow Strategy**: Trend Continuation with "Sweep + Reclaim" entry logic
    - [x] **Thunder Strategy**: JeaFx Trend Following (in progress)
    - [x] **Ringer Strategy**: Recovery & Hedging (planned)
    - [x] **Advanced Backtesting**: Chunked data fetching, Time-machine simulation, Multi-strategy portfolio support
- [ ] Phase 3: Mobile app Control Panel
- [ ] Phase 4: Client deployment & Production Hardening

## Strategies

### 1. Flow (Trend Continuation)
- **Concept**: Catch trend continuations at Areas of Interest (AOI).
- **Logic**:
  - H4/H1 Trend Alignment (Fractal Market Structure)
  - AOI Detection: Support/Resistance zones with 3+ touches
  - **Entry**: "Sweep + Reclaim" - Price sweeps liquidity below support/above resistance and reclaims the level.
  - **Exit**: Fixed RR (1:2) or Break-Even (moves SL to entry at 1R profit).
  - **Risk**: 1% per trade.

### 2. Thunder (JeaFx Trend)
- **Concept**: Catch major trend moves using Moving Averages.
- **Logic**:
  - MA Cross + Trend Filter.
  - Dynamic trailing stop for maximum trend capture.

### 3. Ringer (Recovery)
- **Concept**: Hedge losing trades to recover equity.
- **Logic**:
  - Zone recovery / Martingale hybrid (carefully risk-managed).
  - Uses specific "Ringing" patterns to exit complex drawdowns.

## License

Private - Not for distribution

## Author

Chella Kamina
