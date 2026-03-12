# Brave - Flow Strategy

Automated trading bot for MetaTrader 5 focused on the **Flow** trend continuation strategy with Firebase integration.

## Features

- ✅ MT5 Integration
- ✅ Firebase Real-time Database
- ✅ Remote Control (Mobile-ready)
- ✅ Health Monitoring
- ✅ Trade Verification
- ✅ Comprehensive Logging
- ✅ Flow Strategy (fxalexg Trend Continuation)
- ✅ Project Rules enforced (see [RULES.md](file:///c:/Users/ECSZMLPT0067/Downloads/Back%20Up%20Files/Softs/SeoTools%20Back%20Up%20Files/bin/Projects/ME/Something%20Files/Projects/mt5-trading-bot/RULES.md))

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
Brave/
├── bot.py                  # Main bot (Flow strategy)
├── flow.py                # Flow strategy implementation
├── config.py              # Configuration (NOT in repo)
├── config.example.py      # Config template
├── setup_firebase.py      # Firebase setup script
├── send_command.py        # Command utility
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
- [x] Phase 2: Flow Strategy Implementation & Backtesting
    - [x] **Flow Strategy**: Trend Continuation with "Sweep + Reclaim" entry logic
    - [x] **Advanced Backtesting**: Chunked data fetching, Time-machine simulation
    - [x] **Trade Management**: Break-even at 80% TP, relaxed exit logic
- [ ] Phase 3: Mobile app Control Panel
- [ ] Phase 4: Client deployment & Production Hardening

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
