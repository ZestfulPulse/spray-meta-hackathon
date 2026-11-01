"""Minimum-necessary observation for the SPRAY model boundary; no raw DOM or credentials."""
from __future__ import annotations
import re
from urllib.parse import urlsplit

# Quoted values may contain spaces or escaped delimiters. Mask the whole
# value rather than leaving its suffix in the model prompt.
_SECRET = re.compile(
    r"""(?i)\b(access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|csrf[_-]?token|xsrf[_-]?token|session[_-]?token|api[_-]?key|password|passwd|client[_-]?secret|session[_-]?id|authorization|secret)\b['\"]?"""
    r"""\s*[:=]\s*(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s'"<>&]+)"""
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\b")
_URL = re.compile(r"https?://[^\s<>\"']+|(?<![\w:/])//[^\s<>\"']+", re.I)
_AWS = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
# HTTP authorization and cookie header values can contain multiple tokens,
# commas or semicolons. Mask the entire line, not just the first word.
_SENSITIVE_HEADER = re.compile(
    r"(?i)\b(x-auth-token|proxy-authorization|authorization|set-cookie|cookie)\s*:\s*[^\r\n]*"
)

def redact(value: object, limit: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    def strip_url(match):
        # Page text and control labels are untrusted. URL userinfo and paths
        # may embed credentials or one-time links; retaining scheme/netloc/path
        # leaks those values to the model even when queries are stripped.
        # The page route is conveyed separately by sanitize_observation.
        return "[REDACTED_URL]"
    value = _URL.sub(strip_url, value)
    value = _SENSITIVE_HEADER.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _SECRET.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    value = _JWT.sub("[REDACTED_JWT]", value)
    value = _AWS.sub("[REDACTED_KEY]", value)
    return value[:limit]

# Only generic, public route words may pass to the model. Arbitrary path
# components can be customer identifiers, invitation codes, or reset tokens,
# including short all-lowercase values that entropy heuristics would miss.
_SAFE_ROUTE_PARTS = frozenset({
    "app", "dashboard", "details", "help", "home", "overview", "page",
    "pages", "qa", "report", "reports", "result", "results", "settings",
    "status", "summary", "task", "tasks", "test", "tests", "view",
})


def _safe_route(raw_url: object) -> str:
    if not isinstance(raw_url, str):
        return ""
    try:
        route = urlsplit(raw_url).path
    except ValueError:
        return ""
    # Never decode percent-encoded path data: even a short unknown segment is
    # masked. Preserve slashes and a tiny generic route vocabulary for context.
    return "/".join(
        segment if not segment or segment in _SAFE_ROUTE_PARTS
        else "[REDACTED_PATH]" for segment in route.split("/")
    )


def sanitize_observation(observation: dict) -> dict:
    if type(observation) is not dict:
        raise ValueError("invalid browser observation")
    clean = {"url": redact(_safe_route(observation.get("url", "")), 250)}
    for key, limit in (("title", 100), ("visible_text", 1000)):
        if key in observation:
            clean[key] = redact(observation[key], limit)
    controls = observation.get("controls", [])
    if type(controls) is not list:
        raise ValueError("invalid controls")
    clean["controls"] = []
    for item in controls[:15]:
        if type(item) is not dict or item.get("kind") not in ("button", "link"):
            raise ValueError("invalid control")
        clean["controls"].append({"kind": item["kind"],
            "label": redact(item.get("label", ""), 100),
            "enabled": item.get("enabled") is True})
    # Only bounded, structured recon metadata reaches the model. Never pass
    # raw DOM, screenshot, form values, requirement text, or browser headers.
    qa = observation.get("qa")
    if type(qa) is dict:
        states = qa.get("states", {})
        components = qa.get("components", {})
        missing = qa.get("missing_in_priority_order", [])
        coverage = qa.get("weighted_coverage_percent")
        clean["qa"] = {
            "flow_id": qa.get("flow_id", "") if
                type(qa.get("flow_id")) is str and
                re.fullmatch(r"[A-Za-z0-9_-]{1,32}", qa["flow_id"]) else "",
            "states": {
                key: value if type(value) is int and 0 <= value <= 100 else None
                for key in ("forms", "inputs", "alerts", "busy", "disabled")
                for value in ((states.get(key),) if type(states) is dict else (None,))
            },
            "components": {
                key: value if type(value) is int and 0 <= value <= 100 else 0
                for key in ("button", "link")
                for value in ((components.get(key),) if type(components) is dict else (None,))
            },
            "missing_in_priority_order": [
                case_id for case_id in missing[:12]
                if type(case_id) is str and
                re.fullmatch(r"[A-Za-z0-9_-]{1,32}", case_id)
            ] if type(missing) is list else [],
            "weighted_coverage_percent": coverage
                if type(coverage) in (int, float) and 0 <= coverage <= 100 else None,
        }
    return clean
