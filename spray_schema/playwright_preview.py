"""Opt-in Playwright adapter restricted to one explicit authorized Preview origin.

Not imported by offline CI unless this adapter is used. Never bypass login.
"""
from __future__ import annotations
import os
import re
import unicodedata
from urllib.parse import urlsplit, unquote, parse_qsl

_UNSAFE_LABEL = re.compile(
    r"\b(delete|remove|erase|wipe|destroy|reset|pay|purchase|buy|checkout|"
    r"charge|subscribe|transfer|send|publish|deploy|merge|authorize|approve|"
    r"save|create|update|edit|modify|archive|restore|confirm|cancel|invite|share|post|"
    r"sign\s*in|log\s*in|logout|sign\s*out|submit|upload)\b|"
    r"삭제|탈퇴|초기화|결제|구매|송금|전송|배포|공개|승인|로그인|로그아웃|제출|업로드",
    re.IGNORECASE)
# Detect destructive aliases in path segments, including camelCase APIs.
# The inline case flag only applies to verbs; [A-Z] is case-sensitive.
_UNSAFE_PATH = re.compile(
    r"(?:^|/)(?i:delete|remove|destroy|wipe|reset|pay|purchase|checkout|"
    r"transfer|erase|buy|charge|subscribe|send|approve|authorize|submit|upload|logout|deploy|publish|merge|unsubscribe|revoke|"
    r"save|create|update|edit|modify|archive|restore|confirm|cancel|invite|share|post)"
    r"(?=$|[/.;_-]|[A-Z]|\d)"
)
_UNSAFE_QUERY_ACTION = re.compile(
    r"^(?i:delete|remove|destroy|wipe|reset|pay|purchase|checkout|"
    r"transfer|erase|buy|charge|subscribe|send|approve|authorize|submit|upload|logout|deploy|publish|merge|unsubscribe|revoke|"
    r"save|create|update|edit|modify|archive|restore|confirm|cancel|invite|share|post)"
    r"(?=$|[-_.]|[A-Z]|\d)"
)
# A GET is not automatically read-only: legacy endpoints may mutate state
# through query keys such as ?deleteUser=7 or ?publish=1. Reject these before
# any same-origin navigation or subresource request is allowed.
_UNSAFE_QUERY_KEY = re.compile(
    r"^(?i:delete|remove|erase|wipe|destroy|reset|pay|purchase|buy|checkout|"
    r"charge|subscribe|transfer|send|publish|deploy|merge|unsubscribe|revoke|"
    r"approve|authorize|submit|upload|save|create|update|edit|modify|"
    r"archive|restore|confirm|cancel|invite|share|post)"
)
_UNSAFE_METHOD_OVERRIDE = frozenset({"post", "put", "patch", "delete"})
# A GET request can be converted into a state-changing operation by server
# middleware or a reverse proxy *after* browser routing has approved its URL.
# No model-approved read-only QA action needs either of these header families.
_UNSAFE_REQUEST_HEADER_NAMES = frozenset({
    "xhttpmethodoverride", "xmethodoverride", "xhttpmethod",
    "xoriginalhttpmethod", "xoriginalmethod",
    "xoriginalurl", "xrewriteurl",
})


def _unsafe_request_headers(headers: dict[str, str]) -> bool:
    """Reject method and URL rewriting headers on ostensibly safe GETs."""
    if type(headers) is not dict:
        return True
    for key, value in headers.items():
        if type(key) is not str or type(value) is not str:
            return True
        normalized = re.sub(r"[-_]", "", key.casefold())
        if normalized in _UNSAFE_REQUEST_HEADER_NAMES:
            return True
    return False

