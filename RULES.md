# Project Rules
> These rules apply to every file, every commit, every conversation.

## Code
- Python only. Type hints enforced where possible.
- Code must be clean and well-structured.
- Comments only when necessary — explain *why*, never *what*.
- No credentials, keys, or secrets ever hardcoded — use `config.py` or environment variables.

## Documentation
- Update `README.md` every time a change is made to the code.
- Update `CHECKLIST.md` when tasks are completed.
- Confirm with the team before checking off any task as complete.

## Stack
- **Language:** Python 3.9+
- **Trading:** MetaTrader 5 (MT5) via `MetaTrader5` Python library
- **Database:** Firebase Realtime Database via `firebase-admin`
- **Data/Math:** NumPy
- **Remote Control:** Firebase-based command queue (mobile-ready)
- **Auth/Secrets:** `config.py` (local, never committed) + Firebase service account key (`serviceAccountKey.json`)
- **Deployment:** Local machine / VPS running MT5 terminal

## Philosophy
- Shipping beats perfection.
- Security is not an afterthought — credentials stay out of the repo, always.
- Every trade action must be logged. Reversibility and auditability are non-negotiable.
- Risk is controlled at the code level — no strategy runs without defined SL, TP, and position sizing.
