# QuantifyX — Workflow

---

## How the Agent Works

QuantifyX runs a continuous loop during Asian session hours (00:00–06:00 UTC).
Every 15 minutes it checks market conditions and either generates a signal or skips.

```
Every 15 minutes:
  ├── Is it Asian session (00:00–06:00 UTC)?      → No: sleep, wait
  ├── Is it before cutoff (05:30 UTC)?             → No: no new entries
  ├── Fetch 50 M15 candles from Kraken OHLCV API
  ├── Calculate ATR — is market ranging?           → No: skip
  ├── Calculate 20-period MA and price deviation
  ├── Is deviation between $50 and $1000?          → No: skip
  ├── Is market trending (slope > 100)?            → Yes: skip
  ├── Generate mean reversion signal
  │     BUY if price below MA (fade back up)
  │     SELL if price above MA (fade back down)
  ├── Log signal to Firebase
  └── Execute order on Kraken (or dry run)
```

---

## Running the Agent

### Daily Routine

1. Verify connection is working:
```powershell
& ".\.venv\Scripts\python.exe" test_connection.py
```

2. Run a pipeline test before the Asian session starts:
```powershell
& ".\.venv\Scripts\python.exe" test_frost.py
```

3. Start the agent before midnight UTC:
```powershell
& ".\.venv\Scripts\python.exe" src/agent.py
```

4. Monitor Firebase Console for signals and trades during the session.

5. Check logs after the session:
```powershell
Get-Content logs\agent_YYYYMMDD.log
```

---

## Execution Modes

**dry_run=True (default during testing)**
The agent generates signals and logs orders but does not send them to Kraken.
Use this until you have verified the full pipeline end-to-end.

**dry_run=False (live trading)**
Orders are sent to Kraken. Only switch to this after:
- Depositing funds into Kraken
- Running at least one full Asian session in dry_run mode
- Confirming signals are sensible in the logs

To switch, update `src/agent.py`:
```python
result = executor.execute(signal, dry_run=False)
```

---

## Testing During the Day (Outside Asian Session)

Frost's session filter blocks signals outside 00:00–06:00 UTC.
To test strategy logic during the day, use backtest mode:

```python
candles = f._get_candles('XBTUSD', None)
signal = f.analyze('XBTUSD', provided_rates={'M15': candles})
```

Passing `provided_rates` bypasses the session filter.
This is safe — it does not place any orders.

---

## Threshold Calibration

The thresholds in `frost_kraken.py` were calibrated from live BTC data observed
during the port on April 3, 2026 (active market, not Asian session).

| Threshold | Value | Observed Data |
|---|---|---|
| MAX_ATR_PIPS | 600.0 | ATR observed at $478–543 during active hours |
| MAX_DEVIATION_PIPS | 1000.0 | Deviation observed at $531–777 |
| Trend slope | 100.0 | Slope observed at ~70 during active hours |

During Asian session (calm conditions), these values will be lower.
If the agent is rejecting too many signals during Asian session:
- Check the logs for which filter is triggering
- Print the actual values (the slope logging line does this already)
- Adjust the relevant threshold based on real observed data, not guesses

---

## Known Behavior

**"Outside Asian session"**
Expected. Frost only trades 00:00–06:00 UTC. Not a bug.

**"ATR too high"**
BTC is volatile right now. The agent will trade when it calms down during
the Asian session. Not a bug.

**"Market trending — skip"**
The linear regression slope is too steep for mean reversion to be safe.
Asian session naturally produces ranging conditions. Not a bug.

**Balance returns empty dict**
Normal if no funds deposited. Not an API error.

**Signal generated but no order placed**
dry_run=True is active. This is intentional during testing.

---

## Hackathon Submission Checklist

Before April 12, 2026:

1. Register project at early.surge.xyz (required for prizes)
2. Submit QuantifyX-Leaderboard read-only key to lablab.ai
3. Record demo video showing:
   - Agent running during Asian session
   - Signal generated in logs
   - Order appearing in Kraken
4. Push final code to GitHub (public repo)
5. Complete Devpost submission with all required fields
6. Publish social posts tagging @krakenfx @lablabai @Surgexyz_