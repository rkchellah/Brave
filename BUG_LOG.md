# Brave — Bug Log
> Running log of bugs found during **live testing**. Focused on the root-cause *pattern*, not just the fix.
>
> A live-testing bug is not "fixed" until it has an entry here. Add the entry as part of the fix, not after.
> Keep the **Patterns Observed** tally at the bottom updated with every new entry.

---

## [2026-08-10] MT5 Connected showed "Yes" while stale
**Symptom:** Settings screen displayed `mt5_connected: true` with no bot running.
**Root cause:** No freshness check against `is_running` or `health.timestamp` age.
**Fix:** Added dual freshness gate before rendering the value — `brave-app/App.js`, MT5 connection status render.
**Pattern tag:** `stale-data-trusted-as-current`

---

## [2026-08-10] App Start button did nothing
**Symptom:** Tapping Start in the app updated Firebase but no trading loop began.
**Root cause:** No check that a `bot.py` process was actually running to consume the command; the command listener existed but nothing invoked the loop.
**Fix:** `run()` now starts paused and gates the loop on the `is_running` flag the listener sets — `src/bot.py`, `run()`.
**Pattern tag:** `no-process-check`

---

## [2026-08-10] News filter showed 0 events, silently fail-open
**Symptom:** `NewsFilter: Loaded 0 events` every cycle despite a live calendar with 74 events.
**Root cause:** Date/field schema mismatch caused every row to raise and be silently skipped; empty result defaulted `is_safe_to_trade` to `True`.
**Fix:** Fixed date/currency parsing for the current ForexFactory schema; added a `_fetch_failed` flag so empty-due-to-error now returns `False` (blocks trading) instead of being treated like empty-due-to-quiet-week (`True`) — `src/news_filter.py`, event loading + `is_safe_to_trade()`.
**Pattern tag:** `silent-fail-open`

---

## [2026-08-10] News filter never loaded a single event in any live session
**Symptom:** Every retained bot log from first NewsFilter usage onward shows `Loaded 0 events (0 high-impact)` — never a non-zero load.
**Blast radius:** Logged live session days with the filter effectively off: **2026-03-23, 03-31, 04-06, 04-07, 05-21, 08-09, 08-10** (~84+ calendar refreshes across ~7 session days). Parser shipped **2026-03-12** (`4bc04dc` / `b15d03b`) with `%m-%d-%YT%H:%M:%S%z` + `currency`; by the first surviving log (**Mar 23**) the live feed was already incompatible (ISO dates + `country`). File removed **2026-04-04** (QuantifyX), restored **2026-08-09** (`f0c6426`) with the same broken parser. Git cannot date the ForexFactory schema change — either the feed changed before Mar 23, or the parser was written against an outdated example and never matched production. Either way: **every logged Brave live session since Phase 2 news filtering ran with the news filter silently disabled** (fail-open → trade through high-impact windows).
**Root cause:** Not a regression that appeared later — the feature never successfully ingested the live feed in this environment. Distinct from the same-day parsing/fail-open *fix* entry above, which documents what was patched.
**Fix:** Covered by the schema + fail-closed patch in `src/news_filter.py` (see entry above). Requires a **bot process restart** — an already-running process keeps the broken in-memory code path until killed.
**Pattern tag:** `never-actually-worked`

---

## [2026-08-10] Broker password and API keys hardcoded in `config.py`
**Symptom:** No runtime symptom — the bot ran normally. Found by reading `config.py`: the live broker password and both API keys sat as literal string defaults, readable by anyone with the machine, a backup, or a screen-share.
**Root cause:** `_env_str(name, default)` was designed so env vars *could* override, but the real secrets were used as the fallback literals — so the secure path was optional and nobody took it. A default that works is a default nobody replaces.
**Fix:** Secrets removed from source. API keys moved to `.env` (gitignored) loaded by a new `load_dotenv()` in `config.py`; resolution is env var → `.env` → non-secret default. Broker password removed entirely — `src/credentials.py` prompts for it at startup with echo off and it is never written to disk. `bot.py._read_mt5_credentials()` resolves env → prompt → Firebase → config. Not hashable: MT5 authenticates with the real secret, so a salted SHA-256 *fingerprint* is logged instead to confirm which password was typed.
**Pattern tag:** `secret-in-source`

