"""Offline Gemini free-tier credential preflight; presence/config check only.

This reports whether a Gemini credential is present in the environment and the
planner's configured quota-guard values. It performs NO network call and never
stores, returns, prints, logs, or writes the credential value itself: only a
boolean presence flag leaves this module. When the credential is absent the
preflight stops and reports; it never substitutes any paid alternative.

The quota-guard values mirror the existing perimeter in
``spray_schema.gemini_planner`` (which this module does NOT modify). The model
id and max output tokens are imported directly from the planner so the real
configured values are reported; the request cap (4) and timeout (10) are the
GeminiPlanner constructor's documented defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .gemini_planner import _MAX_OUTPUT_TOKENS, _MODEL_ID
from .model_observation import redact

# Documented GeminiPlanner constructor defaults (perimeter, not modified here).
_DEFAULT_MAX_REQUESTS = 4
_DEFAULT_TIMEOUT = 10
_ENV_KEY = "GEMINI_API_KEY"

_ABSENT_MESSAGE = (
    "GEMINI_API_KEY not present: STOP and report. "
    "Do not substitute any paid alternative."
)
_PRESENT_MESSAGE = "GEMINI_API_KEY present: free-tier preflight may proceed."


@dataclass(frozen=True)
class GeminiPreflight:
    credential_present: bool
    model_id: str
    max_requests: int
    timeout: int | float
    max_output_tokens: int
    stop: bool
    message: str


def gemini_preflight(*, env=None) -> GeminiPreflight:
    """Report credential presence and configured quota values (no key material).

    Presence mirrors the planner's own test: a non-empty, strippable ``str``
    under ``GEMINI_API_KEY``. The key value is read for the presence test only
    and is never retained in the result.
    """
    source = os.environ if env is None else env
    key = source.get(_ENV_KEY)
    present = isinstance(key, str) and bool(key.strip())
    return GeminiPreflight(
        credential_present=present,
        model_id=_MODEL_ID,
        max_requests=_DEFAULT_MAX_REQUESTS,
        timeout=_DEFAULT_TIMEOUT,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        stop=not present,
        message=_PRESENT_MESSAGE if present else _ABSENT_MESSAGE,
    )


def format_preflight_line(result: GeminiPreflight) -> str:
    """Build exactly one redacted status line; contains no key material."""
    line = (
        "gemini_preflight: credential_present="
        + ("Y" if result.credential_present else "N")
        + f" model_id={result.model_id}"
        + f" max_requests={result.max_requests}"
        + f" timeout={result.timeout}"
        + f" max_output_tokens={result.max_output_tokens}"
    )
    # Defense in depth: the key is never placed in the line in the first place,
    # but redaction masks any credential-shaped substring before it is printed.
    return redact(line, 500)
