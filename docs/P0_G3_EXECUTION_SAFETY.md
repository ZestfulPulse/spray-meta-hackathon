# P0-G3: Independent Execution Safety Gate

**Status: DESIGN + OFFLINE TESTS ONLY. No AWS deployment or real spend authority.**

## Objective
Separate a proposed execution request from any real dispatch. A trusted,
owner-controlled verifier must provide independently attested evidence, and a
separate transactional dispatcher must enforce it atomically. A successful
offline `evaluate_execution` result is **not** a spending authorization.

## Offline gate
`spray_schema/execution_safety.py` checks a stable request ID, action,
SHA-256 payload fingerprint, exact Decimal cash estimate and authorized cash
ceiling, owner approval, both stop switches, verified identity/IAM scope,
verified cash and lease, unused idempotency key and fresh evidence. Any
missing, malformed, negative or stale evidence denies by default. This gate
has no credentials, network calls, AWS APIs, side effects or dispatch method.

## Real-world acceptance requirements (NOT IMPLEMENTED)
1. **Independent owner stop:** deny before and during dispatch; the agent
   cannot disable it. Fail closed on timeout, unavailable verifier or stale data.
2. **Atomic reservation:** reserve budget and idempotency key in one durable
   conditional transaction. Duplicate requests, races, retries and crashes
   cannot charge twice or bypass a revoked lease.
3. **Bounded permissions:** no broad IAM write roles, self-granting, secret
   retrieval or account/billing administration for the agent.
4. **Cash vs credits:** provider-specific nonfungible credit rules; no credit
   may be treated as settled cash without independently verified settlement.
5. **Audit:** append-only externally retained authorization, denial, stop,
   dispatch and settlement records; independently test tamper resistance.
6. **Reconciliation:** compare provider charges with ledger, pause on drift,
   and prove termination when the cash or credits are exhausted.
7. **Failure drills:** replay, double-submit, concurrent requests, revoked
   lease, stale evidence, network partition, verifier outage, AWS billing lag,
   and emergency stop during execution.
8. **Cost gate:** all AWS resources, paid services, secrets and IAM changes
   require separate explicit owner approval before provisioning.

## Verification
Run `python3 -m unittest discover -s tests -p 'test_*.py' -v`.
Record exact head SHA and GitHub Actions result. Do not call P0-G3 CLOSED
until independent enforcement and the failure drills above have real evidence.