---

## [2026-08-10] Secrets committed to git history and pushed to GitHub
**Symptom:** No runtime symptom. Found by pickaxe-searching history during the fix above.
**Blast radius:** Finnhub key `d6t5bj9r…` in `sentiment_log.2026-04-06` — the key travelled inside a logged Finnhub error URL (`?token=…`), and the log file was committed before `.gitignore` covered `sentiment_log*`; present through `f8d46ba`, `684b2c5`, `cfc622a`, `c89a760`. MT5 password in `backtest/backtest_flow.py:88` @ `e40ec49` (2026-02-01), removed from the tree later but still in history. Repo is on GitHub (`rkchellah/Brave-trading-agent`), so both must be treated as public since their commit dates. DeepSeek key, `config.py` and `serviceAccountKey.json` were never tracked. HEAD tree is clean.
**Root cause:** Two separate paths, one class — a secret reached a file nobody thought of as a secret. `.gitignore` was written for the files known to hold credentials; a *log* that happened to echo a key in a URL was not one of them, and neither was a backtest script with a copy-pasted password block.
**Fix:** Finnhub key rotated 2026-08-10 and the old one revoked — verified live (`fetch_news_for_symbol`: 4 headlines EURUSD, 8 XAUUSD). MT5 password accepted as-is: demo account. History **not** rewritten — rotation makes the leaked values dead, and a force-push would break existing clones. Prevention is the `secret-in-source` fix above: with no secret in any source file, nothing is left to leak into a log or a copy-paste.
**Pattern tag:** `secret-in-committed-artifact`

---

## [2026-08-12] MT5 terminal AutoTrading was off — not a Brave bug
**Symptom:** Multiple execution attempts failed with `AutoTrading disabled by client`; five EURUSD retries yesterday, plus USDCAD attempts today that were separately blocked by DeepSeek OPPOSE.
**Root cause:** Not a code bug — the MT5 desktop terminal's own AutoTrading toggle was manually off. Confirmed visually: toolbar button lacked the green play-icon state; after clicking it, icon changed and trading resumed.
**Fix:** None needed in the order path. Health check now reads `terminal_info().trade_allowed` as `terminal_autotrading_enabled`, folds it into HEALTHY/DEGRADED, and pushes an Alerts-row on True→False edges — `src/bot.py`, `_health_check()`. Settings shows AutoTrading with the same freshness gate as MT5 Connected — `brave-app/App.js`. Operational note still stands: the toolbar toggle can reset on terminal restart; check it each session start.
**Pattern tag:** `operator-toggle-not-checked` (new tag — distinct from `stale-data-trusted-as-current`, since nothing was stale here; the actual live state was simply never verified before assuming it matched intent)

---

## [2026-08-12] Health check watched the wrong AutoTrading flag
**Symptom:** `Health check PASSED` logged repeatedly on 2026-08-11 and 2026-08-12 while every order attempt failed with `AutoTrading disabled by client`.
**Root cause:** `_health_check()` read `mt5.account_info().trade_allowed` (broker-side account permission) but never read `mt5.terminal_info().trade_allowed` (the local AutoTrading toolbar toggle) — two different flags, and the health check treated only one of them as ground truth for "can this bot actually place trades." `terminal_info()` was already being called in the same function for `.connected`, but `.trade_allowed` on that same object was left unread.
**Fix:** Added `terminal_autotrading_enabled` to the health payload, folded into the overall `healthy` computation. A True→False transition is detected against `self._last_health` and pushes a loud `AUTOTRADING_DISABLED` alert via `_push_alert`, not just a quiet `health_ref.set()`. Settings screen shows AutoTrading On/Off/Unknown with the same `mt5Trusted` freshness gate as MT5 Connected.
**Pattern tag:** `stale-data-trusted-as-current`

