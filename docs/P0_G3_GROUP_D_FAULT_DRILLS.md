# P0-G3 Group D: Offline Integration Fault Drills

The harness joins admission checks, short-lived signed approval validation,
and an atomic SQLite reservation + audit transaction. This is an offline
integration **model**, not an external control plane.

## Test matrix
- Valid signed approval + available budget: reserve and audit.
- Replay of same request: deny without second charge.
- Revoked approval, expired approval, unknown signer: deny.
- Verifier outage, stale evidence, emergency stop: deny.
- Concurrent requests exceeding budget: at most the budgeted count succeeds.
- Injected failure between reservation and audit: rollback both.

## Remaining external gaps
Independent human authentication, protected signing keys, trusted clock,
authenticated revocation feed, durable cross-process lease ownership,
external immutable audit, cloud-provider charge reconciliation, and
AWS dispatcher failure drills remain **NOT VERIFIED**. The prototype accepts
test-provided trusted snapshots; never use them as production authority.

No AWS deployment, paid resources, secrets or IAM changes.
