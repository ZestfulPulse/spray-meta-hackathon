# P0-G3 Group E: Operational Readiness and Closeout

## Current disposition
**OFFLINE PROTOTYPE INTEGRATION: PASS after merge/CI evidence.**
**PRODUCTION EXECUTION READINESS: NOT_VERIFIED.**
**AWS DEPLOYMENT / IAM / SECRETS / PAID RESOURCES: NOT AUTHORIZED.**

## Quality gate
`spray_schema/production_readiness.py` requires independently referenced
evidence for all eleven external controls. Missing, duplicate, invalid or
non-PASS evidence fails closed. Even a complete evidence matrix is only a
review input, **not** an execution authorization.

## Operational runbook (future, not performed)
1. **Before enabling:** owner reviews scope, cash ceiling, account, region,
   lease TTL, IAM policy, independent signer and stop path. Capture approvals.
2. **Dry run:** verify expired/revoked approval denial, network partition,
   double-submit, DB crash recovery, emergency stop, billing lag and rollback.
3. **Deploy:** only under a separate explicit owner authorization, first to
   isolated nonproduction with spending disabled and zero privileged secrets.
4. **Observe:** independent audit, budget reservations vs settled charges,
   credit eligibility, alerting and human kill-switch effectiveness.
5. **Rollback:** disable dispatch externally, revoke credentials/leases,
   retain audit, reconcile outstanding charges and require fresh owner approval
   to re-enable.
6. **Incident:** stop on reconciliation drift, compromised keys, missing audit,
   verifier outage, IAM expansion, or untrusted clock/revocation state.

## Acceptance boundary
GitHub CI proves only Python offline tests. It does not verify the external
signer, durable approval consumption, IAM scope, real billing, independent
stop, tamper-proof retention, or actual provider failure handling.
Do not label P0-G3 production-ready until real external evidence exists.
