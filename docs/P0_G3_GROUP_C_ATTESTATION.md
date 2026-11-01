# P0-G3 Group C: Approval Authentication Prototype

This is an **offline HMAC verifier prototype**, not a live trust boundary.
`offline_approval_attestation.py` verifies a request-bound, action-bound,
short-lived signature, key revocation and approval revocation. Tests cover
tampering, wrong keys, missing revocation snapshot, clock invalidity,
expiry, future issuance and excessive lifetime.

## Production acceptance gates (all NOT VERIFIED)
1. Owner-controlled signing service issues approvals only after independent
   human authentication; SPRAY cannot read or request signing secrets.
2. Approval binds to the exact canonical execution request, immutable
   payload digest, tenant/account, environment, amount ceiling, scope and
   single-use authorization ID. The current prototype does **not** bind
   tenant/account/environment or approval ID to a durable consume operation.
3. Trusted clock and revocation feed are independent, authenticated,
   monotonically versioned, bounded-staleness and fail closed on outage.
4. Key rotation, compromise recovery, approval revocation, replay
   consumption and lease expiry are atomic with independent dispatch.
5. Approval verification is connected to the transaction and actual
   dispatcher only after an external security review and explicit approval.

**No AWS deployment, IAM changes, secrets or paid resources are authorized.**
