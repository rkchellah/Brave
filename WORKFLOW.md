# Brave Bot — Phase 2 Testing Plan

This document explains the internal logic of the Brave trading bot for the Phase 2 testing period. The goal of this phase is to observe the bot's performance and automated safeguards without manual intervention.

## Core Operational Logic

The bot operates in a continuous loop, checking for opportunities once every minute (60 seconds).

### 1. Daily Session Memory
Upon startup, or when a new calendar day begins, the bot "takes a snapshot" of your account equity.
*   **Key Variable**: `_session_start_equity`
*   **Behavior**: This value is stored in the bot's active memory. It serves as the baseline for the entire day's performance calculation.
*   **Resetting**: This memory resets automatically at midnight (UTC) or if the bot process is completely stopped and restarted.

### 2. Daily Stop Loss Limit (Safety Guard)
To prevent catastrophic losses during high volatility or strategy failure, the bot monitors its performance against the daily snapshot.
*   **Limit**: 5% of the starting daily equity.
*   **Calculation**: `Current Equity - Session Start Equity`.
*   **Action**: If your account equity falls 5% or more below the daily start level, the bot will:
    1.  Log a `WARNING` status.
    2.  Set its internal state to "Paused".
    3.  Report `DAILY_LOSS_LIMIT` to Firebase.
*   **Resuming**: If the limit is hit, just sending a "Start" command will often result in the bot pausing itself again immediately because its "memory" still shows the daily loss. To trade again after a limit hit, you must either wait for the next day or restart the bot script to reset its session memory.

### 3. Position and Order Management
The bot is designed to follow a "One Trade per Pair" rule to manage risk.
*   **Logic**: Before placing any new trade, the bot scans both **Active Positions** and **Pending Orders** (e.g., Buy Stops).
*   **Behavior**: If it sees either an open position OR a pending order for a specific currency pair, it will skip that pair entirely.
*   **User Action**: As per the testing plan, all orders must be closed by Take Profit (TP) or Stop Loss (SL). The bot will not "stack" multiple orders on the same pair while one is still waiting to trigger or is currently active.

### 4. Command Handling Latency
When you send a "Start" or "Stop" command via Firebase:
*   The bot receives the instruction immediately in its listener thread.
*   However, the main trading engine may be in the middle of a 60-second "sleep" cycle.
*   **Result**: It can take up to 60 seconds for the bot's terminal output to reflect the command you sent.

## Testing Rules (Phase 2)
1.  **Hands-Off Approach**: All trades should be managed by the bot's TP/SL.
2.  **No Manual Closing**: Do not manually close positions in MetaTrader 5, as this will skew the bot's internal performance tracking for the day.
3.  **Observation**: Use the Firebase dashboard or the log files (`logs/brave_bot.log`) to monitor the bot's decision-making process.
