"""Offline SQLite prototype of a durable reservation boundary.

NOT a production spending controller. No AWS API, network, IAM, or payment.
A trusted external service must own the database, approvals and stop switch.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3

from spray_schema.execution_safety import AdmissionDecision, ExecutionRequest


@dataclass(frozen=True)
class DurableResult:
    accepted: bool
    reason: str
    remaining_usd: str


class OfflineReservationStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def initialize(self, starting_usd: str) -> None:
        initial = _amount(starting_usd)
        if initial is None:
            raise ValueError("invalid initial cash")
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), remaining TEXT NOT NULL, stopped INTEGER NOT NULL CHECK(stopped IN (0,1)))")
            db.execute("CREATE TABLE IF NOT EXISTS reservations (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, amount TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO control(id,remaining,stopped) VALUES(1,?,0)", (str(initial),))

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def stop(self) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("UPDATE control SET stopped=1 WHERE id=1")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def reserve(self, request: ExecutionRequest, decision: AdmissionDecision,
                *, verifier_available: bool, evidence_fresh: bool) -> DurableResult:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT remaining,stopped FROM control WHERE id=1").fetchone()
                if row is None:
                    return _rollback(db, "not_initialized", "0")
                remaining, stopped = row
                if stopped:
                    return _rollback(db, "stopped", remaining)
                if type(verifier_available) is not bool or not verifier_available:
                    return _rollback(db, "verifier_unavailable", remaining)
                if type(evidence_fresh) is not bool or not evidence_fresh:
                    return _rollback(db, "stale_evidence", remaining)
                if not isinstance(request, ExecutionRequest) or not isinstance(decision, AdmissionDecision) or not decision.permitted:
                    return _rollback(db, "no_admission", remaining)
                amount = _amount(request.estimated_cash_usd)
                if amount is None or not isinstance(request.request_id, str) or not request.request_id or len(request.request_id)>128:
                    return _rollback(db, "invalid_request", remaining)
                canonical = json.dumps(
                    {"request_id": request.request_id, "action": request.action,
                     "payload_sha256": request.payload_sha256, "estimated_cash_usd": str(amount)},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                digest = hashlib.sha256(canonical.encode()).hexdigest()
                if digest != decision.request_digest:
                    return _rollback(db, "admission_mismatch", remaining)
                if db.execute("SELECT 1 FROM reservations WHERE request_id=?", (request.request_id,)).fetchone():
                    return _rollback(db, "replay", remaining)
                balance = Decimal(remaining)
                if amount > balance:
                    return _rollback(db, "insufficient_budget", remaining)
                new_balance = str(balance - amount)
                db.execute("INSERT INTO reservations(request_id,digest,amount) VALUES(?,?,?)",
                           (request.request_id, digest, str(amount)))
                db.execute("UPDATE control SET remaining=? WHERE id=1", (new_balance,))
                db.execute("COMMIT")
                return DurableResult(True, "reserved_offline_only", new_balance)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def snapshot(self) -> tuple[str, bool, int]:
        with self._connect() as db:
            row = db.execute("SELECT remaining,stopped FROM control WHERE id=1").fetchone()
            if row is None:
                raise RuntimeError("not initialized")
            count = db.execute("SELECT count(*) FROM reservations").fetchone()[0]
            return row[0], bool(row[1]), count


def _amount(raw: str) -> Decimal | None:
    if not isinstance(raw, str) or not raw or raw.strip()!=raw:
        return None
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _rollback(db: sqlite3.Connection, reason: str, remaining: str) -> DurableResult:
    db.execute("ROLLBACK")
    return DurableResult(False, reason, remaining)