---

## [2026-08-12] Valid RR-2.0 signals silently rejected by float precision
**Symptom:** Flow found valid trend/AOI/sweep+reclaim setups that were then discarded — **13** occurrences across two days (EURUSD 08-11 15:02 UTC; USDCAD 08-12 07:05–07:16 UTC ×11; EURUSD 08-12 14:52 UTC), all logged as generic `signal_build_failed`.
**Root cause:** TP constructed via `sl_distance * MIN_RR`, then RR re-derived via `reward / risk` — the round-trip doesn't guarantee exact equality with `MIN_RR` due to floating point precision, so a strict `<` comparison rejected signals that were correct by construction. Compounded by instrumentation: the CSV reason string didn't distinguish this from a genuine `risk == 0` failure, undercounting real near-misses during the observation week.
**Evidence:** All 13 rows have a matching `Flow: RR 2.00 < 2.0 — skipping` line in `logs/brave_bot.log` — RR was exactly 2.00 to two decimals in every case, and none were `zero_risk`. A sweep of by-construction-exact setups across plausible ATR/point/price combinations rejects **25.3%** of the time.
**Fix:** `RR_EPSILON = 1e-6` tolerance on the RR comparison; `_build_signal()` now returns `(signal, reason)` and `analyze()` writes that reason through, splitting `signal_build_failed` into `rr_below_min` vs `zero_risk` — `src/flow.py`, `_build_signal()` + `analyze()` step 9. Historical CSV rows keep the old generic reason; they cannot be retroactively corrected.
**Pattern tag:** `float-precision-boundary-reject` (new tag)

---

## [2026-08-12] No notification when a signal enters HITL
**Symptom:** 7 EURUSD UNCERTAIN signals routed to `pending_signals` today, all expired unconfirmed — user was not looking at the app and had no other way to know.
**Root cause:** HITL write to Firebase had no accompanying push notification, only an in-app row.
**Fix:** **Not fixed — no push infrastructure exists to wire into.** Verified: `brave-app/package.json` has no `expo-notifications` (or any notification dependency); `app.json` declares no notification plugin or permissions; no `google-services.json`/FCM config anywhere in the repo; no device/Expo push token is stored in Firebase or read by the bot; `firebase_admin.messaging` is never imported — `src/bot.py` uses only `credentials` and `db`. The existing `_push_alert()` / `alerts_ref` path is an in-app Firebase row, which fails the same way: it only reaches a user already looking at the app. Delivering this needs a decision (Expo push + token storage + EAS credentials, or FCM direct, or an out-of-band channel such as Telegram/email), not a patch. Deliberately left unwired rather than shipped as a silent no-op.
**Pattern tag:** `silent-fail-open` — same class as the news filter: a safety/review mechanism exists but nothing tells the human it's waiting on them.

---

## [2026-08-13] trade_log.csv never recorded SL/TP/PnL
**Symptom:** Three open USDJPY fills this morning (tickets 2358145766, 2358145924, 2358174962) are in `logs/trade_log.csv` as EXECUTED with blank `exit_price`, `exit_reason`, and `pnl`. Those columns would stay empty after TP/SL unless someone edited the file by hand — MT5 history would be the only record.
**Root cause:** `update_trade_outcome()` was written to patch those columns, but nothing in `bot.py` or `trade_executor.py` ever called it. Signal-time write with no close reconciler.
**Fix:** Fast-status loop calls `_reconcile_closed_trades()` every 15s. Open tickets (CSV rows with a ticket and empty `exit_price`) are matched against `mt5.history_deals_get` OUT deals once the position is gone; `update_trade_outcome` writes the three columns. DEAL_REASON_SL/TP when the broker tags them, else whichever of the logged SL/TP the fill is closer to. Requires a **bot process restart**.
**Pattern tag:** `unwired-hook` (new tag — the helper existed; the call site did not)

---

