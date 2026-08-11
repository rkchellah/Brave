# Brave — Project Checklist

## Phase 0 — Environment Setup ✅ COMPLETE

- [x] Python venv created: `.venv`
- [x] Dependencies pinned in `requirements.txt` and installed into the correct venv
      MetaTrader5, firebase-admin, langgraph, openai, finnhub-python, requests, numpy
- [x] `.gitignore` configured — `config.py`, `serviceAccountKey.json`, `logs/`, `__pycache__/` excluded
- [x] `config.py` populated with MT5, Firebase, DeepSeek and Finnhub credentials
- [x] `serviceAccountKey.json` downloaded to project root
- [x] Environment-variable overrides supported for every config value
- [x] `validate_config()` fails fast on invalid values at startup

---

## Phase 1 — Broker + Firebase Connection ✅ COMPLETE

- [x] MT5 terminal installed, Algo Trading enabled
- [x] `mt5.initialize()` + `mt5.login()` working against the RoboForex account
- [x] Startup retries — 3 attempts, the terminal is often still booting
- [x] Mid-run reconnect on dropped terminal connection
- [x] Firebase Realtime Database connected via `firebase-admin`
- [x] Command listener active — start/stop from the app
- [x] Default nodes seeded on first run (`bot_config`, `brave_config`)
- [x] Health check publishing to `health/` every 10 minutes

---

## Phase 2 — Flow Strategy ✅ COMPLETE

- [x] `src/flow.py` — H1 fractal trend + M15 sweep+reclaim
- [x] AOI detection via zone clustering, minimum 2 touches in a 15-pip band
- [x] Session filter — Pre-London, London, Bridge, NY (UTC)
- [x] ATR-based SL (1.2× ATR M15), 1.5:1 minimum RR
- [x] Backtested on 50,000 M15 candles across EURUSD + GBPUSD
- [x] Strategy contract followed — `analyze()` returns signal dict or None

---

## Phase 3 — LangGraph Pipeline ✅ COMPLETE

- [x] `src/graph.py` — DETECT → ANALYSE → RISK_CHECK → EXECUTE / HITL
- [x] Conditional routing on `BraveState`
- [x] Compiled graph cached at module level
- [x] Every node catches its own exceptions and routes safely to END
- [x] Finnhub headlines fetched and keyword-filtered per symbol
- [x] DeepSeek returns CONFIRM / OPPOSE / UNCERTAIN with a one-sentence reason
- [x] DeepSeek timeout, retry, and defensive response parsing
- [x] API failure degrades to UNCERTAIN — never CONFIRM or OPPOSE
- [x] Verdict and headlines published to `news_analysis/` for the Insights screen

---

## Phase 4 — Execution Layer ✅ COMPLETE

- [x] `src/trade_executor.py` — one order path for AUTO and MANUAL
- [x] Signal validation — required fields, numeric prices, SL/TP on the correct side
- [x] Risk-based lot sizing clamped to broker volume step/min/max and `LOT_SIZE`
- [x] Price, SL and TP rounded to symbol digits
- [x] Broker stops level respected
- [x] Order never sent without both SL and TP
- [x] Filling-mode fallback (IOC → FOK → RETURN)
- [x] Transient retcodes retried once at a refreshed price
- [x] Every execution appended to `logs/trades/trades_YYYY-MM-DD.csv`
- [x] Every DETECT attempt appended to `logs/flow_attempts.csv` (near-misses included)
- [x] Full setups that leave DETECT appended to `logs/trade_log.csv`
- [ ] Live execution verified end-to-end on the demo account

---

## Phase 5 — Human-in-the-Loop ✅ COMPLETE

- [x] UNCERTAIN sentiment routes to `pending_signals/`
- [x] MANUAL execution mode routes every signal to `pending_signals/`
- [x] Signals expire after `SIGNAL_EXPIRY_SECONDS` (default 180)
- [x] App shows Confirm / Reject with a live countdown
- [x] `_process_pending_signals()` executes confirmations each cycle
- [x] Signal claimed (`EXECUTING`) before the order is sent — no double execution
- [x] Confirmations arriving after expiry are refused
- [x] `scripts/clear_pending_signals.py` for clearing stale test data
- [ ] Full HITL test: signal → phone → confirm → order on the demo account

---

## Phase 6 — Mobile App ✅ COMPLETE

- [x] Dashboard — balance, equity, open P&L, positions, AUTO/MANUAL, start/stop
- [x] Signals — Confirm/Reject with countdown and status history
- [x] Insights — DeepSeek verdict and the Finnhub headlines behind it
- [x] Alerts — executed trades and execution failures
- [x] Settings — Broker Account, Flow toggle, health
- [x] Error boundary — a render error no longer white-screens the app
- [x] Firebase read errors surfaced as banners instead of silent stale data
- [x] All writes report failures to the user
- [ ] Release APK built and installed via EAS

