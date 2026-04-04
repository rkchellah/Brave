# QuantifyX — Architecture Document
> Read this before touching any file. For AI agents and developers.

---

## What QuantifyX Is

QuantifyX is an autonomous crypto trading agent that runs the **Frost** mean reversion strategy on Kraken spot markets.

It was originally built as **Brave** — an MT5 forex trading bot. The Frost strategy was ported from MT5/Forex to Kraken crypto by:
- Replacing the MT5 data feed with Kraken's public OHLCV REST API
- Replacing MT5 `order_send()` with `krakenex` REST API calls
- Recalibrating pip/point thresholds from forex pips to crypto dollar units

The core strategy logic (MA, ATR, trend detection, signal building) is **unchanged** from the original Frost implementation.

---

## Project Rules

- Python only. Type hints enforced.
- No credentials ever hardcoded — always use `.env` via `python-dotenv`
- Comments explain *why*, never *what*
- Every trade action must be logged
- No strategy runs without defined SL, TP, and RR
- Update `README.md` when code changes
- Update `CHECKLIST.md` when tasks complete
- Always run one PowerShell command per line

---

## Repository Structure

```
QuantifyX/
├── src/
│   ├── frost_kraken.py      # Frost strategy — Kraken port
│   ├── executor.py          # Kraken order execution via krakenex
│   ├── agent.py             # Main loop
│   ├── firebase_logger.py   # Firebase signal/trade logging
│   ├── manage_config.py     # Config manager utility
│   └── setup_firebase.py    # One-time Firebase setup
├── backtest/                # Backtesting scripts and CSV results
├── brave-app/               # React Native mobile app (optional)
├── logs/                    # Runtime logs (auto-generated)
├── .env                     # API keys (never committed)
├── config.py                # App config
├── serviceAccountKey.json   # Firebase key (never committed)
├── test_connection.py       # Kraken + PRISM connection test
├── test_frost.py            # Full pipeline test (Frost + Executor)
└── docs/                    # README, ARCHITECTURE, CHECKLIST etc.
```

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.9+ |
| Trading Execution | Kraken REST API via `krakenex 2.2.2` |
| Market Data (OHLCV) | Kraken public REST — `/0/public/OHLC` |
| Market Data (Signals) | PRISM API — `api.prismapi.ai` |
| Database | Firebase Realtime Database (`firebase-admin`) |
| Math | NumPy |
| Secrets | `.env` + `python-dotenv` |
| Mobile App | React Native + Expo SDK (brave-app, optional) |

---

## Data Flow

```
Kraken Public OHLCV API
        ↓
   frost_kraken.py
   (MA, ATR, trend, deviation checks)
        ↓
   Signal dict or None
        ↓
   executor.py
   (krakenex AddOrder)
        ↓
   Kraken Exchange
        ↓
   firebase_logger.py
   (log to Firebase)
```

---

## Frost Strategy (`src/frost_kraken.py`)

**Origin:** Ported from `src/frost.py` (Brave MT5 bot)
**Type:** Mean Reversion / Counter-Trend Scalping
**Session:** Asian only — 00:00 to 06:00 UTC, no new entries after 05:30 UTC
**Pairs:** XBTUSD, ETHUSD on Kraken

### Logic (unchanged from original)
1. Session filter — Asian session only
2. Fetch M15 OHLCV candles from Kraken public API
3. ATR range check — skip if market too volatile or too dead
4. Calculate 20-period MA and price deviation
5. Deviation threshold check — must be between MIN and MAX
6. Trend filter — skip if linear regression slope too steep
7. Generate mean reversion signal — fade price back toward MA

### What Changed vs Original Frost
| Component | Original (Brave/MT5) | QuantifyX (Kraken) |
|---|---|---|
| Data source | `mt5.copy_rates_from_pos()` | Kraken `/0/public/OHLC` |
| Execution | `mt5.order_send()` | `krakenex.query_private('AddOrder')` |
| Spread check | MT5 live tick | Removed (handled by Kraken) |
| Point size | Forex pip (0.00001) | Crypto unit (BTC=0.1, ETH=0.01) |
| Thresholds | Forex pips | Dollar units (recalibrated) |
| Pairs | GBPUSD, USDCAD, EURCHF | XBTUSD, ETHUSD |

### Recalibrated Thresholds (as of April 2026)
```python
MIN_DEVIATION_PIPS = 50.0    # $50 minimum deviation from MA
MAX_DEVIATION_PIPS = 1000.0  # $1000 max deviation
MAX_ATR_PIPS       = 600.0   # Skip if ATR > $600
MIN_ATR_PIPS       = 10.0    # Skip if ATR < $10
# Trend slope threshold: 100.0 units/candle
# Validated from live data: current BTC slope ~70 during active market
# Asian session slope expected to be lower (calmer conditions)
```

