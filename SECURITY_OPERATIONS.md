# Brave security operations

## Deploy the Firebase security boundary

1. In Firebase Console, enable **Email/Password** Authentication and create the authorized operator account.
2. Set the bot's `USER_ID` environment variable to that account's Firebase Auth UID. Do not use an arbitrary string.
3. Install Firebase CLI, authenticate to the intended project, then deploy only the rules: `firebase deploy --only database`.
4. Before enabling live trades, verify an unauthenticated client cannot read `/users`, one user cannot access another user's path, and the signed-in operator can only start/stop, change mode, toggle Frost, or respond to a pending signal.

The service account used by the bot has administrative access and bypasses these rules. Keep it only on the trusted bot host; do not package it with the mobile app.

## Credential incident response

If a Firebase service-account key, MT5 password, DeepSeek key, or Finnhub key may have been exposed:

1. Pause the bot and revoke/rotate the affected credential at its provider.
2. Remove plaintext `mt5_config` data from Firebase after the bot has moved to local credentials.
3. Review Firebase Authentication users, database activity, bot logs, and broker order history for unauthorized actions.
4. Record the incident, affected time range, rotation time, and recovery verification.

## Recovery checks

- Back up Firebase data and trade logs on a defined schedule, encrypted and access-controlled.
- Test restoration to a non-production Firebase project at least quarterly.
- Test a bot restart using only its local service secret configuration; it must fail closed when MT5 credentials are unavailable.