# Legacy GET action dispatchers use more than ?action=... . Normalize common
# alias spelling before checking destructive values, including SPA fragments.
_UNSAFE_ACTION_QUERY_KEYS = frozenset({
    "action", "actiontype", "op", "operation", "operationname",
    "command", "cmd", "method", "do", "task", "intent", "event",
})
# Lowercase concatenation has no word or camelCase boundary. It still forms
# dangerous actions: deleteaccount, publishdraft, transferfunds, saveprofile.
# Match a verb + a known mutable object, preserving innocuous "publisher"
# and "display" names. This is a second conservative layer, not a substitute
# for the HTTP method / exact-origin restrictions.
_UNSAFE_COMPACT_MUTATION = re.compile(
    r"(?i:(?:delete|remove|erase|wipe|destroy|reset|pay|purchase|buy|checkout|"
    r"charge|subscribe|transfer|send|publish|deploy|merge|authorize|approve|"
    r"submit|upload|logout|save|create|update|edit|modify|archive|restore|"
    r"confirm|cancel|invite|share|post)"
    r"(?:account|user|file|draft|post|funds|money|payment|password|token|"
    r"key|data|record|item|cart|order|message|email|role|permission|"
    r"subscription|project|session|settings|profile|plan|changes|change|"
    r"request|2fa|all))(?=$|[^a-z])"
)


def _unsafe_control_label(label: str) -> bool:
    """Deny destructive actions in both separated and camelCase UI labels.

    An owner click allowlist is not a write authorization. For example,
    DeleteAccount or publishDraft must fail before an onclick handler runs.
    Check the original label first to preserve Logout/SignIn recognition.
    """
    # Compatibility glyphs (e.g. fullwidth DELETE) and invisible format
    # characters must not turn a mutating button into an allowed read action.
    label = unicodedata.normalize("NFKC", label)
    label = "".join(ch for ch in label
                    if unicodedata.category(ch) != "Cf")
    if _UNSAFE_LABEL.search(label) or _UNSAFE_COMPACT_MUTATION.search(label):
        return True
    separated = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])|[_-]+", " ", label)
    return bool(_UNSAFE_LABEL.search(separated))



