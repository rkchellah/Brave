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

## Patterns Observed

| Pattern tag | Count |
| --- | --- |
| `stale-data-trusted-as-current` | 1 |
| `no-process-check` | 1 |
| `silent-fail-open` | 1 |
| `never-actually-worked` | 1 |

A tag reaching 2+ means the same class of mistake is recurring — fix the class, not just the instance.
