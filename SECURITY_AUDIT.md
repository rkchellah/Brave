# Brave Security Checks Canvas

**Scope:** Python trading bot, Expo mobile app, Firebase Realtime Database, local configuration and dependencies.  
**Last reviewed:** 2026-09-17  
**Review method:** Static code/configuration review and local dependency scan. This is not a penetration test and cannot verify the live Firebase rules, IAM, or broker configuration.

## Current release posture

**Do not treat the mobile app or remote trading controls as production-safe until the Firebase deployment and credential-rotation steps below are complete.** Repository controls now require Firebase Auth and versioned rules, but their protection begins only after they are deployed and tested against the live project.

| Priority | Task | Status | Evidence / acceptance check |
|---|---|---|---|
| Critical | S-01: Authenticate every mobile user with Firebase Auth. Remove reliance on a hard-coded user identifier as an identity control. | Implemented — deployment verification pending | The app now signs in with Firebase Email/Password and derives its path from `auth.uid`. Enable the provider and verify the intended operator account in Firebase Console. |
| Critical | S-02: Enforce least-privilege Firebase Realtime Database rules and commit the rules source plus emulator tests. | Implemented — deployment/testing pending | `firebase.database.rules.json` denies by default, scopes reads/writes to `auth.uid`, and limits client action paths. Deploy and exercise the access tests in `SECURITY_OPERATIONS.md`. |
| Critical | S-03: Remove plaintext MT5 passwords from Firebase. | Implemented — data cleanup/rotation pending | The active app UI and bot credential path no longer access `mt5_config`; the legacy seeding script exits without writing. Remove old database data and rotate the broker password if it was stored remotely. |
| High | S-04: Confirm whether the local Firebase service-account key is active; rotate it if exposure is possible, then store it outside the workspace (secret manager or protected service account location). | Open / operator action | `serviceAccountKey.json` exists locally but is ignored and not tracked in current Git history. It remains a high-value administrator credential. |
| High | S-05: Add durable authorization/audit events for all trading-impacting remote actions (start/stop, mode changes, signal approval, broker-config edits). | Open | Current app writes directly to Firebase; code records timestamps but no authenticated actor or tamper-resistant audit trail. Close with server-side event records linked to authenticated UID and monitored alerts. |
| High | S-06: Make pending-signal claiming atomic. | Implemented | `src/bot.py` now uses a Firebase transaction and a unique claim token; only the transaction winner executes a confirmed, unexpired signal. |
| Medium | S-07: Add automated secret scanning and a protected CI gate for tracked files and pull requests. | Implemented — CI activation pending | `.github/workflows/security.yml` runs Gitleaks on repository history. Protect the default branch and require the check. |
| Medium | S-08: Pin/scan Python dependencies in a repeatable CI job. | Partial | CI now runs `pip-audit` and `npm audit`. Pinning/locking Python dependencies remains open. |
| Medium | S-09: Define backup, restore, and incident-response procedures for Firebase data, trade logs, compromised API keys, and broker credentials. | Implemented — exercise pending | `SECURITY_OPERATIONS.md` defines deployment checks, rotation response, and recovery testing. |
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
