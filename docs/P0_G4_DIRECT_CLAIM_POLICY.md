# P0-G4 Direct Offline Claim Policy Requirement

The local `OfflineControlPlane.claim` method now fails closed when its
`OfflinePolicy` argument is missing. The gateway forwards the same policy.
The direct-call tests now supply policy and include an explicit bypass
regression test: missing policy must not reserve funds.

**Boundary:** This closes the *accidental missing-policy* path in the offline
Python API. It does not make enforcement non-bypassable by a compromised
process. Callers still control policy, signer key, revocations, clock,
billing and stop evidence, and may call SQLite directly. True independence
requires out-of-process authority and privilege separation. No AWS,
secrets, IAM changes, cloud dispatch or paid resources are authorized.