## [2026-08-13] Maxed USDJPY still paid for DeepSeek five times
**Symptom:** USDJPY hit its 3-position cap, then continued re-running full DETECT→ANALYSE cycles five times in ~12 minutes — each calling Finnhub and DeepSeek (10–25s) before RISK_CHECK blocked a 4th trade. Outcome was correct (no extra order); the waste was the LLM call.
**Not this bug:** Those five UNCERTAIN verdicts were genuine mixed-sentiment judgments (dollar strength vs yen weakness). Logs show `[news_fetcher] USDJPY: 5 relevant articles` on each cycle — Finnhub succeeded. Distinct from the earlier `conflated-failure-modes` entry, where UNCERTAIN was caused by Finnhub timeouts and 0 articles.
**Root cause:** Position/order count is knowable cheaply, but the cap lived only at the end of the graph, after DETECT and ANALYSE had already run.
**Fix:** `_check_signals` skips `run_brave_graph` when `_count_positions(symbol) >= max_trades` (same sources RISK_CHECK uses: `config["max_trades"]`, MT5 positions + pending orders). Writes `reason=max_trades_reached` to `logs/flow_attempts.csv`, same early-exit pattern as `news_filter_pause`. RISK_CHECK is unchanged and remains the last-line gate. Requires a **bot process restart**.
**Pattern tag:** `late-cheap-gate` (new tag)

---

## [2026-08-13] AUTO HITL asked for confirmation on Finnhub timeouts
**Symptom:** AUTO mode asked for confirmation on signals where the reason was tool failure, not genuine uncertainty, indistinguishable from real HITL in the app. Confirmed twice today on USDJPY: DeepSeek returned UNCERTAIN both times because `[news_fetcher] All fetch attempts failed` (Finnhub connection timeout), not mixed sentiment. Both routed to HITL identically to a real judgment call.
**Root cause:** ANALYSE conflates "insufficient data due to fetch failure" with "sufficient data but ambiguous sentiment" — both produce the same UNCERTAIN verdict and identical HITL routing.
**Fix:** `fetch_news_for_symbol` now returns `(articles, data_ok)`. ANALYSE treats Finnhub timeout / 0 articles / DeepSeek API or empty reply as `analysis_unavailable`. AUTO aborts with that reason and does not HITL; MANUAL still HITL but `pending_signals` carries `hitl_kind: data_unavailable` vs `genuine_uncertainty`. App Signals/Insights pills distinguish the two. CSV outcome is `ANALYSIS_UNAVAILABLE`. Requires a **bot process restart**.
**Pattern tag:** `conflated-failure-modes` (new tag)

---

## [2026-08-13] Doubled USDJPY exposure from two independently-confirmed HITL signals
**Symptom:** Flow pushed two separate near-duplicate USDJPY SELL signals to `pending_signals` two minutes apart (11:17 and 11:19). Both were confirmed by the user and both executed — tickets 2358145766 and 2358145924, doubled position on the same directional idea.
**Root cause:** RISK_CHECK is blind to Firebase-pending signals; it only sees MT5 `positions_get` / `orders_get`. A HITL row is not an MT5 order, so the second cycle passed risk and pushed a second confirm slot.
**Fix:** `_open_hitl_key()` queries `pending_signals_ref` for PENDING or EXECUTING on the same symbol. RISK_CHECK fails with `duplicate_pending_signal` if one exists (also blocks AUTO execute while a HITL is outstanding). `node_hitl` skips the push with the same reason if routing still reaches it — `src/graph.py`.
**Pattern tag:** `duplicate-order-risk` (same class as the earlier AUTO-mode fix that counted MT5 pending orders, now shown to also apply to the HITL path)

---

