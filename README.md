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
   git clone https://github.com/yourusername/mt5-trading-bot.git
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

- [x] Phase 1: Core infrastructure
- [ ] Phase 2: Supply & Demand strategy
- [ ] Phase 3: Mobile app
- [ ] Phase 4: Client deployment

## License

Private - Not for distribution

## Author

Your Name