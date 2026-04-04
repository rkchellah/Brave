# QuantifyX — Project Rules
> These rules apply to every file, every commit, every conversation.

---

## Code

- Python only. Type hints enforced where possible.
- Clean, well-structured code. No unnecessary complexity.
- Comments explain *why*, never *what*.
- No credentials, keys, or secrets ever hardcoded — always use `.env` via `python-dotenv`.
- Every strategy must follow the Strategy Contract in ARCHITECTURE.md.
- No strategy runs without defined SL, TP, and RR on every signal.
- Every trade action must be logged.

---

## Documentation

- Update `README.md` every time a meaningful code change is made.
- Update `CHECKLIST.md` when tasks are completed or new issues are found.
- Update `ARCHITECTURE.md` when the stack or data flow changes.
- Log every bug and fix in the Known Issues table in CHECKLIST.md.

---

## Stack

- **Language:** Python 3.9+
- **Trading Execution:** Kraken REST API via `krakenex`
- **Market Data:** Kraken public OHLCV API + PRISM API
- **Database:** Firebase Realtime Database via `firebase-admin`
- **Math:** NumPy
- **Secrets:** `.env` file via `python-dotenv` — never committed
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

- Never commit `.env`, `serviceAccountKey.json`, `config.py`, or any file with credentials
- All three are in `.gitignore` — verify before every push
- Kraken API keys must never have Withdraw permissions enabled
- Do not share API keys in chat, Discord, or any public channel
- Rotate keys immediately if accidentally exposed (as done on Apr 3 2026)

---

## Philosophy

- Shipping beats perfection — especially with a hackathon deadline.
- Test dry_run=True before ever switching to dry_run=False.
- Security is not optional — credentials stay out of the repo, always.
- If something is not working, print the actual values before guessing at thresholds.
- One data point beats ten guesses.