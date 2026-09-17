# Brave Security Checks Canvas

**Scope:** Python trading bot, Expo mobile app, Firebase Realtime Database, local configuration and dependencies.  
**Last reviewed:** 2026-09-17  
**Review method:** Static code/configuration review and local dependency scan. This is not a penetration test and cannot verify the live Firebase rules, IAM, or broker configuration.

## Current release posture

**Do not treat the mobile app or remote trading controls as production-safe until items S-01 through S-03 are closed.** The app currently makes privileged Firebase reads/writes without authenticating a user. Firebase database rules are therefore the decisive security control, but no deployable rules file or rules test is present in this repository.

| Priority | Task | Status | Evidence / acceptance check |
|---|---|---|---|
| Critical | S-01: Authenticate every mobile user with Firebase Auth. Remove reliance on a hard-coded user identifier as an identity control. | Open | `brave-app/App.js` initializes Database only; it contains no Firebase Auth sign-in/session handling. Close only when the app uses the authenticated UID for paths and unauthenticated access is denied. |
| Critical | S-02: Enforce least-privilege Firebase Realtime Database rules and commit the rules source plus emulator tests. | Open / external deployment needed | No `firebase.json` or database rules file is present. Rules must scope data to `auth.uid`, deny cross-user access, and restrict writes to command/config/signal shapes. Validate with Firebase Emulator tests before deployment. |
| Critical | S-03: Remove plaintext MT5 passwords from Firebase. | Open | The mobile client writes `users/<uid>/mt5_config/password`; bot reads it. A database compromise exposes broker credentials. Replace with local prompt/OS secret storage or a purpose-built broker credential flow; do not encrypt with a key stored in this repository. |
| High | S-04: Confirm whether the local Firebase service-account key is active; rotate it if exposure is possible, then store it outside the workspace (secret manager or protected service account location). | Open / operator action | `serviceAccountKey.json` exists locally but is ignored and not tracked in current Git history. It remains a high-value administrator credential. |
| High | S-05: Add durable authorization/audit events for all trading-impacting remote actions (start/stop, mode changes, signal approval, broker-config edits). | Open | Current app writes directly to Firebase; code records timestamps but no authenticated actor or tamper-resistant audit trail. Close with server-side event records linked to authenticated UID and monitored alerts. |
| High | S-06: Make pending-signal claiming atomic. | Open | `src/bot.py` reads a signal then updates it to `EXECUTING`; the update is not a compare-and-set transaction. Close with a Firebase transaction that claims only a currently `CONFIRMED`, unexpired signal. |
| Medium | S-07: Add automated secret scanning and a protected CI gate for tracked files and pull requests. | Open | `.gitignore` excludes `.env`, `config.py`, and service keys; no repository CI/security gate was found. Close with secret scanning plus a documented rotation runbook. |
| Medium | S-08: Pin/scan Python dependencies in a repeatable CI job. | Open | `requirements.txt` uses ranges for `firebase-admin`; no Python vulnerability report or lockfile is present. Close with a lock/constraints file and a maintained vulnerability scan. |
| Medium | S-09: Define backup, restore, and incident-response procedures for Firebase data, trade logs, compromised API keys, and broker credentials. | Open | No recovery/incident runbook was found in the reviewed files. Exercise restore and credential-rotation procedures. |
| Low | S-10: Add a threat model and security review checklist to change workflow. | Open | Risks are described informally in docs, but there is no versioned threat model, trust-boundary diagram, or release security sign-off. |

## Checks by security concern

| Concern | Status | What was verified |
|---|---|---|
| Authentication | Fail | Mobile app does not use Firebase Auth; a hard-coded `USER_ID` selects the database path. |
| Authorization / least privilege | Fail (not verifiable) | Firebase rules are absent from the repository; remote controls and broker credentials are direct client writes. |
| Data protection | Fail | MT5 password is deliberately persisted in Firebase plaintext. HTTPS is required for the configured Firebase URL. |
| Input and execution validation | Partial | Signal shape, prices, direction, stops and broker constraints are validated in `src/trade_executor.py`; remote Firebase payloads still need rule/schema enforcement. |
| Secret management | Partial | `.env` and service account key are ignored; the local service key is untracked in reviewed Git history. No automated secret scan; `config.py` contains project/user identifiers. |
| Dependency / supply chain | Pass (snapshot) | `npm audit --package-lock-only --omit=dev` reported 0 vulnerabilities on 2026-09-17. Python dependencies were not audited against a vulnerability feed. |
| Secure configuration | Partial | `.env.example` avoids real keys and Firebase URL validation requires HTTPS. No versioned Firebase rules, environment separation, or hardened deployment configuration was found. |
| Logging / monitoring | Partial | Errors and trade actions are logged; sensitive-token redaction exists in `src/news_fetcher.py`. No security-event monitoring or immutable audit trail was found. |
| Availability / recovery | Partial | Request timeouts, bounded retries, data caching and risk limits exist. Backups, restore drills and incident handling are undocumented. |
| AI / untrusted-content handling | Partial | News content is delimited in the LLM prompt and suspicious instruction patterns are filtered. This is defense-in-depth, not a substitute for authorization around order execution. |

## Completed checks

- [x] Confirmed `serviceAccountKey.json` is ignored and not tracked by the current repository history.
- [x] Confirmed existing uncommitted changes in `src/graph.py` and `src/news_fetcher.py` were left untouched.
- [x] Python compilation check passed for `src`, `backtest`, and `test_news_injection_example.py`.
- [x] JavaScript production dependency audit completed with zero reported vulnerabilities.
- [x] Reviewed the direct Firebase command, configuration, signal-confirmation, and broker-credential paths.

## Next work order

1. Choose an authentication approach for the mobile app (for example, Firebase email/password with MFA where available, or an enterprise identity provider).
2. Implement Firebase Auth in the app, remove hard-coded identity selection, and add versioned Realtime Database rules plus Emulator tests.
3. Migrate broker credentials out of Firebase and rotate affected credentials if they have been stored there.
4. Add the CI secret/dependency checks, then update this canvas with links to test results and rule deployment version.

## Guardrails for future changes

- Never place passwords, API keys, private keys, or service-account files in source, examples, logs, test fixtures, or build artifacts.
- Treat every Firebase client write as hostile until Firebase rules and server-side validation approve it.
- Any change that can place, approve, or alter a trade requires authentication, authorization, an auditable actor, validation, and a fail-closed error path.
- Re-run the completed checks after dependency, Firebase, credential, or execution-path changes and update the matching task status above.
