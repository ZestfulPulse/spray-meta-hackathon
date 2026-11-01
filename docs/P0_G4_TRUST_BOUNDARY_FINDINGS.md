# P0-G4 Trust Boundary: Adversarial Evidence

This offline test milestone deliberately demonstrates that a caller with the
test signing key can invent a permissive policy and submit favorable
stop/billing/revocation booleans. The existing SQLite reference model will
reserve funds. That is **expected proof of the remaining weakness**, not
a successful independent authorization.

The boundary report never returns production-ready, even when every
self-reported flag is true. It cannot verify the origin of those claims.

## Next actual security architecture requirements
1. An independent authority must own policy, identity and signing keys,
   inaccessible to the SPRAY worker process.
2. A separate executor must refuse work without an externally verified
   capability and monotonic revocation/stop epoch.
3. Provider-authenticated spending evidence and independent immutable audit
   anchoring are needed before real billing claims can be trusted.
4. IAM privilege isolation, cloud access and any paid test need explicit
   owner approval. They are NOT included here.

No cloud deployment, IAM edits, secrets, external dispatch or spend.
