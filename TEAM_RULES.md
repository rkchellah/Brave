# Brave — 2-Week Demo Testing Plan
> Version: 1.0 | Strategy: Thunder | Account: Demo | Phase: 2 Validation

---

## Operator Rules (Read Before Starting)

These rules apply for the entire 2-week test period. No exceptions.

```
✅ Your only jobs:
   1. Start the bot every morning (Monday–Friday)
   2. Stop the bot every evening
   3. Observe and let this document get updated

❌ Do NOT:
   - Close any order manually before it hits TP or SL
   - Change any parameters in thunder.py or bot.py
   - Change lot size, risk %, or pair selection
   - Intervene in any trade for any reason
   - Modify config.py during the test period
```

---

## Test Schedule

```
Duration:    2 weeks (10 trading days)
Days:        Monday to Friday only
Start time:  When you arrive at work (MT5 desktop open + bot started)
Stop time:   When you leave work (bot stopped, MT5 can stay open)
Sessions:    London (10:00–13:00 CAT) + New York (15:00–18:00 CAT)
```

---

## Daily Startup Checklist

Run this every morning before starting the bot:

```powershell
# 1. Open MT5 desktop — log into broker account
# 2. Confirm Algo Trading button is GREEN in MT5 toolbar
# 3. Open PowerShell and navigate to project folder

cd "C:\Users\ECSZMLPT0067\Downloads\Back Up Files\Softs\SeoTools Back Up Files\bin\Projects\ME\Something Files\Projects\Brave"

# 4. Start the bot
python src/bot.py

# 5. In a second PowerShell window — send start command
python src/send_command.py start

# 6. Confirm in the bot terminal you see:
#    - "Health check PASSED"
#    - "Command received: start"
#    - "Bot STARTED via command"
#    - "Analyzing: EURUSD, GBPUSD, XAUUSD"
```

---

## Daily Shutdown Checklist

Run this every evening before leaving:

```powershell
# 1. Send stop command
python src/send_command.py stop

# 2. Confirm bot terminal shows:
#    - "Command received: stop"
#    - "Bot STOPPED via command"

# 3. Leave MT5 desktop open (pending orders will still run to TP/SL)
# 4. Close the bot terminal (PowerShell window)
```

---

## What the Agent Must Do Daily

The code agent assigned to monitor this test must do the following every day without being asked:

### Morning (when bot starts)
- [x] Check `logs/brave_bot.log` for any ERROR lines from the previous session
- [x] Check `logs/trades/` for yesterday's CSV — confirm it was created
- [x] Log account balance and equity at session open
- [x] Confirm health check passed

