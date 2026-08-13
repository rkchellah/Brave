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

## Patterns Observed

| Pattern tag | Count |
| --- | --- |
| `stale-data-trusted-as-current` | 2 |
| `no-process-check` | 1 |
| `silent-fail-open` | 2 |
| `never-actually-worked` | 1 |
| `secret-in-source` | 1 |
| `secret-in-committed-artifact` | 1 |
| `operator-toggle-not-checked` | 1 |
| `float-precision-boundary-reject` | 1 |
| `duplicate-order-risk` | 1 |
| `conflated-failure-modes` | 1 |
| `late-cheap-gate` | 1 |
| `unwired-hook` | 1 |

A tag reaching 2+ means the same class of mistake is recurring — fix the class, not just the instance.

`silent-fail-open` is now at 2 — the class, not the instance, is the problem: Brave has
safety/review mechanisms (news filter, HITL queue) that stop or hold a trade without any
channel that reaches the human away from the app. The class fix is one out-of-band alert
path every such mechanism calls, not a second one-off patch.

The two `secret-*` tags are one class seen from both ends — a secret written into a file that
was never meant to hold one. Counted separately because the fixes differ (remove from source
vs. rotate what already shipped), but treat a second occurrence of *either* as the class
recurring: no secret goes in any tracked file, and anything that logs a URL gets masked.