## [2026-08-13] Flow has no memory of recently-traded AOI zones
**Symptom:** All three USDJPY trades executed today (tickets 2358145924, 2358145766, 2358174962) were re-entries into the same resistance zone (AOI 159.333) within a 6-minute window, not three independent setups. `MAX_TRADES` (3, per symbol) was fully consumed by one repeating thesis while GBPUSD and EURUSD sat unused in `aoi_too_far` the entire session — so the day's full risk budget went to one directional idea rather than diversified exposure.
**Root cause:** Flow's DETECT has no state between cycles — each 60s pass re-evaluates the same AOI fresh, with no check for "a signal was already taken on this zone recently." A sweep+reclaim oscillating a few pips inside the entry window across consecutive candles reads as N distinct "new" signals rather than one persisting setup.
**Fix:** Deferred — user is intentionally not changing strategy behavior mid-observation-week. To be addressed after the week closes. Candidate approaches to evaluate then: (a) per-AOI cooldown after a fill, (b) require price to exit and re-approach the zone before re-qualifying, (c) leave as-is if MAX_TRADES's hard cap is judged sufficient risk control on its own.
**Pattern tag:** `no-reentry-memory` (new tag — distinct from `duplicate-order-risk`, which was about the same signal reaching multiple execution paths; this is about the *strategy* re-generating genuinely new-looking signals from the same underlying setup, which is a design gap, not an infrastructure bug)

---

## [2026-08-16] News filter was inactive on USDCAD and EURCHF
**Symptom:** No runtime symptom — the filter logged normally and reported events loaded. Found by reading `news_filter.py` against `config.SYMBOLS`.
**Blast radius:** `SYMBOL_CURRENCIES` never had entries for USDCAD or EURCHF, but both have shipped in the default `SYMBOLS` list. `is_safe_to_trade()` returns `True` for any symbol not in the map, so **every USDCAD and EURCHF signal since those pairs were added traded through high-impact news windows with no pause at all** — including the 11 USDCAD attempts on 2026-08-12. The other four symbols were filtered correctly, which is why nothing looked wrong.
**Root cause:** A lookup table that must be exhaustive was treated as best-effort. The unknown-symbol branch was written as "don't block things we don't understand", which is the right instinct for an optional enrichment and exactly wrong for a safety gate — it converts a missing table row into silent permission.
**Fix:** Added USDCAD and EURCHF (plus EURGBP/EURJPY/GBPJPY/NZDUSD) to `SYMBOL_CURRENCIES`; the fall-through now logs a WARNING naming the symbol instead of returning quietly; new `unmapped_symbols()` is called from `BraveBot.__init__` so any symbol without a mapping is named at startup — `src/news_filter.py`, `src/bot.py.__init__`.
**Pattern tag:** `silent-fail-open`

---

## [2026-08-16] Manual-confirm tickets attached to the wrong signal rows
**Symptom:** Found while rebuilding `trade_log.csv` (see below). The three USDJPY fills on 2026-08-13 were cross-checked against `logs/trades/trades_2026-08-13.csv`, which records SL/TP at fill time. Tickets 2358145766 and 2358145924 were attached to each other's rows: the row with SL 159.434 carried the ticket belonging to the row with SL 159.430, and vice versa. Exit price and P&L followed the ticket, so both rows reported the other trade's result.
**Root cause:** `attach_ticket()` identified its target as "the newest un-ticketed HITL row for this symbol". Identity was inferred from recency instead of from anything actually identifying. With several signals queued on one symbol — routine for Flow, and guaranteed by the `no-reentry-memory` gap logged above — the user confirms an older signal while a newer one sits in the queue, and the newer row claims the ticket.
**Fix:** `attach_ticket()` now takes the confirmed signal's `sl`/`tp` and matches the row on levels, within a fraction of a pip; the newest-row rule survives only as a fallback when levels are absent or match nothing, since attaching to a wrong trade is worse than not attaching. `bot.py` passes them from the confirmed signal — `src/trade_logger.py`, `attach_ticket()`/`_patch_latest_hitl()`/`_match_by_levels()`; `src/bot.py._process_pending_signals()`.
**Pattern tag:** `identity-by-recency` (new tag — a record was located by "most recent match" where nothing guaranteed the most recent one was the right one)

---

