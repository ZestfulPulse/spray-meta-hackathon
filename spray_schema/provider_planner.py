"""Provider-agnostic planner interface for bounded local-browser QA.

This module defines a minimal, stdlib-only planner protocol so that different
model backends (Gemini, Meta Muse Spark, ...) can be driven through one
interface. The concrete adapters live here (GeminiProviderAdapter) and in the
per-provider modules (spray_schema.meta_planner.MetaPlanner).

The interface deliberately mirrors ``GeminiPlanner.choose`` so wrapping the
existing Gemini path does NOT change its behavior. See
spray_schema.gemini_planner for the authoritative Gemini implementation, which
this module wraps unchanged.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .gemini_planner import GeminiPlanner


@runtime_checkable
class Planner(Protocol):
    """Provider-agnostic planner protocol.

    A conforming planner exposes read-only ``provider`` and ``model_id``
    attributes and a ``choose`` method with the same keyword-only signature as
    ``GeminiPlanner.choose``. Implementations must never leak provider bodies or
    headers in exceptions and must apply S6 redaction to any outbound content.
    """

    provider: str
    model_id: str

    def choose(self, *, observation: dict, target_text: str,
               history: Any) -> dict:
        ...


class GeminiProviderAdapter:
    """Adapt the existing GeminiPlanner to the provider-agnostic interface.

    Construction and ``choose`` are delegated to a wrapped GeminiPlanner with
    its behavior UNCHANGED: the same free-tier attestation, header-only API key,
    S6 redaction, request cap and decision validation all apply because this
    adapter does not reimplement any of them. It only exposes ``provider`` and
    ``model_id`` and forwards calls.
    """

    provider = "gemini"

    def __init__(self, *, planner: GeminiPlanner | None = None, **kwargs: Any):
        # Either wrap a pre-built GeminiPlanner or construct one from kwargs.
        # Either way, GeminiPlanner's own __init__ enforces every precondition
        # (free-tier attestation, allowed model, request cap, key presence).
        if planner is not None:
            if kwargs:
                raise ValueError(
                    "pass either a built planner or constructor kwargs, not both")
            self._planner = planner
        else:
            self._planner = GeminiPlanner(**kwargs)

    @property
    def model_id(self) -> str:
        return self._planner.model_id

    @property
    def planner(self) -> GeminiPlanner:
        """The wrapped GeminiPlanner (read-only access for callers/tests)."""
        return self._planner

    def choose(self, *, observation, target_text, history):
        # Pure delegation: identical behavior to calling GeminiPlanner directly.
        return self._planner.choose(
            observation=observation, target_text=target_text, history=history)