### What Stays Identical
- `_calculate_ma()` — pure NumPy, no exchange dependency
- `_calculate_atr()` — pure NumPy, no exchange dependency
- `_is_trending()` — linear regression slope, no exchange dependency
- `_is_asian_session()` — datetime only
- `_is_safe_entry_time()` — datetime only
- `_build_signal()` — pure math, signal dict shape unchanged

---

## Executor (`src/executor.py`)

Wraps `krakenex` and translates signal dicts into Kraken REST API calls.

**dry_run=True** — logs the order, does not send it. Use for testing.
**dry_run=False** — live execution on Kraken.

```python
order_params = {
    "pair":      "XBTUSD",
    "type":      "buy",      # Kraken uses lowercase
    "ordertype": "market",
    "volume":    "0.001",
}
api.query_private("AddOrder", order_params)
```

---

## Strategy Contract

Every strategy must follow this interface. `agent.py` calls only `analyze()`.

```python
class MyStrategy:
    def __init__(self, config: dict):
        pass

    def analyze(self, symbol: str, provided_rates: dict | None = None) -> dict | None:
        """
        Live mode:    provided_rates = None → fetch from Kraken
        Backtest mode: provided_rates = {"M15": list[dict]}
        Returns signal dict or None.
        """
        pass
```

**Signal dict shape:**
```python
{
    "symbol":            str,    # e.g. "XBTUSD"
    "direction":         str,    # "BUY" | "SELL"
    "strategy_name":     str,    # e.g. "frost_kraken"
    "order_type":        str,    # "MARKET"
    "entry_price":       float,
    "suggested_sl":      float,
    "suggested_tp":      float,
    "risk_reward_ratio": float,
    "volume":            float,  # BTC volume e.g. 0.001
    "probability":       str,    # "HIGH" | "MEDIUM"
    "structure":         dict,   # strategy metadata
}
```

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
    "volume": float,  # trade volume
}
```

Kraken OHLC API returns: `[time, open, high, low, close, vwap, volume, count]`
We map index 0→time, 1→open, 2→high, 3→low, 4→close, 6→volume.

---

## Kraken Pair Names

Kraken uses non-standard pair names. Important ones:

| Common Name | Kraken Name |
|---|---|
| BTC/USD | XBTUSD |
| ETH/USD | ETHUSD |

Always use Kraken's naming in API calls. XBTUSD not BTCUSD.

---

## Firebase Data Structure

```
quantifyx/
  signals/
    {push_id}/
      symbol, direction, strategy_name
      entry_price, suggested_sl, suggested_tp
      risk_reward_ratio, volume, probability
      timestamp

  trades/
    {push_id}/
      symbol, direction, volume
      txid, status, timestamp

  status/
    is_running        bool
    last_updated      string
    active_strategy   string
```

---

## Environment Variables (`.env`)

```
KRAKEN_API_KEY=         # QuantifyX-Agent trading key
KRAKEN_API_SECRET=      # QuantifyX-Agent trading secret
PRISM_API_KEY=          # PRISM market data key
```

Never commit `.env`. It is in `.gitignore`.

---

## Kraken API Keys

Two keys are required:

**QuantifyX-Agent** (used by the bot)
- Query Funds ✅
- Query Open Orders & Trades ✅
- Query Closed Orders & Trades ✅
- Create & Modify Orders ✅
- Cancel & Close Orders ✅
- Withdraw ❌ never

**QuantifyX-Leaderboard** (submitted to lablab.ai)
- Query Funds ✅
- Query Open Orders & Trades ✅
- Everything else ❌

---

## Known Constraints and Issues

- Kraken CLI binary is Linux/Mac only — no Windows binary in v0.3.0.
  QuantifyX uses `krakenex` (Python REST wrapper) instead.
  This covers 100% of required functionality.

- Git Bash on Windows cannot execute Linux ELF binaries even with the
  `.tar.gz` extracted. Do not attempt to run the Kraken CLI binary on Windows
  without WSL or a Linux machine.

- WSL on company laptops is risky — IT policy may prohibit it.
  Stick with `krakenex` on Windows.

- The `.venv` path must be explicit when running scripts in PowerShell
  because the activated venv sometimes resolves to an older Brave venv.
  Always use the full path:
  `& "...\QuantifyX\.venv\Scripts\python.exe" script.py`

- Frost session filter blocks signals outside 00:00–06:00 UTC.
  For testing during the day, pass candles directly:
  `f.analyze('XBTUSD', provided_rates={'M15': candles})`

- BTC ATR during active trading hours is $400–600.
  MAX_ATR_PIPS set to 600.0 to accommodate this.
  During Asian session it drops to $200–350 — natural filter.

- BTC trend slope during active hours: ~70 units/candle observed.
  Threshold set to 100.0. Asian session slope expected lower.
  Recalibrate if too many false trending rejections occur overnight.

- Kraken balance returns empty dict `{}` if account has no funds.
  This is normal — not an API error.

- python-dotenv must be installed into the correct venv.
  If `ModuleNotFoundError: No module named 'dotenv'` appears,
  run: `& "...\QuantifyX\.venv\Scripts\python.exe" -m pip install python-dotenv`