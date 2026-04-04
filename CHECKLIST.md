# QuantifyX — Project Checklist

## Phase 0 — Environment Setup ✅ COMPLETE

- [x] Project folder created: `QuantifyX` (renamed from `Brave_Agent`)
- [x] Python venv created: `.venv`
- [x] `krakenex 2.2.2` installed into correct venv
- [x] `python-dotenv 1.2.2` installed into correct venv
- [x] `numpy` installed
- [x] `requests` installed
- [x] `.env` file created with all three API keys
- [x] `.gitignore` configured — `.env`, `serviceAccountKey.json`, `logs/`, `__pycache__/` excluded
- [x] Junk files deleted — old logs, pycache, irrelevant Brave files removed

---

## Phase 1 — Kraken Connection ✅ COMPLETE

- [x] Kraken account created
- [x] **QuantifyX-Agent** API key generated
      Permissions: Query Funds, Query Open/Closed Orders, Create & Modify Orders, Cancel & Close Orders
- [x] **QuantifyX-Leaderboard** API key generated (read-only, for lablab.ai submission)
- [x] `test_connection.py` created and validated
- [x] Public API test passed — BTC price fetching at $66,920
- [x] Private API test passed — Balance endpoint responding (empty, no funds yet)
- [x] PRISM API key generated and added to `.env`
- [x] PRISM `/resolve/BTC` endpoint tested and confirmed working
- [ ] Deposit funds into Kraken account for live trading
- [ ] Submit QuantifyX-Leaderboard read-only key to lablab.ai

---

## Phase 2 — Frost Strategy Port ✅ COMPLETE

- [x] `src/frost_kraken.py` created — Frost ported from MT5 to Kraken
- [x] MT5 `copy_rates_from_pos()` replaced with Kraken `/0/public/OHLC` endpoint
- [x] MT5 `symbol_info()` removed — point sizes hardcoded per crypto pair
- [x] MT5 `symbol_info_tick()` spread check removed — not needed on Kraken
- [x] Core indicators unchanged — `_calculate_ma()`, `_calculate_atr()`, `_is_trending()`
- [x] Thresholds recalibrated for BTC dollar units (not forex pips)
      MAX_ATR_PIPS: 300 → 600 (BTC ATR observed at $478–543 during active hours)
      MAX_DEVIATION_PIPS: 500 → 1000 (BTC deviation observed at $531–777)
      Trend slope threshold: 5 → 100 (BTC slope observed at ~70 units/candle)
- [x] Candle fetch test passed — 50 M15 candles fetched, latest close confirmed
- [x] Full analysis test passed — signal generated in backtest mode
      SELL signal: Entry $66,918 | SL $66,972 | TP $66,875 | RR 0.79 | Probability HIGH
- [x] Session filter confirmed working — correctly blocks signals outside Asian session

---

## Phase 3 — Execution Layer ✅ COMPLETE

- [x] `src/executor.py` created
- [x] `dry_run=True` mode implemented and tested
- [x] Full pipeline test passed:
      Frost signal → Executor → Dry run order confirmed
      BUY 0.001 XBTUSD @ market | SL $66,831 | TP $66,858 | RR 0.75
- [ ] Live execution test — switch `dry_run=False` after depositing funds
- [ ] Verify order appears in Kraken open orders
- [ ] Verify order fills and appears in trade history

---

## Phase 4 — Main Agent Loop ⬜ NOT STARTED

- [ ] `src/agent.py` created — runs Frost every 15 minutes
- [ ] Loop runs during Asian session only (00:00–06:00 UTC)
- [ ] Graceful shutdown on keyboard interrupt
- [ ] Error handling — API failures do not crash the loop
- [ ] Agent tested overnight during Asian session

---

## Phase 5 — Firebase Logging ⬜ NOT STARTED

- [ ] `src/firebase_logger.py` created
- [ ] Signal pushed to Firebase on every signal generated
- [ ] Trade pushed to Firebase on every execution
- [ ] Status updated every loop cycle
- [ ] Firebase Console shows live data

---

## Phase 6 — Hackathon Submission ⬜ NOT STARTED

- [ ] Surge project registration completed at early.surge.xyz
- [ ] Multi-sig wallet set up for prize receiving
- [ ] QuantifyX-Leaderboard read-only key submitted to lablab.ai
- [ ] GitHub repo made public
- [ ] Demo video recorded
      Must show: agent analyzing market → signal generated → order placed on Kraken
- [ ] Devpost submission completed:
      - [ ] Project title and description
      - [ ] Cover image
      - [ ] Video presentation
      - [ ] Slide presentation
      - [ ] GitHub repo link
      - [ ] Tech tags
- [ ] Social posts published and tagged:
      @krakenfx @lablabai @Surgexyz_

---

## Known Issues Log

| Date | Issue | Status | Fix |
|---|---|---|---|
| Apr 3 2026 | Kraken CLI has no Windows binary in v0.3.0 | Resolved | Switched to krakenex Python library |
| Apr 3 2026 | Git Bash cannot execute Linux ELF binary | Resolved | krakenex used instead |
| Apr 3 2026 | python-dotenv installed into wrong venv (old Brave venv) | Resolved | Used explicit python.exe path to install |
| Apr 3 2026 | krakenex installed into wrong venv initially | Resolved | Used explicit python.exe path to install |
| Apr 3 2026 | MAX_ATR_PIPS 300 too tight for BTC | Resolved | Raised to 600 based on observed ATR $478–543 |
| Apr 3 2026 | MAX_DEVIATION_PIPS 500 too tight for BTC | Resolved | Raised to 1000 based on observed deviation $531–777 |
| Apr 3 2026 | Trend slope threshold 5 too tight for BTC | Resolved | Raised to 100 based on observed slope ~70 |
| Apr 3 2026 | Private API balance returns empty dict | Not a bug | Expected — account has no funds yet |

---

## Backtest Results (Frost — Original Forex Validation)

These results are from the original Brave Frost backtest on forex pairs.
The Kraken port inherits the same core logic.

| Metric | Result |
|---|---|
| Pairs | GBPUSD, USDCAD, EURCHF |
| Total trades | 527 |
| Win rate | 64.5% |
| Profit factor | 1.64 |
| Total return | +79.3% |
| Max drawdown | 5.8% |
| Period | 17 months |

Crypto-specific backtest on XBTUSD/ETHUSD — pending.