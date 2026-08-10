# Brave — Project Rules
> These rules apply to every file, every commit, every conversation.

---

## Code

- Python only. Type hints enforced where possible.
- Clean, well-structured code. No unnecessary complexity.
- Comments explain *why*, never *what*.
- No credentials, keys, or secrets ever hardcoded — environment variables first, `config.py` as local fallback, never committed.
- Every strategy must follow the Strategy Contract in ARCHITECTURE.md.
- No strategy runs without defined SL, TP, and RR on every signal.
- Every trade action must be logged.

---

## Documentation

- Update `README.md` every time a meaningful code change is made.
- Update `CHECKLIST.md` when tasks are completed or new issues are found.
- Update `ARCHITECTURE.md` when the stack or data flow changes.
- Log every bug and fix in the Known Issues table in CHECKLIST.md.
- Log every **live-testing** bug in `bug_log.md` — see Testing & Debugging below.

---

## Testing & Debugging

- `bug_log.md` at the project root is the running log of bugs found during live testing.
  It records the root-cause *pattern*, not just the fix, so repeated classes of mistake become visible.
- **A live-testing bug is not "fixed" until it has an entry in `bug_log.md`.** Writing the entry is part
  of the definition of done, not an optional afterthought.
- Entry format: `## [Date] Short title`, then **Symptom**, **Root cause** (one phrase), **Fix**
  (what changed, file/function), **Pattern tag** (short reusable category).
- Update the **Patterns Observed** tally at the bottom of `bug_log.md` with every entry.
  A tag hitting 2+ is a signal to fix the class of mistake, not just the instance.
- Before debugging, check `bug_log.md` — the same pattern may already be documented.

---

## Stack

- **Language:** Python 3.9+
- **Orchestration:** LangGraph
- **Trading Execution:** MetaTrader 5 Python API
- **Market Data:** MT5 H1 + M15 rates
- **News:** Finnhub headlines + DeepSeek analysis, ForexFactory event calendar
- **Database:** Firebase Realtime Database via `firebase-admin`
- **Math:** NumPy
- **Secrets:** environment variables, `config.py` fallback — never committed
- **Dev Environment:** Windows PowerShell

---

## PowerShell Rules

- Always run one command per line — never paste multiline blocks at once
- Always use the explicit python path to avoid venv resolution issues:
  `& ".\.venv\Scripts\python.exe" script.py`
- Always activate the correct venv before installing packages:
  `& ".\.venv\Scripts\Activate.ps1"`
- When installing packages, use the explicit pip path to guarantee the right venv:
  `& ".\.venv\Scripts\python.exe" -m pip install package_name`

---

## Security

- Never commit `serviceAccountKey.json`, `config.py`, or any file with credentials
- Both are in `.gitignore` — verify before every push
- MT5 accounts used for testing must be demo accounts until the pipeline is verified live
- Firebase Realtime Database rules must be scoped to the owning UID — `mt5_config` holds
  the broker password in plaintext
- Do not share API keys in chat, Discord, or any public channel
- Rotate keys immediately if accidentally exposed

---

## Philosophy

- Shipping beats perfection — especially with a hackathon deadline.
- Test dry_run=True before ever switching to dry_run=False.
- Security is not optional — credentials stay out of the repo, always.
- If something is not working, print the actual values before guessing at thresholds.
- One data point beats ten guesses.