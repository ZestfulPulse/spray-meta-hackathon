# P0-G4 Hardening: Local Preflight

Adds a test-only allowlist/cash ceiling preflight for permits to highlight
cross-account, cross-region, action/environment mismatch, short nonce and
nonfinite/over-ceiling amounts. It does not enforce production policy unless
wired to an independent trusted authority.

## Critical limitations
- The caller controls allowlists and max ceiling in this offline helper.
- Nonce length does not prove randomness or cryptographic entropy.
- HMAC test signing remains inside the test fixture; there is no protected
  independent signing service.
- G4 claim API does not yet require this preflight. This is an adversarial
  inspection tool, not a gate on dispatch.
- Local audit is not immutable, billing is not provider-authenticated.
- No cloud, IAM, secrets, paid resources or deployment are authorized.

Next acceptance requires immutable owner-approved policy outside SPRAY,
independent signer and revocation, and authenticated provider reconciliation.
