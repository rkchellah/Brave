# Brave — Workflow

---

## How the Agent Works

Brave runs a continuous loop. Every 60 seconds it syncs account state to the app; every
cycle it re-checks whether there is a trade to make.

```
Every 60 seconds:
  ├── Is the bot running?                          → No: wait for start command
  ├── Push balance/equity/positions to Firebase
  ├── Health check due (every 10 min)?             → Yes: run it
  ├── Execute any signals confirmed in the app     (MANUAL mode)
  ├── Is Flow enabled in app settings?             → No: skip
  ├── Daily loss limit hit (-5% on the day)?       → Yes: pause for the day
  ├── Re-score tradable pairs (hourly)
  ├── Any markets open?                            → No: skip
  └── For each open pair:
        ├── High-impact news within ±30 min?       → Yes: skip pair
        └── run_brave_graph(symbol)
              ├── DETECT      Flow finds a setup?  → No: end
              ├── ANALYSE     DeepSeek on Finnhub headlines
              │                 OPPOSE             → end
              ├── RISK_CHECK  positions, daily loss, signal sanity
              │                 fail               → end
              └── EXECUTE (CONFIRM + AUTO)  or  HITL (UNCERTAIN or MANUAL)
```

---

## Running the Agent

### Daily Routine

1. Start the MT5 terminal and confirm Algo Trading is enabled.

2. Start the bot:
```powershell
& ".\.venv\Scripts\python.exe" src/bot.py
```

3. Press START in the app (or write `commands/action: start` in Firebase). The bot boots
   paused — it will not trade until told to.

4. Monitor the app or the Firebase Console during the session.

5. Check logs after the session:
```powershell
Get-Content logs\brave_bot.log -Tail 100
Get-Content logs\trades\trades_2026-08-09.csv
```

---

## Execution Modes

Set from the app (Dashboard → AUTO / MANUAL) or `EXECUTION_MODE` in `config.py`. The bot
re-reads it from Firebase every cycle, so switching does not need a restart.

**AUTO**
CONFIRM verdicts execute immediately. UNCERTAIN still routes to the phone. OPPOSE aborts.

**MANUAL**
Every signal routes to `pending_signals` and waits for Confirm/Reject in the app. Nothing
reaches the broker without a human tap. Use this until the full pipeline is verified.

Signals expire after `SIGNAL_EXPIRY_SECONDS` (default 180) — a Flow setup at M15 is stale
once price has moved off the AOI. A confirmation arriving after expiry is refused.

---

## Testing Outside Session Hours

Flow's session filter blocks signals outside its four UTC windows. To exercise strategy
logic during off hours, use backtest mode:

```python
signal = Flow(config).analyze('EURUSD', provided_rates={
    mt5.TIMEFRAME_H1:  h1_candles,
    mt5.TIMEFRAME_M15: m15_candles,
})
```

Passing `provided_rates` bypasses the session filter. It is safe — it places no orders.

To exercise the HITL path without waiting for a real setup, set MANUAL mode and clear the
queue first:

```powershell
& ".\.venv\Scripts\python.exe" scripts/clear_pending_signals.py --dry-run
& ".\.venv\Scripts\python.exe" scripts/clear_pending_signals.py
```

---

## Changing the Broker Account

Settings → Broker Account in the app writes `mt5_config` to Firebase. The bot reads it at
startup only, so:

1. Save the new login / password / server in the app
2. Stop the bot
3. Start it again
4. Confirm the log line: `MT5 credentials source: Firebase | Login: … | Server: …`

If that line says `config.py fallback`, the Firebase node is missing a field — the bot
refuses to log in with a half-filled config rather than locking itself out.

---

## Known Behavior

**"Outside session"**
Expected. Flow only trades Pre-London, London, Bridge and NY windows. Not a bug.

**"Skipped — high-impact news window"**
A high-impact event for that pair's currencies is within ±30 minutes. Not a bug.

**"Max trades (N) already open on SYMBOL"**
The per-symbol cap counts open positions *and* pending orders. Not a bug.

**"UNCERTAIN — News analysis failed"**
DeepSeek or Finnhub was unreachable. The pipeline degrades to a human decision rather than
guessing. Check the key and connectivity if it persists.

**"All markets closed — skipping"**
No symbol reported a fresh tick. Normal at weekends.

**Signal pushed but no order placed**
MANUAL mode is active, or sentiment was UNCERTAIN. Confirm it in the app.

**LangChain Pydantic v1 warning on import**
Known, non-blocking on Python 3.14. Tracked in CHECKLIST.md.
