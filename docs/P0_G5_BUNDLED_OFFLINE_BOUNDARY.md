# P0-G5: Bundled Offline Authority / Worker Boundary

This **single milestone** groups policy-scoped capability issuance, worker
verification, stop epoch, replay protection, budget reservation and audit
recording with integrated adversarial tests.

## Components
- `OfflineAuthority`: test-only owner policy, signing key, revision and stop epoch.
- `Capability`: canonical signed scope, cash ceiling, expiry, nonce and epoch.
- `OfflineWorker`: checks authority verification, rejects stale evidence,
  enforces local budget, commits grant and audit in one SQLite transaction.
- Regression suite: tampering, replay, stop/resume, stale authority,
  policy revision, expiry and concurrent budget race.

## Security boundaries not yet established
These are separate Python objects in one process, **not independent security
principals**. A malicious process may still mutate authority state, invoke
test-only resume, bypass the worker and write SQLite directly. The worker's
audit hash is not anchored externally, and the provider's actual billing is
not reconciled. Stop and authorization are not distributed-transaction
atomic; a remote stop can race with a local commit.

No network calls, AWS/IAM, production secrets, deployment, paid resources or
real execution. CI PASS may close only the OFFLINE PROTOTYPE. Real external
authority, executor isolation and verified provider billing require
separate explicit owner approval.