---

## Phase 7 — App-Managed Broker Credentials ✅ COMPLETE

- [x] `mt5_config` node defined at `users/{USER_ID}/mt5_config`
      (login, password, server, updated_at, updated_by)
- [x] `bot.py` reads `mt5_config` at startup, before `mt5.login()`
      Firebase primary, `config.py` fallback — fallback kept deliberately
- [x] Firebase wins only when login + password + server are all present and valid;
      partial config falls back rather than attempting a doomed login
- [x] Credentials cached per process — a reconnect never switches accounts mid-session
- [x] Credential source logged (`Firebase` / `config.py fallback`); password never logged
- [x] Firebase init moved ahead of MT5 init in `__init__`
- [x] `src/seed_mt5_config.py` — one-time seed from config.py, `--force` to overwrite
- [ ] Seed script run against live Firebase and verified
- [x] Mobile: Settings → Broker Account card (login / password / server + Save)
- [x] Saved password shown masked, never rendered back; EDIT clears for replacement;
      untouched password is not overwritten on save
- [x] UI states the restart requirement in the confirmation and in persistent copy
- [ ] End-to-end test: edit credentials in app → restart bot → confirm Firebase source in log

---

## Known Issues Log

| Date | Issue | Status | Fix |
|---|---|---|---|
| Aug 9 2026 | `src/flow.py` and `src/news_filter.py` deleted in commit `aaaf262` but still imported by `bot.py` / `graph.py` — bot could not start | Resolved | Restored both from git history |
| Aug 9 2026 | `App.js` called `SplashScreen.hideAsync()` with no import — ReferenceError crash on app launch | Resolved | Removed the orphaned splash logic |
| Aug 9 2026 | `SignalTimer` called `setDbReady` from another component's scope; countdown never ticked | Resolved | Local interval state, guards for missing `expires_at` |
| Aug 9 2026 | Confirming a MANUAL signal in the app did nothing — consumer only reachable from orphaned `execute_signal` | Resolved | `_process_pending_signals()` executes confirmations each cycle |
| Aug 9 2026 | `node_execute` used flat lot size, no digit rounding, unguarded `tick.ask`, hardcoded IOC filling | Resolved | New `trade_executor.py` — validation, risk sizing, rounding, filling fallback |
| Aug 9 2026 | Risk check counted positions but not pending orders — one signal could stack an order per cycle | Resolved | `_count_positions()` counts both |
| Aug 9 2026 | Non-dict `strategy_config` in Firebase crashed strategy gating | Resolved | `isinstance` guard in `_flow_enabled()` |
| Aug 9 2026 | Dashboard rendered hardcoded placeholder figures ($350.61, -20.6%) when bot offline | Resolved | Real values or em-dash, never fake data |
| Aug 9 2026 | Finnhub general-news fetched per symbol per cycle — free-tier rate limit | Resolved | Module-level feed cache, 5-minute TTL |
| Aug 9 2026 | `mt5_config` stores the broker password in plaintext in Realtime Database | Open — accepted | Mitigate with UID-scoped DB rules; encryption-at-rest not implemented |
| Aug 9 2026 | LangChain Pydantic v1 warning on Python 3.14 | Open — monitored | Non-blocking; `graph.py` breaks at import if v1 support is dropped |
| Aug 10 2026 | Broker password and both API keys hardcoded as literal defaults in `config.py` | Resolved | Keys moved to gitignored `.env` (`load_dotenv()` in `config.py`); password prompted at startup via new `src/credentials.py`, never stored |
| Aug 10 2026 | Finnhub key leaked to GitHub in `sentiment_log.2026-04-06` (logged inside a Finnhub error URL) | Resolved | Key rotated Aug 10 2026, old one revoked and verified live; history not rewritten — rotation kills the leaked value |
| Aug 10 2026 | MT5 password leaked to GitHub in `backtest/backtest_flow.py:88` @ `e40ec49` | Open — accepted | Demo account, risk accepted; password no longer in any source file |
| Aug 10 2026 | `src/seed_mt5_config.py` writes the broker password to Firebase in plaintext | Open — accepted | Now warns and requires confirmation before writing; see the `mt5_config` row above |

---

## Backtest Results (Flow)

| Metric | Result |
|---|---|
| Pairs | EURUSD, GBPUSD |
| Candles | 50,000 M15 (~17 months) |
| Total trades | 372 |
| Win rate | 41% |
| Profit factor | 1.24 |
| Total return | +65.8% |
| Max drawdown | 14.6% |
