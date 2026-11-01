"""Fail-closed, persistent preflight reservation for opt-in Bedrock text inference.

The ledger reserves worst-case *estimated* charges before each request and never
refunds ambiguous failures. It is a single-host safety gate, not an AWS billing
hard limit or a distributed lock. Price inputs must be independently verified.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_UP
import json
import os
from pathlib import Path
import tempfile

_QUANTUM = Decimal("0.00000001")
_OVERHEAD_TOKENS = 4096


def _positive_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("invalid price or ceiling")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid price or ceiling") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("price or ceiling must be finite and positive")
    return amount


class BedrockBudget:
    """Local persistent USD cap. Share one protected ledger across all test runs."""

    def __init__(self, *, model_id: str, region: str, ledger_path: str,
                 input_usd_per_million: str, output_usd_per_million: str,
                 pricing_source: str, ceiling_usd: str = "1.00"):
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model ID required")
        if not isinstance(region, str) or not region.strip():
            raise ValueError("region required")
        if not isinstance(pricing_source, str) or not pricing_source.startswith(
                "https://aws.amazon.com/bedrock/pricing"):
            raise ValueError("official AWS pricing reference required")
        if not isinstance(ledger_path, (str, os.PathLike)) or not str(ledger_path):
            raise ValueError("persistent ledger path required")
        self.path = Path(ledger_path)
        if not self.path.parent.is_dir():
            raise ValueError("ledger parent must exist on persistent storage")
        self.model_id = model_id
        self.region = region
        self.input_price = _positive_decimal(input_usd_per_million)
        self.output_price = _positive_decimal(output_usd_per_million)
        self.ceiling = _positive_decimal(ceiling_usd)
        if self.ceiling > Decimal("1.00"):
            raise ValueError("owner ceiling is USD 1")
        self.pricing_source = pricing_source
        self.identity = {
            "schema": 1, "model_id": model_id, "region": region,
            "input_usd_per_million": str(self.input_price),
            "output_usd_per_million": str(self.output_price),
            "ceiling_usd": str(self.ceiling), "pricing_source": pricing_source,
        }

    def reserve(self, *, model_id: str, region: str, request_text: str,
                system_text: str, max_output_tokens: int) -> dict:
        if model_id != self.model_id or region != self.region:
            raise PermissionError("model or region differs from verified pricing")
        if not isinstance(request_text, str) or not isinstance(system_text, str):
            raise ValueError("invalid prompt")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 120:
            raise ValueError("invalid output cap")
        # UTF-8 byte count is a deliberately conservative proxy, with an
        # additional token allowance for Converse envelope/hidden overhead.
        estimated_input_tokens = (len(request_text.encode("utf-8")) +
                                  len(system_text.encode("utf-8")) +
                                  _OVERHEAD_TOKENS)
        estimate = ((self.input_price * estimated_input_tokens +
                     self.output_price * max_output_tokens) /
                    Decimal(1000000)).quantize(_QUANTUM, rounding=ROUND_UP)
        if estimate > self.ceiling:
            raise PermissionError("single request exceeds owner ceiling")
        try:
            import fcntl
        except ImportError as exc:
            raise PermissionError("persistent POSIX lock unavailable") from exc
        lock_path = self.path.with_name(self.path.name + ".lock")
        if self.path.is_symlink() or lock_path.is_symlink():
            raise PermissionError("symlinked budget ledger refused")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(lock_path, flags, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if self.path.is_symlink():
                raise PermissionError("symlinked budget ledger refused")
            if self.path.exists():
                try:
                    state = json.loads(self.path.read_text(encoding="utf-8"))
                    if type(state) is not dict or any(
                            state.get(k) != v for k, v in self.identity.items()):
                        raise ValueError("ledger identity mismatch")
                    reserved = Decimal(state["reserved_usd"])
                    requests = state["requests"]
                    if (not reserved.is_finite() or reserved < 0 or
                            reserved > self.ceiling or
                            type(requests) is not int or requests < 0):
                        raise ValueError("invalid ledger state")
                except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
                    raise PermissionError("budget ledger invalid; fail closed") from exc
            else:
                state = dict(self.identity)
                reserved, requests = Decimal("0"), 0
            if reserved + estimate > self.ceiling:
                raise PermissionError("cumulative Bedrock budget exhausted")
            state["reserved_usd"] = str(reserved + estimate)
            state["requests"] = requests + 1
            # Durable atomic replace, with no prompts, credentials or page data.
            fd, tmp_name = tempfile.mkstemp(prefix=".spray-budget-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(state, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp_name, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            return {
                "request_reserved_usd": str(estimate),
                "cumulative_reserved_usd": state["reserved_usd"],
                "requests": state["requests"],
                "estimated_input_token_ceiling": estimated_input_tokens,
                "output_token_ceiling": max_output_tokens,
            }
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
