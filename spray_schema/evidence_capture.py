"""Privacy-first screenshot evidence for isolated SPRAY browser fixtures.

Default screenshots preserve layout geometry but remove all text, media and
CSS background images. No remote Preview capture or credential export.
"""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
from .model_observation import redact

_LAYOUT_ONLY_CSS = """
html body, html body * {
  color: transparent !important;
  -webkit-text-fill-color: transparent !important;
  text-shadow: none !important;
  background-image: none !important;
  list-style-image: none !important;
  caret-color: transparent !important;
}
html body *::before, html body *::after {
  content: none !important;
  background-image: none !important;
}
html body img, html body svg, html body canvas, html body video,
html body iframe, html body object, html body embed, html body picture {
  visibility: hidden !important;
}
"""

def capture_fixture_evidence(browser, evidence_dir, *, target_verified: bool) -> dict:
    """Capture real Chromium pixels, without sending raw page content to disk.

    Synthetic loopback fixtures only. Remote pages are intentionally excluded
    until an owner-approved evidence retention/redaction policy is available.
    """
    if getattr(browser, "fixture_html", None) is None:
        raise PermissionError("remote screenshots not authorized")
    if type(target_verified) is not bool:
        raise ValueError("verification must be boolean")
    page = browser.page
    png = page.screenshot(full_page=True, style=_LAYOUT_ONLY_CSS)
    record = {
        "schema": "spray.layout_evidence.v1",
        "screenshot_policy": "layout_only_text_media_masked_v1",
        "browser_fixture": True,
        "target_verified": target_verified,
        "title": redact(page.title(), 100),
        "screenshot_sha256": sha256(png).hexdigest(),
    }
    out = Path(evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "layout.png").write_bytes(png)
    (out / "evidence.json").write_text(
        json.dumps(record, sort_keys=True, indent=2), encoding="utf-8")
    return record
