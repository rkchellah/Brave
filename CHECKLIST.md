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
- [x] Health includes `terminal_autotrading_enabled` (local AutoTrading toggle) in HEALTHY/DEGRADED
- [x] True→False AutoTrading edge pushes a Firebase alert
- [x] Settings shows AutoTrading with the same freshness gate as MT5 Connected

---

## Phase 2 — Strategy (Flow, retired — superseded by Frost 2026-08-14) ✅ COMPLETE

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
- [x] Every DETECT attempt appended to `logs/frost_attempts.csv` (near-misses included)
- [x] Max-trades skip before DETECT (`reason=max_trades_reached`) — same early-exit as news_filter_pause
- [x] Full setups that leave DETECT appended to `logs/trade_log.csv`
- [x] Closed MT5 tickets back-fill `exit_price` / `exit_reason` / `pnl` on that CSV row
- [ ] Live execution verified end-to-end on the demo account

---

## Phase 5 — Human-in-the-Loop ✅ COMPLETE

- [x] UNCERTAIN sentiment routes to `pending_signals/`
- [x] Fetch/tool failure is `analysis_unavailable`, not HITL — AUTO skips; MANUAL HITL tagged `hitl_kind`
- [x] Duplicate PENDING/EXECUTING HITL on the same symbol is skipped (`duplicate_pending_signal`)
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
- [x] Settings — Broker Account, Frost toggle, health
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
| Aug 13 2026 | AUTO HITL asked for confirmation on Finnhub timeouts — indistinguishable from genuine mixed sentiment | Resolved | ANALYSE splits `data_unavailable` from `genuine_uncertainty`; AUTO skips (`analysis_unavailable`); MANUAL HITL tagged `hitl_kind` |
| Aug 13 2026 | USDJPY at 3-position cap still ran DETECT→ANALYSE (DeepSeek) five times before RISK_CHECK blocked | Resolved | `_check_signals` skips `run_brave_graph` when `_count_positions >= max_trades`; CSV `reason=max_trades_reached` |
| Aug 13 2026 | `trade_log.csv` never got SL/TP/PnL — `update_trade_outcome()` existed but nothing called it | Resolved | Fast-status loop reconciles `history_deals_get` against open tickets every 15s |
| Aug 16 2026 | News filter had no `SYMBOL_CURRENCIES` entry for USDCAD or EURCHF — both ship in `SYMBOLS`, and unmapped symbols fail open, so neither pair was ever paused around high-impact news | Resolved | Mappings added; fall-through now logs WARNING; `unmapped_symbols()` checked at startup |
| Aug 16 2026 | `attach_ticket()` claimed the newest un-ticketed HITL row, so with several signals queued on one symbol the ticket, exit price and P&L landed on the wrong rows (confirmed against `trades_2026-08-13.csv`) | Resolved | Matches on the confirmed signal's SL/TP; newest-row rule kept only as fallback |
| Aug 16 2026 | `bot.py` and `graph.py` implemented the daily loss limit differently — graph compared equity to *balance*, i.e. unrealised P&L, not the day's loss | Resolved | New `src/risk.py` owns the definition; both callers use it |
| Aug 16 2026 | Log paths were relative to the working directory — launching from `src/` created a second, unreconciled `src/logs/` tree | Resolved | `config.LOG_DIR` anchored to `PROJECT_ROOT`; all CSV writes routed through `trade_logger` |
| Aug 16 2026 | `run()` set `is_running = True` unconditionally, so a Stop set in the app was undone by the next process restart — regression of the Aug 10 fix, reintroduced by the v3.0 rewrite | Resolved | `_resume_run_state()` restores the stored `bot_status.is_running`, snapshotted before this process writes to it |
| Aug 16 2026 | `flow.py` used `datetime.utcnow()` for session gating — deprecated on Python 3.12+, and this project runs 3.14 | Resolved | `datetime.now(timezone.utc)` |
| Aug 16 2026 | `logs/trade_log.csv` destroyed during a `trade_logger` test — `_rewrite`'s default `path=LOG_PATH` bound at definition time and ignored a redirected module path | Resolved | Rebuilt all 66 rows from `brave_bot.log` + `flow_attempts.csv` (16,082 vs 16,103 bytes); `_rewrite` now requires path and columns explicitly |
| Aug 14 2026 | Strategy swapped Flow → Frost (mean reversion, Asian 00:00–06:00 UTC). Frost lacked the `last_attempt`/`_record_attempt` instrumentation `graph.node_detect` depends on — wiring it in as-is would have raised `AttributeError` on every no-signal cycle | Resolved | Parity `_record_attempt` added to `frost.py` with Flow's exact signature and CSV schema; `utcnow()` and root-logger issues fixed at the same time |
| Aug 14 2026 | Flow's Aug 11–14 dataset would have been mixed with Frost's under one filename | Resolved | Archived as `flow_attempts_archive_2026-08-11_to_14.csv`; Frost starts a fresh `flow_attempts.csv` |
| Aug 16 2026 | No test suite — every fix in `BUG_LOG.md` is one rewrite away from silently reopening, as the `is_running` regression demonstrates | Open | Highest-value next task; start with the gates that can stop a trade |

---

## Backtest Results (Flow — retired strategy, kept for comparison)

| Metric | Result |
|---|---|
| Pairs | EURUSD, GBPUSD |
| Candles | 50,000 M15 (~17 months) |
| Total trades | 372 |
| Win rate | 41% |
| Profit factor | 1.24 |
| Total return | +65.8% |
| Max drawdown | 14.6% |
