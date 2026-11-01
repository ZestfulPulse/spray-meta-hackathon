"""Offline integrated reservation + audit transaction prototype.

One SQLite transaction updates budget, replay guard, and hash-chain event.
Not an external authorization service and not a real spending controller.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import sqlite3

from spray_schema.execution_safety import AdmissionDecision, ExecutionRequest
from spray_schema.offline_audit import OfflineAuditJournal
from spray_schema.offline_reservation_store import (
    DurableResult, OfflineReservationStore, _amount,
)


class OfflineIntegratedControl:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.reservations = OfflineReservationStore(db_path)
        self.audit = OfflineAuditJournal(db_path)

    def initialize(self, starting_usd: str) -> None:
        self.reservations.initialize(starting_usd)
        self.audit.initialize()

    @staticmethod
    def _append_in_transaction(db, event_type: str, request_id: str, details: dict) -> None:
        latest = db.execute(
            "SELECT sequence,event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = latest[0] + 1 if latest else 1
        previous = latest[1] if latest else "0" * 64
        details_json = json.dumps(details, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=True, allow_nan=False)
        digest = OfflineAuditJournal._hash(
            sequence, event_type, request_id, details_json, previous)
        db.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?)",
                   (sequence, event_type, request_id, details_json, previous, digest))

    def reserve(self, request: ExecutionRequest, decision: AdmissionDecision,
                *, verifier_available: bool, evidence_fresh: bool,
                inject_failure_after_reservation: bool = False) -> DurableResult:
        with self.reservations._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT remaining,stopped FROM control WHERE id=1").fetchone()
                if row is None:
                    return self._deny(db, "not_initialized", "0")
                remaining, stopped = row
                if stopped:
                    return self._deny(db, "stopped", remaining)
                if type(verifier_available) is not bool or not verifier_available:
                    return self._deny(db, "verifier_unavailable", remaining)
                if type(evidence_fresh) is not bool or not evidence_fresh:
                    return self._deny(db, "stale_evidence", remaining)
                if not isinstance(request, ExecutionRequest) or not isinstance(decision, AdmissionDecision) or not decision.permitted:
                    return self._deny(db, "no_admission", remaining)
                amount = _amount(request.estimated_cash_usd)
                if amount is None or not isinstance(request.request_id, str) or not request.request_id or len(request.request_id) > 128:
                    return self._deny(db, "invalid_request", remaining)
                canonical = json.dumps(
                    {"request_id": request.request_id, "action": request.action,
                     "payload_sha256": request.payload_sha256, "estimated_cash_usd": str(amount)},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                digest = hashlib.sha256(canonical.encode()).hexdigest()
                if digest != decision.request_digest:
                    return self._deny(db, "admission_mismatch", remaining)
                if db.execute("SELECT 1 FROM reservations WHERE request_id=?",
                              (request.request_id,)).fetchone():
                    return self._deny(db, "replay", remaining)
                balance = Decimal(remaining)
                if amount > balance:
                    return self._deny(db, "insufficient_budget", remaining)
                new_balance = str(balance - amount)
                db.execute("INSERT INTO reservations(request_id,digest,amount) VALUES(?,?,?)",
                           (request.request_id, digest, str(amount)))
                db.execute("UPDATE control SET remaining=? WHERE id=1", (new_balance,))
                if inject_failure_after_reservation:
                    raise RuntimeError("injected failure before audit append")
                self._append_in_transaction(db, "reservation_accepted", request.request_id,
                                            {"digest": digest, "amount_usd": str(amount),
                                             "remaining_usd": new_balance})
                db.execute("COMMIT")
                return DurableResult(True, "reserved_and_audited_offline_only", new_balance)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def stop(self) -> None:
        with self.reservations._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("UPDATE control SET stopped=1 WHERE id=1")
                self._append_in_transaction(db, "emergency_stop", "owner", {})
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _deny(db, reason: str, remaining: str) -> DurableResult:
        db.execute("ROLLBACK")
        return DurableResult(False, reason, remaining)