def _decode_bounded(value: str) -> str | None:
    # URL routers may normalize NFKC-compatibility characters; inspect that
    # canonical form *before and after* percent decoding. Invisible format
    # controls in routes are ambiguous, so deny rather than guess semantics.
    for _ in range(4):
        value = unicodedata.normalize("NFKC", value)
        if any(unicodedata.category(ch) == "Cf" for ch in value):
            return None
        decoded = unquote(value, errors="strict")
        if decoded == value:
            break
        value = decoded
    value = unicodedata.normalize("NFKC", value)
    if (any(unicodedata.category(ch) == "Cf" for ch in value)
            or "%" in value or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    return value

def _unsafe_navigation(url: str) -> bool:
    try:
        parts = urlsplit(url)
        routes = [(parts.path, parts.query)]
        # Hash routers may dispatch state-changing actions locally, without
        # a new HTTP navigation. Apply the same path/query denial to #/route,
        # #!/route and #?action=... while preserving ordinary #section links.
        fragment = _decode_bounded(parts.fragment)
        if fragment is None:
            return True
        # Bare and bang hash routers (#deleteUser / #!publishDraft)
        # dispatch client-side without a network request too. Inspect all
        # fragments as potential route path/query while leaving safe anchors
        # such as #section-3 available.
        if fragment.startswith("!"):
            fragment = fragment[1:]
        if fragment:
            hash_route = urlsplit(
                fragment if fragment.startswith("/") else "/" + fragment)
            routes.append((hash_route.path, hash_route.query))
        for raw_path, raw_query in routes:
            path = _decode_bounded(raw_path)
            if (path is None or _UNSAFE_PATH.search(path)
                    or any(_UNSAFE_COMPACT_MUTATION.match(segment)
                           for segment in path.split("/"))):
                return True
            for key, value in parse_qsl(raw_query, keep_blank_values=True):
                key, value = _decode_bounded(key), _decode_bounded(value)
                if key is None or value is None:
                    return True
                # Preserve case for camelCase detection in action values, and
                # inspect query keys as well as values. Casefolding first would
                # turn deleteUser into deleteuser and bypass the alias guard.
                key, value = key.strip(), value.strip()
                if _UNSAFE_QUERY_KEY.match(key):
                    return True
                # GET query parameters can trigger framework method overrides.
                # Recognize common aliases before an apparently safe GET proceeds.
                normalized_key = re.sub(r"[-_]", "", key.casefold())
                if (normalized_key in ("method", "httpmethod", "methodoverride",
                                       "xhttpmethodoverride")
                        and value.casefold() in _UNSAFE_METHOD_OVERRIDE):
                    return True
                if normalized_key in _UNSAFE_ACTION_QUERY_KEYS:
                    if (_UNSAFE_QUERY_ACTION.match(value)
                            or _UNSAFE_COMPACT_MUTATION.search(value)
                            # Treat human-readable operation values exactly like
                            # UI action labels: "delete account" or "save/changes"
                            # must not evade the read-only GET/SPA dispatcher.
                            or _unsafe_control_label(value)):
                        return True
    except (ValueError, UnicodeError):
        return True
    return False

def _route_only(url: str) -> str:
    return urlsplit(url).path or "/"



def _origin(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("invalid preview origin")
    return f"{p.scheme}://{p.netloc}"


class PreviewBrowser:
    def __init__(self, url: str, *, allowed_origin: str, chromium_path: str | None = None,
                 fixture_html: str | None = None,
                 allowed_click_labels: tuple[str, ...] | None = None):
        self.origin = _origin(allowed_origin)
        if _origin(url) != self.origin:
            raise PermissionError("preview origin not allowlisted")
        if self.origin.startswith("http://") and urlsplit(url).hostname not in ("localhost", "127.0.0.1"):
            raise PermissionError("remote previews require HTTPS")
        if fixture_html is not None and urlsplit(url).hostname not in ("localhost", "127.0.0.1"):
            raise PermissionError("synthetic fixture only allowed on loopback")
        if _unsafe_navigation(url):
            raise PermissionError("unsafe initial Preview URL")
        if allowed_click_labels is not None and (
                type(allowed_click_labels) is not tuple or
                any(type(label) is not str or not 1 <= len(label) <= 100
                    for label in allowed_click_labels)):
            raise ValueError("invalid owner click allowlist")
        self.allowed_click_labels = (
            None if fixture_html is not None and allowed_click_labels is None
            else frozenset(label.strip().casefold() for label in (allowed_click_labels or ()))
        )
        self.fixture_html = fixture_html
        self.url = url
        self.chromium_path = chromium_path or os.environ.get("SPRAY_CHROMIUM_PATH")
        self._controls = []
        self._observed_labels = []
        self._observed_enabled = []
        self._blocked_unsafe_requests = 0
        self._blocked_requests = 0
        self._blocked_policy_actions = 0

    def __enter__(self):
        # __exit__ is not invoked when __enter__ fails. A blocked Preview
        # navigation must release Chromium, its context and Playwright before
        # the batch runner handles the policy denial.
        self._pw = None
        self._browser = None
        self._context = None
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            options = {"headless": True}
            if self.chromium_path:
                options["executable_path"] = self.chromium_path
            self._browser = self._pw.chromium.launch(**options)
            self._context = self._browser.new_context(accept_downloads=False, service_workers="block")
            def route_guard(route):
                request_url = route.request.url
                if request_url.startswith(("data:", "about:", "blob:")):
                    return route.continue_()
                unsafe_request = True
                try:
                    unsafe_request = (_unsafe_navigation(request_url)
                                      or _unsafe_request_headers(route.request.headers))
                    permitted = (_origin(request_url) == self.origin and
                                 route.request.method.upper() in ("GET", "HEAD", "OPTIONS") and
                                 not unsafe_request)
                except (ValueError, UnicodeError, TypeError):
                    permitted = False
                if not permitted:
                    self._blocked_requests += 1
                    if unsafe_request:
                        self._blocked_unsafe_requests += 1
                return route.continue_() if permitted else route.abort()
            self._context.route("**/*", route_guard)
            # HTTP request interception does not cover WebSocket frames. Block
            # all WebSocket transports before the page (or its scripts) exists.
            # Never call connect_to_server: read-only QA cannot grant writes.
            self._context.route_web_socket("**/*", self._deny_websocket)
            self.page = self._context.new_page()
            if self.fixture_html is None:
                self.page.goto(self.url, wait_until="domcontentloaded", timeout=10000)
            else:
                # Real Chromium DOM execution without a network request (test fixture only).
                self.page.set_content(self.fixture_html)
            # A script or redirect may have changed the SPA route without an
            # HTTP request. Deny unsafe final URLs before exposing the page.
            self._assert_safe_page_route()
            return self
        except BaseException:
            for resource, method in ((self._context, "close"),
                                     (self._browser, "close"),
                                     (self._pw, "stop")):
                if resource is not None:
                    try:
                        getattr(resource, method)()
                    except Exception:
                        pass
            raise

    def _deny_websocket(self, websocket) -> None:
        """Deny upstream WebSocket traffic and fail the entire G5 task.

        Playwright's routed socket never reaches the server unless
        connect_to_server() is called. A synchronous close() inside this
        callback can deadlock in Playwright 1.57. The page may see a mock
        OPEN event, so only the blocked-request counters are authoritative.
        """
        self._blocked_requests += 1
        self._blocked_unsafe_requests += 1
        websocket.on_message(lambda _message: None)

    def __exit__(self, *_):
        self._context.close()
        self._browser.close()
        self._pw.stop()

    def observe(self) -> dict:
        self._assert_safe_page_route()
        # Locator.nth(i) resolves dynamically. If the DOM reorders after
        # observation, clicking the saved locator can hit a DIFFERENT button.
        # Pin the actual observed element instead, failing safely if detached.
        for handle in self._controls:
            try:
                handle.dispose()
            except Exception:
                pass
        self._controls = []
        self._observed_labels = []
        self._observed_enabled = []
        controls = []
        # Scan visible controls in DOM order, but do not let unrelated
        # controls crowd out owner-approved read-only actions. Keep the
        # original fixture-mode cap and bound Preview scanning at 256.
        scan_limit = 256 if self.allowed_click_labels is not None else 25
        for locator in self.page.locator("button:visible, a:visible").all()[:scan_limit]:
            if not locator.is_visible():
                continue
            handle = locator.element_handle()
            if handle is None:
                continue
            kind = "button" if handle.evaluate("e => e.tagName") == "BUTTON" else "link"
            # ElementHandle.inner_text() has no timeout kwarg (unlike Locator.inner_text).
            label = handle.inner_text().strip()[:100]
            # The planner cannot execute anything outside the owner's click
            # allowlist, so do not spend its 15-control observation budget on
            # ineligible or explicitly destructive controls. G4 synthetic
            # fixtures without an owner allowlist retain legacy observation.
            if self.allowed_click_labels is not None and (
                    label.casefold() not in self.allowed_click_labels
                    or _unsafe_control_label(label)):
                handle.dispose()
                continue
            enabled = handle.is_enabled()
            # Disabled approved controls are not executable tasks. Do not
            # spend the model's fifteen visible-control slots on them.
            # G4 fixture observations without a click allowlist still
            # include disabled controls for backwards-compatible QA.
            if self.allowed_click_labels is not None and not enabled:
                handle.dispose()
                continue
            self._controls.append(handle)
            self._observed_labels.append(label)
            # Record enabled-at-observation state in lockstep so click() can
            # tell a mid-click stale-control race (observed enabled, now
            # disabled) from a never-executable control.
            self._observed_enabled.append(enabled)
            controls.append({"kind": kind, "label": label,
                             "enabled": enabled})
            if len(controls) >= 15:
                break
        return {"url": _route_only(self.page.url), "title": self.page.title()[:100],
                "visible_text": self.page.locator("body").inner_text()[:1000],
                "controls": controls}

    def _deny_policy(self, reason: str) -> None:
        self._blocked_policy_actions += 1
        raise PermissionError(reason)

    def _assert_safe_page_route(self) -> None:
        """Fail closed when SPA JS/history/hash changes the URL without HTTP.

        HTTP routing cannot see fragment-only or pushState navigation. Count
        an unsafe current page route as a policy denial, including after a
        click or during verification. about:blank is permitted only for the
        explicitly loopback-limited synthetic HTML fixture.
        """
        current_url = self.page.url
        if self.fixture_html is not None and current_url == "about:blank":
            return
        try:
            allowed = (_origin(current_url) == self.origin
                       and not _unsafe_navigation(current_url))
        except (ValueError, UnicodeError):
            allowed = False
        if not allowed:
            self._deny_policy("unsafe client-side Preview navigation")

    def click(self, index: int) -> None:
        self._assert_safe_page_route()
        if type(index) is not int or not 0 <= index < len(self._controls):
            self._deny_policy("unobserved control")
        locator = self._controls[index]
        label = locator.inner_text().strip()
        if label != self._observed_labels[index]:
            self._deny_policy("observed control changed before click")
        if _unsafe_control_label(label):
            self._deny_policy("unsafe action label")
        if self.allowed_click_labels is not None and label.casefold() not in self.allowed_click_labels:
            self._deny_policy("click not approved by owner")
        if not locator.is_enabled():
            # A control that was observed as ENABLED but is disabled by the
            # time we click it is a mid-click stale-control race (the
            # documented G5Recovery fault in docs/G3_GEMINI_LOCAL_PROOF.md,
            # armed by run_g3_gemini_chromium.py::TransitionPreview). That is
            # a recoverable transient action failure: raise a plain
            # PermissionError so run_loop records a failed-click fingerprint
            # and replans to the safe alternate control, WITHOUT bumping the
            # policy-denial counter that would halt the whole G5 batch.
            #
            # A control that was NOT observed as enabled (no observation
            # record, or observed disabled) can only reach click() by a
            # planner ignoring the observation, so it stays a policy denial.
            # No guard is weakened: observe() already filters disabled
            # controls out of observations, and run_loop (agent_loop.py)
            # rejects a planner that picks an observed-disabled control, so
            # both cover the "model chose a disabled control" threat.
            if index < len(self._observed_enabled) and self._observed_enabled[index]:
                raise PermissionError("disabled control")
            self._deny_policy("disabled control")
        if locator.evaluate("""e => {
            const a = e.closest('a');
            if (a && (a.hasAttribute('download') || a.target === '_blank')) return true;
            const b = e.closest('button');
            return !!(b && b.form);
        }"""):
            self._deny_policy("form submit, download or new tab denied")
        if locator.evaluate("e => e.tagName") == "A":
            href = locator.get_attribute("href") or ""
            if href.startswith(("javascript:", "data:", "blob:", "mailto:", "tel:")):
                self._deny_policy("non-HTTP link denied")
            resolved = locator.evaluate("e => e.href")
            # Invalid protocols and malformed hrefs are policy denials, not
            # generic runner errors. Otherwise G5 may continue to the next
            # task after encountering a rejected link instead of halting.
            try:
                unsafe_link = (_origin(resolved) != self.origin
                               or _unsafe_navigation(resolved))
            except (TypeError, ValueError, UnicodeError):
                unsafe_link = True
            if unsafe_link:
                self._deny_policy("unsafe link denied")
        locator.click(timeout=3000)
        # Client-side hash routing or pushState has no network interception.
        # Never report successful completion on a forbidden final page URL.
        self._assert_safe_page_route()

    def verify(self, target_text: str) -> bool:
        self._assert_safe_page_route()
        verified = self.page.get_by_text(target_text, exact=True).first.is_visible()
        self._assert_safe_page_route()
        return verified

    def wait(self, milliseconds: int) -> None:
        self._assert_safe_page_route()
        self.page.wait_for_timeout(milliseconds)
        self._assert_safe_page_route()
