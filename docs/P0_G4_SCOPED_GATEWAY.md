# P0-G4 Scoped Offline Gateway

The `ScopedOfflineGateway` now composes the existing offline permit
preflight and reservation method. A missing policy, disallowed account,
region, action or environment, invalid nonce, or excessive cash ceiling
is rejected **before** the SQLite claim transaction. Tests verify denied
requests do not change local accounting.

## Important limitation
The gateway is opt-in and `OfflineControlPlane.claim` remains directly
callable. The policy and verification evidence are supplied in-process,
so a compromised SPRAY process can bypass the facade. This is a safer
offline integration API, **not mandatory independent enforcement**.
Production enforcement must live outside SPRAY's trust boundary and
must not expose a bypassable direct claim/dispatch path.

No AWS, IAM, secrets, deployment or spending authority is included.
