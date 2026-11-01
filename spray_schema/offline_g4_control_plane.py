"""P0-G4 A-D unified OFFLINE reference control plane.

Not an independent authority: caller provides keys, revocations, clock,
stop status and billing evidence. No network, cloud, IAM or dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path


@dataclass(frozen=True)
class Permit:
    request_id: str
    principal: str
    action: str
    account: str
    region: str
    environment: str
    ceiling_usd: str
    nonce: str
    issued_at: int
    expires_at: int
    key_id: str
    signature: str = ""


def _canonical(permit: Permit) -> bytes:
    return json.dumps({k: getattr(permit, k) for k in Permit.__dataclass_fields__
                       if k != "signature"}, sort_keys=True, separators=(",", ":")).encode()


def sign_test_permit(permit: Permit, key: bytes) -> Permit:
    """Test-only fixture, never a production signing service."""
    from dataclasses import replace
    return replace(permit, signature=hmac.new(key, _canonical(permit), hashlib.sha256).hexdigest())


def _money(value: str) -> Decimal:
    try:
        d = Decimal(value)
        if not d.is_finite() or d < 0 or d.as_tuple().exponent < -2:
            raise ValueError("invalid amount")
        return d
    except (InvalidOperation, TypeError, AttributeError) as exc:
        raise ValueError("invalid amount") from exc


@dataclass(frozen=True)
class Outcome:
    accepted: bool
    reason: str


class OfflineControlPlane:
    def __init__(self, path: str):
        self.path = str(Path(path))

    def initialize(self, budget_usd: str) -> None:
        budget = _money(budget_usd)
        with sqlite3.connect(self.path, timeout=15) as db:
            db.execute("CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), budget TEXT NOT NULL, stopped INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS claims (nonce TEXT PRIMARY KEY, request_id TEXT UNIQUE, reserved TEXT NOT NULL, settled TEXT, provider_ref TEXT UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, data TEXT NOT NULL, prev_hash TEXT NOT NULL, digest TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO config VALUES (1, ?, 0)", (str(budget),))

    @staticmethod
    def _audit(db: sqlite3.Connection, kind: str, data: str) -> None:
        last = db.execute("SELECT digest FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last[0] if last else "0" * 64
        digest = hashlib.sha256(json.dumps([kind, data, prev], separators=(",", ":")).encode()).hexdigest()
        db.execute("INSERT INTO journal(kind,data,prev_hash,digest) VALUES(?,?,?,?)", (kind, data, prev, digest))

    def stop(self) -> None:
        with sqlite3.connect(self.path, timeout=15, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE config SET stopped=1 WHERE id=1")
            self._audit(db, "stop", "local_stop")
            db.execute("COMMIT")

    def claim(self, permit: Permit, *, keyring: dict[str, bytes], now: int,
              revoked_nonces: frozenset[str], stop_authority_available: bool,
              stop_active: bool, revocation_fresh: bool, billing_fresh: bool,
              provider_credit_verified: bool, inject_failure: bool = False,
              policy: object = None) -> Outcome:
        from spray_schema.offline_g4_preflight import check_permit_shape
        from spray_schema.offline_g4_scoped_gateway import OfflinePolicy
        if not isinstance(policy, OfflinePolicy):
            return Outcome(False, "missing_policy")
        preflight = check_permit_shape(
            permit, allowed_accounts=policy.allowed_accounts,
            allowed_regions=policy.allowed_regions,
            allowed_actions=policy.allowed_actions,
            allowed_environments=policy.allowed_environments,
            max_cash_usd=policy.max_cash_usd)
        if not preflight.acceptable:
            return Outcome(False, "preflight_" + ",".join(preflight.failures))
        if not isinstance(permit, Permit) or type(now) is not int or now < 0:
            return Outcome(False, "invalid_input")
        values = (permit.request_id, permit.principal, permit.action, permit.account,
                  permit.region, permit.environment, permit.nonce, permit.key_id)
        if any(not isinstance(v, str) or not v or len(v) > 128 for v in values):
            return Outcome(False, "invalid_scope")
        if type(permit.issued_at) is not int or type(permit.expires_at) is not int:
            return Outcome(False, "invalid_time")
        if permit.issued_at > now or permit.expires_at <= now or not 0 < permit.expires_at - permit.issued_at <= 300:
            return Outcome(False, "expired")
        if not all((stop_authority_available, revocation_fresh, billing_fresh, provider_credit_verified)):
            return Outcome(False, "external_evidence_unavailable")
        if stop_active or not isinstance(revoked_nonces, frozenset) or permit.nonce in revoked_nonces:
            return Outcome(False, "stopped_or_revoked")
        if not isinstance(keyring, dict):
            return Outcome(False, "invalid_keyring")
        key = keyring.get(permit.key_id)
        if not isinstance(key, bytes) or len(key) < 32 or not isinstance(permit.signature, str):
            return Outcome(False, "unknown_signer")
        if not hmac.compare_digest(hmac.new(key, _canonical(permit), hashlib.sha256).hexdigest(), permit.signature):
            return Outcome(False, "bad_signature")
        try:
            amount = _money(permit.ceiling_usd)
        except ValueError:
            return Outcome(False, "invalid_amount")
        if amount <= 0:
            return Outcome(False, "invalid_amount")
        with sqlite3.connect(self.path, timeout=15, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            stopped, budget = db.execute("SELECT stopped,budget FROM config WHERE id=1").fetchone()
            if stopped:
                db.execute("ROLLBACK")
                return Outcome(False, "durable_stop")
            used = sum((_money(row[0]) for row in db.execute("SELECT COALESCE(settled,reserved) FROM claims")), Decimal("0"))
            if used + amount > _money(budget):
                db.execute("ROLLBACK")
                return Outcome(False, "budget_exceeded")
            try:
                db.execute("INSERT INTO claims(nonce,request_id,reserved) VALUES(?,?,?)",
                           (permit.nonce, permit.request_id, str(amount)))
            except sqlite3.IntegrityError:
                db.execute("ROLLBACK")
                return Outcome(False, "replay")
            if inject_failure:
                db.execute("ROLLBACK")
                raise RuntimeError("injected failure before audit commit")
            self._audit(db, "claim", permit.nonce)
            db.execute("COMMIT")
            return Outcome(True, "reserved_offline")

    def settle(self, nonce: str, provider_ref: str, actual_usd: str) -> Outcome:
        if not isinstance(nonce, str) or not nonce or not isinstance(provider_ref, str) or not provider_ref:
            return Outcome(False, "invalid_reference")
        try:
            actual = _money(actual_usd)
        except ValueError:
            return Outcome(False, "invalid_amount")
        with sqlite3.connect(self.path, timeout=15, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reserved,settled FROM claims WHERE nonce=?", (nonce,)).fetchone()
            if row is None or row[1] is not None:
                db.execute("ROLLBACK")
                return Outcome(False, "missing_or_settled")
            if actual > _money(row[0]):
                db.execute("ROLLBACK")
                return Outcome(False, "settlement_exceeds_reservation")
            try:
                db.execute("UPDATE claims SET settled=?,provider_ref=? WHERE nonce=?",
                           (str(actual), provider_ref, nonce))
            except sqlite3.IntegrityError:
                db.execute("ROLLBACK")
                return Outcome(False, "duplicate_provider_reference")
            self._audit(db, "settle", provider_ref)
            db.execute("COMMIT")
            return Outcome(True, "settled_offline")

    def verify_audit(self) -> bool:
        with sqlite3.connect(self.path) as db:
            prev = "0" * 64
            for kind, data, prior, digest in db.execute("SELECT kind,data,prev_hash,digest FROM journal ORDER BY seq"):
                expected = hashlib.sha256(json.dumps([kind, data, prev], separators=(",", ":")).encode()).hexdigest()
                if prior != prev or digest != expected:
                    return False
                prev = digest
            return True

    def snapshot(self) -> tuple[str, int, int]:
        with sqlite3.connect(self.path) as db:
            budget = _money(db.execute("SELECT budget FROM config WHERE id=1").fetchone()[0])
            rows = db.execute("SELECT COALESCE(settled,reserved) FROM claims").fetchall()
            used = sum((_money(row[0]) for row in rows), Decimal("0"))
            return str(budget - used), len(rows), db.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
