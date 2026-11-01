# P0-G3 Offline Audit and Reconciliation

**Status: OFFLINE PROTOTYPE. No production enforcement.**

`spray_schema/offline_audit.py` adds an ordered SQLite hash-chain
journal and exact-Decimal reservation/charge comparison. Tests demonstrate
detection of modified events, deleted middle events, concurrent appends,
persistence after reopening, billing drift, and invalid money inputs.

## Non-negotiable production gaps

- A local hash chain is **not immutable**. An attacker with database access
  can rewrite the whole chain or truncate the tail undetected. Production
  requires independently controlled write-once retention and an externally
  anchored, independently verified head hash and expected event count.
- Audit append is **not in the same transaction** as the reservation store.
  Crash consistency and no-lost-event guarantees are NOT VERIFIED.
- The reconciliation function compares only caller-supplied amounts. It does
  not fetch, authenticate, normalize, or verify AWS bills, credit eligibility,
  pending charges, billing lag, or provider refunds.
- Billing drift produces a `pause_required` reason but does **not** change
  the independent stop switch. Production must enforce the stop externally.
- No real execution or paid resource should be enabled until all of these
  gaps are resolved, threat-modelled, tested and explicitly owner-approved.

Run offline regression suite with
`python3 -m unittest discover -s tests -p 'test_*.py' -v`.
