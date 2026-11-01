# P0-G3 Atomic Reservation + Audit Offline Prototype

The `OfflineIntegratedControl` model puts reservation debit, replay guard,
and hash-chain audit event into **one SQLite transaction**. An injected
exception between debit and audit append rolls both back. A stop operation
also commits its flag and audit event together.

## Scope and proof boundary

- Tests cover injected transaction failure, concurrency, restart, and
  stop persistence in an offline database.
- A denial does not append a durable event in this prototype. Production
  requires independently retained denial attempts and alerting.
- An in-process SQLite file is not a trusted independent control plane.
  Caller-supplied approval booleans are not authenticated attestations.
- A successful reservation does not authorize or dispatch AWS requests.
  AWS calls and external charges cannot participate in this SQLite
  transaction; a production design requires idempotent dispatch, leases,
  settlement reconciliation, external stop enforcement, and recovery.
- The local hash chain is tamper-evident only against partial edits, not
  a privileged attacker rewriting or truncating the complete journal.
- No cloud resources, permissions, secrets or paid services are changed.

Run `python3 -m unittest discover -s tests -p 'test_*.py' -v`.
Do not mark real-world enforcement PASS based on these tests.