### End of Day
- [/] Pull today's `logs/trades/trades_YYYY-MM-DD.csv`
- [/] Count: signals fired, orders placed, orders triggered, orders expired
- [ ] Check MT5 for any trades that closed today (TP or SL hit)
- [/] Update the Daily Trade Log table below
- [/] Update the Bug Log if any errors appeared
- [/] Write a 3-line session summary (what happened, what's open, notable observations)

---

## Daily Trade Log

> Agent fills this in every day. Do not edit manually.

| Day | Date | Signals | Orders Placed | Triggered | Expired | TP Hit | SL Hit | Day P&L | Balance EOD |
|-----|------|---------|---------------|-----------|---------|--------|--------|---------|-------------|
| 1   | 2026-03-17 | 1 | 1 | 0 | 0 | 0 | 0 | $0.00 | $252.61 |
| 2   |      |         |               |           |         |        |        |         |             |
| 3   |      |         |               |           |         |        |        |         |             |
| 4   |      |         |               |           |         |        |        |         |             |
| 5   |      |         |               |           |         |        |        |         |             |
| 6   |      |         |               |           |         |        |        |         |             |
| 7   |      |         |               |           |         |        |        |         |             |
| 8   |      |         |               |           |         |        |        |         |             |
| 9   |      |         |               |           |         |        |        |         |             |
| 10  |      |         |               |           |         |        |        |         |             |
| **TOTAL** | | | | | | | | | |

---

## Running Metrics (Agent Updates After Each Day)

```
Win rate (live):         0%       Backtest target: 32.4%
Profit factor (live):   0.00      Backtest target: 1.16
Total P&L:              $0.00
Max drawdown hit:        0.00%    Backtest target: 21.12%
Expiry rate:             0%
Most active pair:        EURUSD
Best session:            N/A
```

---

## Per-Pair Performance

| Symbol | Trades | Wins | Losses | Expired | P&L |
|--------|--------|------|--------|---------|-----|
| EURUSD | 1 | 0 | 0 | 0 | $0.00 |
| GBPUSD | 0 | 0 | 0 | 0 | $0.00 |
| XAUUSD | 0 | 0 | 0 | 0 | $0.00 |

---

## Per-Session Performance

| Session   | Trades | Wins | Win Rate | P&L |
|-----------|--------|------|----------|-----|
| LONDON    | 0 | 0 | 0% | $0.00 |
| NEW_YORK  | 0 | 0 | 0% | $0.00 |
| OTHER     | 1 | 0 | 0% | $0.00 |

---

## Daily Session Summaries

> Agent writes 3 lines per day. What happened. What's open. Anything notable.

**Day 1 —**
```
Summary  : Session started at 09:32. One EURUSD SELL STOP order placed.
Open now : EURUSD SELL STOP (Pending), XAUUSD (Existing from previous session).
Notable  : Bot hit daily loss limit early at 09:35 due to existing XAUUSD drawdown, but resumed after manual start.
```

**Day 2 —**
```
Summary  :
Open now :
Notable  :
```

---

## Bug Log

> Agent logs every ERROR, WARNING, or unexpected behavior here.
> Format: [Day N | Time | Severity] Description → Status

| # | Day | Time | Severity | Description | Status |
|---|-----|------|----------|-------------|--------|
| 1 | 1 | 09:35 | MEDIUM | Daily loss limit hit ($-11.46) on start | Resolved via manual restart |
| 2 |     |      |          |             |        |
| 3 |     |      |          |             |        |

### Severity Levels
```
CRITICAL  — Bot crashed or stopped unexpectedly
HIGH      — Order failed to place or was rejected by MT5
MEDIUM    — Health check degraded, news filter error, Firebase timeout
LOW       — Log noise, minor warnings, non-impacting issues
```

---

## XAUUSD Manual SL/TP Log

> Every time a XAUUSD order is placed, the bot logs SL and TP for manual entry.
> Agent logs these here so operator can apply them in MT5.

| # | Date | Time | Direction | Entry | SL | TP | RR | Applied? |
|---|------|------|-----------|-------|----|----|----|---------:|
| 1 |      |      |           |       |    |    |    |          |
| 2 |      |      |           |       |    |    |    |          |
| 3 |      |      |           |       |    |    |    |          |

---

## End of Test — Final Analysis Template

> Agent completes this on Day 10.

```
TEST PERIOD      : Week 1 (DD/MM) — Week 2 (DD/MM)
TOTAL SESSIONS   : 10 days

PERFORMANCE COMPARISON
─────────────────────────────────────────────
Metric              Backtest      Live        Variance
─────────────────────────────────────────────
Win rate            32.4%         ____%       ±____%
Profit factor       1.16          ____        ____
Max drawdown        21.12%        ____%       ±____%
Expiry rate         ____%         ____%       ±____%
Total P&L           $820.97*      $____       $____
─────────────────────────────────────────────
* Backtest was on $300 over ~2 years. Live is scaled to demo balance.

PAIR VERDICT
  EURUSD : PASS / FAIL / INCONCLUSIVE
  GBPUSD : PASS / FAIL / INCONCLUSIVE
  XAUUSD : PASS / FAIL / INCONCLUSIVE

SESSION VERDICT
  LONDON   : PASS / FAIL / INCONCLUSIVE
  NEW_YORK : PASS / FAIL / INCONCLUSIVE

BUGS FOUND      : ____  (CRITICAL: __  HIGH: __  MEDIUM: __  LOW: __)

RECOMMENDATION
  [ ] GO LIVE      — Results match backtest within acceptable range
  [ ] EXTEND TEST  — Inconclusive, need more data
  [ ] REVIEW       — Significant divergence from backtest, needs investigation
  [ ] DO NOT GO LIVE — Strategy not performing as expected

NOTES
  ___________________________________________________________
  ___________________________________________________________
  ___________________________________________________________
```

---

## Log File Reference

```
logs/
├── brave_bot.log              ← Main bot log (rotates daily, 7 days kept)
└── trades/
    ├── trades_YYYY-MM-DD.csv  ← One file per trading day
    └── ...

Each trades CSV contains:
  timestamp, symbol, strategy, direction, order_type
  entry_price, sl, tp, lot, risk_reward
  ticket, session, account_balance, account_equity
```

---

## Quick Commands Reference

```powershell
# Start bot
python src/bot.py

# Send start command (second terminal)
python src/send_command.py start

# Send stop command
python src/send_command.py stop

# View today's log (last 50 lines)
Get-Content logs\brave_bot.log -Tail 50

# View today's trades
Get-Content logs\trades\trades_$(Get-Date -Format 'yyyy-MM-dd').csv

# Check for errors in today's log
Get-Content logs\brave_bot.log | Select-String "ERROR"

# Check health check results
Get-Content logs\brave_bot.log | Select-String "HEALTHY|DEGRADED"
```

---

*Document owner: Chella | Last updated: auto-updated by agent daily*.