## [2026-08-16] `trade_log.csv` destroyed during a test, rebuilt from logs
**Symptom:** `logs/trade_log.csv` went from 16,103 bytes (66 signal rows) to 190 bytes (1 row) during a round-trip test of `trade_logger`. Not a bot bug — an agent-caused incident during the cleanup work — but the mechanism was a real defect in the module.
**Root cause:** `_rewrite(rows, path=LOG_PATH, ...)` carried the module-level `LOG_PATH` as a **default argument**, which Python binds once at function-definition time. A test that reassigned `trade_logger.LOG_PATH` to a scratch directory redirected every other function but not this one, so `update_trade_outcome()` rewrote the real log. A default that silently ignores the module state it was copied from is indistinguishable from one that tracks it, right up until it destroys something.
**Fix:** `_rewrite(rows, path, columns)` now requires both explicitly — no defaults to go stale — with a comment stating why. Recovery: `trade_log.csv` holds derived data, so all 66 rows were rebuilt from `brave_bot.log` rotations (DETECT/ANALYSE/RISK_CHECK/HITL/EXECUTE lines) cross-checked against the intact `flow_attempts.csv` (67 `outcome=signal` rows, one of which never produced a trade_log row because the process was killed mid-ANALYSE). Rebuilt file is 16,082 bytes against the original 16,103. The damaged file is kept at `logs/trade_log.damaged-20260816.csv` and the rebuild at `logs/trade_log.reconstructed.csv`. The ticket mis-pairing above was found by this cross-check and is corrected in the rebuild.
**Pattern tag:** `default-bound-at-definition`

---

## [2026-08-16] Two disagreeing definitions of the daily loss limit
**Symptom:** No runtime symptom. `bot.py` measured session P&L against the equity the day opened at; `graph.py` measured `(balance - equity) / balance`, which is unrealised P&L on whatever is currently open. The second is not a daily loss at all — it reads as a large "daily loss" whenever a position is simply open and underwater, and reads zero after a realised loss closes.
**Root cause:** The same rule was implemented twice, in two files, at two different times. Neither was wrong where it was written; they drifted because nothing owned the definition.
**Fix:** New `src/risk.py` owns session equity tracking and `daily_loss_limit_hit()`; `bot.py` and `graph.py` both call it — `src/risk.py`, `src/bot.py._daily_loss_limit_hit()`, `src/graph.py.node_risk_check()`.
**Pattern tag:** `duplicated-rule-drift` (new tag)

---

## [2026-08-16] Log paths resolved against the working directory
**Symptom:** `src/logs/brave_bot.log` existed alongside `logs/brave_bot.log` — a second, smaller log nobody was reading.
**Root cause:** `bot.py` used `os.makedirs("logs")` and `trade_logger` used `Path("logs")`, both relative. Launching from `src/` instead of the project root silently created a parallel log tree. For `brave_bot.log` that is cosmetic; for `trade_log.csv` it is not — the reconciler would have backfilled exits into one copy while the graph appended signals to the other.
**Fix:** `config.LOG_DIR` is anchored to `PROJECT_ROOT` and is now the single source for every log path — `config.py`, `src/bot.py`, `src/trade_logger.py`. `trade_executor`'s separate per-day CSV writer moved into `trade_logger.log_execution()` so all three CSVs share one anchoring.
**Pattern tag:** `cwd-relative-path` (new tag)

---

## [2026-08-16] Bot auto-started on launch, ignoring a Stop set in the app
**Symptom:** Stopping the bot from the app and restarting the process resumed trading with no Start command. The 2026-08-10 `no-process-check` entry records `run()` being fixed to start paused; the v3.0 rewrite (`f8d46ba`, 2026-07-23) reintroduced an unconditional `self.is_running = True`, so that fix has not been in effect for any v3.0 session.
**Root cause:** A fix landed on the pre-v3 bot and the rewrite reimplemented `run()` from scratch without it. Nothing tested for it, so the regression was invisible — the earlier entry made it look permanently solved.
**Fix:** `run()` resumes the stored `bot_status.is_running`. The snapshot is taken in `_init_firebase()` before this process writes anything to `bot_status`, since reading it later would only see our own initialization write. LOCAL MODE still runs (no app exists to decide), and a `DAILY_LOSS_LIMIT` pause is not resumed because it is scoped to the day it was set — `src/bot.py._resume_run_state()`, `_init_firebase()`.
**Pattern tag:** `fixed-then-regressed` (new tag — a documented fix silently undone by a later rewrite; the bug log itself asserted it was closed)

