# Brave Trading Bot – Team Rules

These rules are **required** for anyone working on the Brave bot (Python) and app (React Native / Expo).

---

## 1. Core Principles

- Always **understand the error yourself first** before asking any agent or other person.
- Prefer **simple, high‑impact practices** over heavy tooling.
- Only add automation (tests, CI/CD) when it clearly saves time.

---

## 2. Bot Debugging Rules (Python)

### 2.1 Read Logs First

Whenever the bot misbehaves or crashes:

```powershell
# Last 50 lines of the bot log
Get-Content logs\brave_bot.log -Tail 50
```

- Find the error message and stack trace in the log.
- Note the function, line number, and recent events before asking for help.

**Rule:** No one posts an error to chat/AI until they have checked `logs/brave_bot.log` and can quote the relevant lines.

---

### 2.2 Run `bot.py` in a Visible Terminal

- Do **not** run the bot hidden or detached during development.
- Always run it in a terminal window where logs stream live.

**Rule:** If you cannot see logs scrolling, you are not “really” running the bot.

---

### 2.3 Dry‑Run Mode (Safe Testing)

In `config.py`:

```python
DRY_RUN = True  # skips mt5.order_send(), logs what would have been placed
```

In the execution function (e.g., `execute_signal()`):

```python
if DRY_RUN:
    logging.info(
        f"[DRY RUN] Would place: {direction} {symbol} @ {price} | SL: {sl} | TP: {tp}"
    )
    return None
```

**Usage:**

- Any time order logic, risk logic, or strategy filtering changes:
  - Run the bot with `DRY_RUN = True`.
  - Review logs to confirm correct behavior.
  - Only then disable dry‑run for live trading.

---

### 2.4 Per‑Strategy Sanity Test

At the bottom of each strategy file (example: `flow.py`):

```python
if __name__ == "__main__":
    import MetaTrader5 as mt5
    from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER

    mt5.initialize()
    mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)

    f = Flow({"lot_size": 0.01})
    result = f.analyze("EURUSD")
    print("Signal:", result)

    mt5.shutdown()
```

Run directly:

```powershell
& ".\.venv\Scripts\python.exe" src\flow.py
```

**Rule:** After editing a strategy file, run it directly and fix any crash or bad output **before** running `bot.py`.

---

## 3. App & Firebase Rules (React Native / Expo)

### 3.1 Verify Firebase Before Any APK Build

**Bot‑side check:**

```powershell
.venv\Scripts\python -c "
from firebase_admin import db
import firebase_admin
from firebase_admin import credentials

cred = credentials.Certificate('serviceAccountKey.json')
firebase_admin.initialize_app(
    cred,
    {'databaseURL': 'https://thunder-23e63-default-rtdb.firebaseio.com'}
)
ref = db.reference('users/RcB4T6930SVvE4Lt9mCSs6nbG1G2/bot_status')
print(ref.get())
"
```

- If this fails, fix Python/Firebase before touching the app.

**App‑side check:**

- Open the same Firebase path in a browser or tool on your phone.
- Confirm keys and paths match the app’s expectations.

**Rule:** Do **not** start an APK build until both checks succeed.

---

## 4. Things We Ignore For Now

Until the project and team grow:

- No full automated test suites (pytest, Jest, etc.).
- No CI/CD pipelines.
- No heavy automated testing systems.

They are allowed later when the benefit clearly outweighs the setup cost.

---

## 5. Working With AI / Agents

### 5.1 Read the File Before Asking for Edits

Before asking an agent (or another person) to modify code:

1. Open the relevant file.
2. Read the section you want changed.
3. Look for:
   - Existing logic that might conflict.
   - Duplicated or legacy code.
   - Comments explaining design decisions.

**Rule:** If an AI change causes a bug, we assume we didn’t read the file carefully enough before giving instructions.

---

### 5.2 Standard Debugging Flow

When something breaks:

1. Reproduce the issue.
2. Read the relevant log (`logs/brave_bot.log` or app logs).
3. Identify the likely file/function.
4. Read that file section.
5. Only then:
   - Fix it yourself, or
   - Ask for help, including:
     - Error message
     - Log snippet
     - Relevant code block

---

## 6. Pre‑Push / Pre‑Live Checklist

Before pushing to main or running live:

- [ ] Checked `logs/brave_bot.log` for errors.
- [ ] Ran `bot.py` with visible logs.
- [ ] Tested with `DRY_RUN = True` after any logic change.
- [ ] Ran each modified strategy file directly (`python src\strategy.py`).
- [ ] Verified Firebase reads/writes before building APKs.
- [ ] Read all files affected before asking an agent to edit them.
