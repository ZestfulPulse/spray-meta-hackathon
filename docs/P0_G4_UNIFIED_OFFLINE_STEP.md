# P0-G4 A-D Unified Offline Step

**Scope:** one consolidated local simulation milestone, not production enforcement.

| Group | Implemented offline | Still NOT VERIFIED externally |
| --- | --- | --- |
| G4-A | Canonical signed permit bound to principal/action/account/region/environment/ceiling/nonce/expiry | Independent signing service, protected keys, owner auth, authenticated revocation |
| G4-B | Atomic SQLite reserve/settle, budget ceiling, provider-ref deduplication, partial refund accounting | Real provider invoices, AWS credits, stale billing guarantees, distributed enforcement |
| G4-C | Durable local stop, hash-chain audit, tamper detection for simple modifications | External stop authority, immutable remote audit anchoring |
| G4-D | Replay, concurrency, crash rollback, tamper, revocation, expiry, outage and credit-denial tests | Real AWS fault drills, isolated IAM, external evidence and owner deployment approval |

## Important threat model limitations
The caller supplies signer keys, clock, revocation, stop and billing evidence.
A compromised SPRAY process can fabricate those inputs. SQLite and the local
hash chain can be rewritten by a privileged attacker. Provider settlement
amounts are caller-supplied and cannot prove actual billing. Claim and
settlement are local accounting only, **not execution or AWS spending**.

## Closure criteria
CI and merge can close only the **P0-G4 OFFLINE SIMULATION** milestone.
P0-G4 independent production controls remain OPEN / NOT_VERIFIED until
external authorities, real provider data, immutable audit anchors and
separately authorized deployment are demonstrated.

No AWS/IAM, secrets, network calls, or paid resources are used.