---

## [2026-08-14] Strategy swapped from Flow to Frost
**Reason:** User wants Fury-style mechanical profile (mean reversion, Asian session, tight RR) — Flow is trend-continuation and structurally can't match that. Frost was already built and validated for exactly this profile (64.5% WR, 1.64 PF, 527 trades backtest) but was deleted during the QuantifyX cleanup (commit aaaf262) and recovered from git history (ad4e703) for this swap.
**Effect:** Flow's Aug 11-14 observation dataset archived as `flow_attempts_archive_2026-08-11_to_14.csv`. Frost begins fresh. Trading window changes from Pre-London/London/Bridge/NY to Asian-only (00:00-06:00 UTC). `flow.py` remains on disk, unused, not deleted, not referenced in the app.
**Instrumentation added:** `_record_attempt` parity method added to `frost.py` (didn't exist pre-deletion). `datetime.utcnow()` deprecation fixed. Logging switched to module logger.
**Pattern tag:** `strategy-swap` (not a bug — recorded here because the trading profile, session window and dataset boundary all change on this date, and future log analysis needs the seam marked)

---

## Patterns Observed

| Pattern tag | Count |
| --- | --- |
| `stale-data-trusted-as-current` | 2 |
| `no-process-check` | 1 |
| `silent-fail-open` | 3 |
| `never-actually-worked` | 1 |
| `secret-in-source` | 1 |
| `secret-in-committed-artifact` | 1 |
| `operator-toggle-not-checked` | 1 |
| `float-precision-boundary-reject` | 1 |
| `duplicate-order-risk` | 1 |
| `conflated-failure-modes` | 1 |
| `late-cheap-gate` | 1 |
| `unwired-hook` | 1 |
| `no-reentry-memory` | 1 |
| `identity-by-recency` | 1 |
| `default-bound-at-definition` | 1 |
| `duplicated-rule-drift` | 1 |
| `cwd-relative-path` | 1 |
| `fixed-then-regressed` | 1 |
| `strategy-swap` | 1 |

A tag reaching 2+ means the same class of mistake is recurring — fix the class, not just the instance.

`silent-fail-open` is now at **3** and is the most persistent class in this log. All three
instances share one shape: a safety gate whose *unknown* case was written to permit rather
than to refuse. Empty calendar → allowed. Unparseable dates → allowed. Symbol missing from
the currency map → allowed. The class fix is a rule, not another patch: **in any gate that
can stop a trade, the absence of information is a refusal, and every fall-through logs at
WARNING naming what was missing.** A gate that cannot say why it allowed something is not a
gate. Two of the three were invisible for months precisely because the permit path was silent.

`fixed-then-regressed` deserves attention out of proportion to its count of 1: it means an
entry in this log asserted a bug was closed while the bug was live. The log is only as good
as that assertion. Any fix recorded here that is not covered by a test is one rewrite away
from silently reopening — and Brave currently has no test suite at all.

`silent-fail-open` was previously at 2 — the class, not the instance, is the problem: Brave has
safety/review mechanisms (news filter, HITL queue) that stop or hold a trade without any
channel that reaches the human away from the app. The class fix is one out-of-band alert
path every such mechanism calls, not a second one-off patch.

The two `secret-*` tags are one class seen from both ends — a secret written into a file that
was never meant to hold one. Counted separately because the fixes differ (remove from source
vs. rotate what already shipped), but treat a second occurrence of *either* as the class
recurring: no secret goes in any tracked file, and anything that logs a URL gets masked.
