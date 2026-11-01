"""Offline append-only hash-chain audit and reconciliation prototype.

No cloud APIs, dispatch, payments or privileged credentials. This module
demonstrates integrity detection, not tamper-proof external audit retention.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3


@dataclass(frozen=True)
class AuditVerification:
    valid: bool
    reason: str
    entries: int


class OfflineAuditJournal:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def initialize(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS audit_events (
                sequence INTEGER PRIMARY KEY, event_type TEXT NOT NULL,
                request_id TEXT NOT NULL, details_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL)""")

    @staticmethod
    def _hash(sequence: int, event_type: str, request_id: str,
              details_json: str, previous_hash: str) -> str:
        payload = json.dumps(
            {"sequence": sequence, "event_type": event_type, "request_id": request_id,
             "details_json": details_json, "previous_hash": previous_hash},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def append(self, event_type: str, request_id: str, details: dict) -> str:
        if not isinstance(event_type, str) or not event_type or len(event_type) > 128:
            raise ValueError("invalid event_type")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError("invalid request_id")
        if not isinstance(details, dict):
            raise ValueError("invalid details")
        try:
            details_json = json.dumps(details, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid details") from exc
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                latest = db.execute("SELECT sequence,event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
                sequence = latest[0] + 1 if latest else 1
                previous = latest[1] if latest else "0" * 64
                digest = self._hash(sequence, event_type, request_id, details_json, previous)
                db.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?)",
                           (sequence, event_type, request_id, details_json, previous, digest))
                db.execute("COMMIT")
                return digest
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def verify(self) -> AuditVerification:
        with self._connect() as db:
            rows = db.execute("SELECT sequence,event_type,request_id,details_json,previous_hash,event_hash FROM audit_events ORDER BY sequence").fetchall()
        previous = "0" * 64
        for expected_sequence, row in enumerate(rows, 1):
            sequence, event_type, request_id, details_json, previous_hash, event_hash = row
            if sequence != expected_sequence or previous_hash != previous:
                return AuditVerification(False, "broken_chain", expected_sequence - 1)
            if self._hash(sequence, event_type, request_id, details_json, previous_hash) != event_hash:
                return AuditVerification(False, "modified_event", expected_sequence - 1)
            previous = event_hash
        return AuditVerification(True, "valid_local_chain_only", len(rows))


@dataclass(frozen=True)
class ReconciliationResult:
    matched: bool
    reason: str
    reserved_usd: str
    charged_usd: str


def reconcile_offline(reserved_usd: str, charged_usd: str) -> ReconciliationResult:
    def parse(raw):
        if not isinstance(raw, str) or not raw or raw.strip() != raw:
            return None
        try:
            amount = Decimal(raw)
        except (InvalidOperation, ValueError):
            return None
        return amount if amount.is_finite() and amount >= 0 else None
    reserved, charged = parse(reserved_usd), parse(charged_usd)
    if reserved is None or charged is None:
        return ReconciliationResult(False, "invalid_money", reserved_usd, charged_usd)
    if reserved != charged:
        return ReconciliationResult(False, "billing_drift_pause_required", str(reserved), str(charged))
    return ReconciliationResult(True, "matched_offline_only", str(reserved), str(charged))
