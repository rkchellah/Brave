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
- [x] **Soft Session Filter added** — Brave trades 24/7 but tags trades with LONDON/NEW_YORK/OTHER for analysis
- [x] **Duplicate order bug fixed** — `_count_positions()` now counts pending STOP
      orders AND active positions (was only counting active positions before,
      causing the same signal to stack 10+ orders per hour)
- [x] **Log rotation added** — `TimedRotatingFileHandler`, daily files, 7 days kept
- [x] **News filter added** — `news_filter.py` pauses trading ±30 min around
      high-impact ForexFactory events per symbol's currencies
- [x] Firebase SSE timeout downgraded to DEBUG — no longer spams logs
- [x] **Firebase init bug fixed** — `firebase_enabled` flag now set immediately after connection to ensure initial status push isn't skipped.
- [x] **MT5 Terminal check added** — bot now detects if "Algo Trading" button is OFF in MT5 and logs a critical error.
- [x] **Daily Loss Limiter (Kill Switch) added** — pauses bot if daily loss exceeds 5%
- [x] **XAUUSD SL/TP dropped bug fixed** — `bot.py` now precisely rounds Entry, SL, and TP to `info.digits` for each symbol to prevent MT5 from silently stripping invalid decimals on metals/indices.
- [x] **AI Sentiment Service added** — `sentiment_service.py` uses GPT-4 and Grok-3 to analysis market sentiment and push to Firebase.

### Known Remaining Issues
- [ ] US30/NAS100 returning no data from broker (broker may not support indices on demo)
- [x] USDJPY appearing in pair selection — excluded until backtested

---

## Phase 3 — Mobile App 🔄 IN PROGRESS

### Setup Complete
- [x] Stack chosen: React Native with Expo SDK 52
- [x] Expo CLI + EAS CLI installed globally
- [x] `brave-app` project scaffolded with `create-expo-app`
- [x] Firebase packages installed (`@react-native-firebase/app`, `@react-native-firebase/database`)
- [x] Navigation installed (`@react-navigation/native`, `@react-navigation/bottom-tabs`)
- [x] `expo-notifications` installed
- [x] App running live on physical Android device via Expo Go (SDK 52)

### ⚠️ Setup Challenge — Expo SDK Version Mismatch
**Problem:** After scaffolding with `create-expo-app`, scanning the QR code on the
physical device returned: *"Project is incompatible with this version of Expo Go."*

**Root cause:** Initial tests with SDK 54/55 (latest) revealed that the user's Expo Go version was older.
**What fixed it:** Downgraded the project to **SDK 52** for maximum compatibility:
```bash
cd brave-app
npm install --legacy-peer-deps
npx expo start --clear
```
App loaded on device showing: "Open up App.js to start working on your app!"
Lesson for future devs: Match the project SDK to the device SDK (52 is currently the safest bet for stability).
### Screens To Build
- [x] Dashboard — balance, equity, P&L, bot status, start/stop button
- [x] Signals — pending signals with Confirm/Reject (Human-in-the-Loop)
- [x] AI Insights — sentiment scores for EURUSD, GBPUSD, XAUUSD
- [x] Alerts — trade history feed
- [x] Settings — execution mode AUTO/MANUAL, strategy switcher

### Human-in-the-Loop (HITL) Architecture
- [x] Execution mode designed — AUTO and MANUAL
- [x] Firebase schema extended — `brave_config/execution_mode`
- [x] `config.py` updated — `EXECUTION_MODE = "AUTO"`, `SIGNAL_EXPIRY_SECONDS = 180`
- [x] `bot.py` — execution mode gate added to `execute_signal()`
- [x] `bot.py` — MANUAL mode pushes pending signal to Firebase and waits
- [x] `bot.py` — 3-minute expiry logic for MANUAL signals
- [x] Mobile app — AUTO/MANUAL toggle on Dashboard
- [x] Mobile app — Signals screen with Confirm/Reject buttons
- [ ] Push notifications via Firebase Cloud Messaging (FCM)

### Mobile App Setup Challenges Log
- [x] **SDK mismatch** — `create-expo-app@latest` scaffolds SDK 55, phone has SDK 54.
      Fix: manually replace `package.json` with all versions locked to SDK 54
      before running `npm install --legacy-peer-deps`
- [x] **PlatformConstants red screen** — caused by `react-native-screens` versions
      above 3.34.0 using TurboModules not supported in Expo Go SDK 54.
      Fix: lock `react-native-screens` to exactly `3.34.0` in `package.json`
- [x] **Windows path length error** — `node_modules` nesting exceeds Windows 260-char
      limit. Fix: enable long paths via registry
      `HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem LongPathsEnabled = 1`
- [x] **PowerShell multiline paste** — commands merge into one line e.g.
      `cd brave-appnpm install`. Fix: always paste one command per line in PowerShell
- [x] **VPN interference** — Proton VPN on phone blocks local network connection to
      Metro bundler. Fix: disable VPN on phone before scanning QR code
- [x] **`@react-native-firebase` incompatible with Expo Go** — native packages require
      a compiled APK build. Fix: uninstall and use JS Firebase SDK (`firebase@10.14.1`)
- [x] **npm version drift** — each `npm install` pulls newer incompatible versions.
      Fix: lock all versions in `package.json` before any install
- [x] **Firebase Permission denied** — app showed $0.00 because Firebase rules
      were set to deny public reads. Fix: set rules to `.read: true, .write: true`
      for development. Tighten with auth rules before live account.

### send_command.py Status
- [ ] Verify `send_command.py start` triggers bot correctly via Firebase listener
- [ ] Verify `send_command.py stop` halts bot correctly via Firebase listener
- [ ] Document any observed latency between command and bot response

---

## Phase 4 — Production Hardening ❌ NOT STARTED
- [ ] VPS setup (Windows VPS with MT5 terminal pre-installed)
- [ ] Auto-restart on crash (Task Scheduler or NSSM service wrapper)
- [x] Kill switch — auto-stop if drawdown exceeds 5% in a daily session
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
4. [x] Fix pair selection to exclude USDJPY until it is backtested on Thunder
