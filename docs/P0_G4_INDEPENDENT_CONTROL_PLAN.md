# P0-G4: Independent Control Boundary (Plan, not authorization)

**Status:** PLANNED. No AWS deployment, IAM mutation, secrets, payment,
cloud resources or external authorization are approved.

## Goal
Move beyond the P0-G3 offline simulations by designing independently
enforced approval, spend limit, emergency stop, audit and reconciliation.
The SPRAY runtime must not be able to forge approvals, override a stop,
grant itself IAM privileges, or silently spend outside its ceiling.

## Work packages
### G4-A: Independent trust and approval
- Define versioned canonical request envelope: principal, action, account,
  region, environment, exact USD ceiling, nonce, expiry and digest.
- Specify signer custody outside SPRAY, owner authentication, rotation and
  key compromise response.
- Authenticate revocation snapshots with freshness and monotonic versions.
- One-time approval consumption must be atomic with a dispatch claim.
- Deliver: protocol spec, adversarial vectors, test-only local simulator.

### G4-B: Independent spending control
- Model reserve/settle/refund with immutable transaction IDs and provider
  invoice reference; no double-counting or overspend.
- Reject when provider billing is stale, credit eligibility unverified,
  reconciliation drift exists, or cash ceiling is exceeded.
- Deliver: offline provider adapter interface and fault-injection fixtures.

### G4-C: External stop and audit
- Specify an out-of-process stop authority and lease expiry semantics.
- Anchor audit digests outside SPRAY's mutable datastore.
- Deny on missing stop or audit availability; do not silently degrade.
- Deliver: threat model and simulation-based proof of fail-closed behavior.

### G4-D: Preproduction quality gate
- Separate code CI PASS from authenticated external control evidence.
- Test crash, timeout, split brain, retry, replay, clock skew, key rotation,
  stale revocation, delayed provider charges and rollback.
- Require owner sign-off before any cloud deployment or costs.

## Milestone exit criteria
1. Every critical path is bound to a verified request and single-use token.
2. Stop and budget checks cannot be bypassed by SPRAY-controlled state.
3. Signed approvals, independent evidence and audit are validated with
   externally sourced proofs, not self-reported booleans.
4. Production readiness gate has real, reviewable evidence references.
5. Any production/cloud action is separately authorized by the owner.

## Immediate permitted scope
Repository-only documentation, local mocks, tests and CI. Do not enable
external dispatch, create secrets, alter IAM or spend credits.
