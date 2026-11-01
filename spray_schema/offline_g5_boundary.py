"""P0-G5 offline trust-boundary prototype.

Separate authority and worker objects, opaque capability signatures,
single-use nonce, policy revision, stop epoch and SQLite atomic journal.
All components run in the same Python trust domain for tests only.
No AWS, IAM, network, cloud dispatch or production credentials.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import sqlite3


@dataclass(frozen=True)
class Scope:
    account: str
    region: str
    environment: str
    action: str


@dataclass(frozen=True)
class Capability:
    request_id: str
    nonce: str
    scope: Scope
    cash_ceiling: str
    policy_revision: int
    stop_epoch: int
    expires_at: int
    signature: str = ""


def _payload(cap: Capability) -> bytes:
    data = {
        "request_id": cap.request_id, "nonce": cap.nonce,
        "scope": {"account": cap.scope.account, "region": cap.scope.region,
                  "environment": cap.scope.environment, "action": cap.scope.action},
        "cash_ceiling": cap.cash_ceiling, "policy_revision": cap.policy_revision,
        "stop_epoch": cap.stop_epoch, "expires_at": cap.expires_at,
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _amount(s: str) -> Decimal:
    if not isinstance(s, str):
        raise ValueError("amount must be string")
    try:
        d = Decimal(s)
        if not d.is_finite() or d <= 0 or d.as_tuple().exponent < -2:
            raise ValueError("invalid cash")
        return d
    except InvalidOperation as exc:
        raise ValueError("invalid cash") from exc


class OfflineAuthority:
    """Owner-controlled in tests. Must be separate privileged service in production."""

    def __init__(self, key: bytes, allowed_scopes: frozenset[Scope],
                 max_cash: str, policy_revision: int = 1):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("weak test key")
        self.__key = key
        self.allowed_scopes = allowed_scopes
        self.max_cash = _amount(max_cash)
        self.policy_revision = policy_revision
        self.stop_epoch = 0
        self.stopped = False

    def stop(self) -> None:
        self.stop_epoch += 1
        self.stopped = True

    def resume_test_only(self) -> None:
        self.stop_epoch += 1
        self.stopped = False

    def issue(self, *, request_id: str, nonce: str, scope: Scope,
              cash_ceiling: str, expires_at: int, now: int) -> Capability:
        amount = _amount(cash_ceiling)
        if (self.stopped or scope not in self.allowed_scopes or amount > self.max_cash
                or not isinstance(request_id, str) or not request_id
                or not isinstance(nonce, str) or len(nonce) < 16
                or type(now) is not int or type(expires_at) is not int
                or not now < expires_at <= now + 300):
            raise ValueError("authority denied")
        cap = Capability(request_id, nonce, scope, cash_ceiling,
                         self.policy_revision, self.stop_epoch, expires_at)
        return replace(cap, signature=hmac.new(self.__key, _payload(cap), hashlib.sha256).hexdigest())

    def verify(self, cap: Capability, *, now: int) -> bool:
        if not isinstance(cap, Capability) or self.stopped or type(now) is not int:
            return False
        if (now >= cap.expires_at or cap.policy_revision != self.policy_revision
                or cap.stop_epoch != self.stop_epoch or cap.scope not in self.allowed_scopes):
            return False
        try:
            if _amount(cap.cash_ceiling) > self.max_cash:
                return False
        except ValueError:
            return False
        if not isinstance(cap.signature, str):
            return False
        expected = hmac.new(self.__key, _payload(cap), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, cap.signature)


class OfflineWorker:
    """Worker receives an authority interface, not raw signing key."""

    def __init__(self, database: str, authority: OfflineAuthority, budget: str):
        self.database = database
        self.authority = authority
        self.budget = _amount(budget)
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS grants(nonce TEXT PRIMARY KEY, request_id TEXT UNIQUE, cash TEXT NOT NULL, epoch INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, nonce TEXT NOT NULL, digest TEXT NOT NULL)")

    def authorize_offline(self, cap: Capability, *, now: int,
                          evidence_fresh: bool, stop_reachable: bool) -> bool:
        if not evidence_fresh or not stop_reachable or not self.authority.verify(cap, now=now):
            return False
        try:
            cash = _amount(cap.cash_ceiling)
        except ValueError:
            return False
        with sqlite3.connect(self.database, timeout=15, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            # Recheck immediately before local commit. Still not distributed atomicity.
            if not self.authority.verify(cap, now=now):
                db.execute("ROLLBACK")
                return False
            used = sum((_amount(row[0]) for row in db.execute("SELECT cash FROM grants")), Decimal("0"))
            if used + cash > self.budget:
                db.execute("ROLLBACK")
                return False
            try:
                db.execute("INSERT INTO grants VALUES(?,?,?,?)",
                           (cap.nonce, cap.request_id, str(cash), cap.stop_epoch))
            except sqlite3.IntegrityError:
                db.execute("ROLLBACK")
                return False
            digest = hashlib.sha256(_payload(cap)).hexdigest()
            db.execute("INSERT INTO audit(nonce,digest) VALUES(?,?)", (cap.nonce, digest))
            db.execute("COMMIT")
            return True

    def snapshot(self) -> tuple[int, int]:
        with sqlite3.connect(self.database) as db:
            return (db.execute("SELECT COUNT(*) FROM grants").fetchone()[0],
                    db.execute("SELECT COUNT(*) FROM audit").fetchone()[0])
