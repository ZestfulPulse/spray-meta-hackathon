"""Deterministic, offline fault simulator for P0-G3 atomic admission.

No AWS APIs, no payment methods and no persistent authority. The lock and
transaction below model behavior that a future independently controlled
durable service must enforce and prove under real failures.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from decimal import Decimal, InvalidOperation
from threading import Lock

from spray_schema.execution_safety import AdmissionDecision, ExecutionRequest


@dataclass(frozen=True)
class ReservationResult:
    accepted: bool
    reason: str
    remaining_usd: str


class SimulatedIndependentBudget:
    def __init__(self, cash_usd: str):
        try:
            amount = Decimal(cash_usd)
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("invalid initial budget")
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid initial budget")
        self._balance = amount
        self._used_ids: set[str] = set()
        self._lock = Lock()
        self._stopped = False

    def stop(self) -> None:
        with self._lock:
            self._stopped = True

    def reserve(self, request: ExecutionRequest,
                decision: AdmissionDecision, *, verifier_available: bool = True,
                evidence_fresh: bool = True) -> ReservationResult:
        with self._lock:
            balance = str(self._balance)
            if self._stopped:
                return ReservationResult(False, "stopped", balance)
            if type(verifier_available) is not bool or not verifier_available:
                return ReservationResult(False, "verifier_unavailable", balance)
            if type(evidence_fresh) is not bool or not evidence_fresh:
                return ReservationResult(False, "stale_evidence", balance)
            if not isinstance(decision, AdmissionDecision) or not decision.permitted or not decision.request_digest:
                return ReservationResult(False, "no_admission", balance)
            if not isinstance(request, ExecutionRequest):
                return ReservationResult(False, "invalid_request", balance)
            request_id, amount_usd = request.request_id, request.estimated_cash_usd
            if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                return ReservationResult(False, "invalid_request_id", balance)
            if not isinstance(amount_usd, str) or not amount_usd or amount_usd.strip() != amount_usd:
                return ReservationResult(False, "invalid_amount", balance)
            try:
                amount = Decimal(amount_usd)
            except (InvalidOperation, ValueError):
                return ReservationResult(False, "invalid_amount", balance)
            if not amount.is_finite() or amount < 0:
                return ReservationResult(False, "invalid_amount", balance)
            canonical = json.dumps({"request_id": request_id, "action": request.action,
                "payload_sha256": request.payload_sha256, "estimated_cash_usd": str(amount)},
                sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            if hashlib.sha256(canonical.encode()).hexdigest() != decision.request_digest:
                return ReservationResult(False, "admission_mismatch", balance)
            if request_id in self._used_ids:
                return ReservationResult(False, "replay", balance)
            if amount > self._balance:
                return ReservationResult(False, "insufficient_budget", balance)
            # Atomic in-memory simulation only; not a durable external commit.
            self._balance -= amount
            self._used_ids.add(request_id)
            return ReservationResult(True, "reserved_simulation_only", str(self._balance))

    def snapshot(self) -> tuple[str, bool, int]:
        with self._lock:
            return str(self._balance), self._stopped, len(self._used_ids)
