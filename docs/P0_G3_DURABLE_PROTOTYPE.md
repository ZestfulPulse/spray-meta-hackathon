# P0-G3 Durable Reservation Prototype

**Offline prototype only. Not an AWS or paid-service enforcement boundary.**

`spray_schema/offline_reservation_store.py` uses a local SQLite file and
`BEGIN IMMEDIATE` to serialize budget reservations, request-ID replay
protection, and an irreversible stop flag. Tests cover simultaneous requests,
process reopen, replay, stop persistence, verifier outage, stale evidence,
changed request contents, and prevention of accidental budget reset.

## Threat model and limitations

- The agent must **not** own the database, file permissions, or stop switch in
  production. SQLite in the same process is only a model, not an independent
  authorization service.
- A signed or otherwise authenticated verifier snapshot, expiration timestamp,
  actor identity, approved action scope and durable lease revocation are still
  missing. Caller-supplied booleans are **not** trusted evidence.
- Atomic database reservation is **not** atomic with an AWS API or provider
  charge. No external dispatch, provider reconciliation, cancellation, refund
  or billing-lag protection is implemented.
- Production design must use independently controlled credentials, durable
  transactions, restrictive IAM, authenticated approval, audit retention,
  emergency-stop propagation, and chaos tests against the actual infrastructure.
- No production deployment, cloud account modification or expense is approved
  by this prototype.

## Offline QA

`python3 -m unittest discover -s tests -p 'test_*.py' -v`

A green CI demonstrates only this offline prototype and existing tests.
It must not be interpreted as proof of real-world spending safety.
