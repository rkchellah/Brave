# Brave — Project Checklist

## Phase 1 — Core Infrastructure ✅ COMPLETE
- [x] MT5 connection + login
- [x] Firebase Realtime Database integration
- [x] Command listener (start/stop from mobile/terminal)
- [x] Health check (MT5 + Firebase + account status)
- [x] Market status tracking per symbol
- [x] Dynamic pair selection (spread + volatility scoring)
- [x] Trade logging to Firebase
- [x] Alert push to Firebase on signal
- [x] config.py credential management (never committed)
- [x] Bot status pushed to Firebase every cycle

---

## Phase 2 — Thunder Strategy + Stability ✅ COMPLETE

### Thunder Strategy
- [x] `thunder.py` — EMA 8/13/21 stack, ATR-scaled, M15/H4
- [x] `backtest_thunder.py` — full backtester with CSV cache + reporting
- [x] Backtest validated — 273% return, 1.16 profit factor, 32.4% win rate
- [x] Thunder wired into live `bot.py` via strategy registry
- [x] Live demo test confirmed — trades executing on MT5

### Phase 2 Bug Fixes + Stability
- [x] `firebase_enabled` AttributeError fixed — attribute now set first in `__init__`
- [x] Session filter removed — Brave trades 24/7 regardless of session
- [x] **Duplicate order bug fixed** — `_count_positions()` now counts pending STOP
      orders AND active positions (was only counting active positions before,
      causing the same signal to stack 10+ orders per hour)
- [x] **Log rotation added** — `TimedRotatingFileHandler`, daily files, 7 days kept
- [x] **News filter added** — `news_filter.py` pauses trading ±30 min around
      high-impact ForexFactory events per symbol's currencies
- [x] Firebase SSE timeout downgraded to DEBUG — no longer spams logs

### Known Remaining Issues
- [ ] US30/NAS100 returning no data from broker (broker may not support indices on demo)
- [ ] USDJPY appearing in pair selection — not in original Thunder backtest symbols

---

## Phase 3 — Mobile App ❌ NOT STARTED
- [ ] Choose stack: React Native / Flutter / PWA
- [ ] Real-time bot status display (balance, equity, open positions)
- [ ] Start / Stop controls → writes to Firebase `commands/action`
- [ ] Strategy switcher → writes to Firebase `brave_config/active_strategy`
- [ ] Alerts feed (reads `users/{id}/alerts`)
- [ ] Trade history view (reads `users/{id}/trades`)
- [ ] News event display (upcoming high-impact events per active pairs)

---

## Phase 4 — Production Hardening ❌ NOT STARTED
- [ ] VPS setup (Windows VPS with MT5 terminal pre-installed)
- [ ] Auto-restart on crash (Task Scheduler or NSSM service wrapper)
- [ ] Kill switch — auto-stop if drawdown exceeds X% in a session
- [ ] Second strategy for Brave bundle (pending decision after more demo data)
- [ ] Live account migration checklist

---

## Backtest Results (Thunder — 3 pairs, ~10,000 H4 candles each)

| Metric           | Result        |
|------------------|---------------|
| Total trades     | 4,249         |
| Win rate         | 32.4%         |
| Profit factor    | 1.16          |
| Total P&L        | +$820.97      |
| Total return     | +273.66%      |
| Max drawdown     | 21.12%        |
| Best pair        | GBPUSD (+342) |
| Best session     | NY (+$506)    |

Note: US30 and NAS100 returned no data during backtest (broker limitation on demo).
Backtest was run on EURUSD, GBPUSD, XAUUSD only.

---

## Immediate Next Steps
1. Watch demo account for 2 weeks — do NOT change Thunder parameters during this period
2. Log real win rate vs backtest win rate — expect some divergence, that is normal
3. Decide on second strategy for Brave bundle based on demo observations
4. Fix pair selection to exclude USDJPY until it is backtested on Thunder
