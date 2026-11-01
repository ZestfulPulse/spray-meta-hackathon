"""H9 regression: no remote writes; all browser cases use synthetic loopback DOM."""
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from spray_schema.playwright_preview import (
    PreviewBrowser, _unsafe_navigation, _unsafe_control_label,
    _unsafe_request_headers,
)

URL = "http://127.0.0.1:8765/"


class NavigationGuardTests(unittest.TestCase):
    def test_encoded_delete_paths_fail_closed(self):
        for suffix in ("/api/%64elete", "/api/%2564elete",
                       "/api/delete;confirm", "/api/%2Fdelete",
                       "/view?action=%2564elete", "/view?op=%2572eset",
                       "/view?command=logout%00"):
            with self.subTest(suffix=suffix):
                self.assertTrue(_unsafe_navigation(URL + suffix))

    def test_query_key_and_camel_case_actions_are_denied(self):
        # Even GET URLs can mutate server state when query keys encode an
        # operation. Double-encoded keys and camelCase values must not bypass
        # the same read-only route guard.
        for suffix in (
            "/view?delete=42",
            "/view?deleteUser=42",
            "/view?%2564eleteUser=42",
            "/view?remove_item=42",
            "/view?publishDraft=1",
            "/view?send=1",
            "/view?action=deleteAccount",
            "/view?task=publishDraft",
            "/view?method=POST",
        ):
            with self.subTest(suffix=suffix):
                self.assertTrue(_unsafe_navigation(URL + suffix))

    def test_get_method_override_aliases_cannot_mutate_preview(self):
        # Many web frameworks accept method override keys on a GET URL.
        # A read-only transport method must not authorize DELETE/POST/etc.
        for suffix in (
            "/view?_method=DELETE",
            "/view?%255fmethod=PATCH",
            "/view?http_method=put",
            "/view?http-method=POST",
            "/view?methodOverride=DELETE",
            "/view?X-HTTP-Method-Override=POST",
            "/view?x_http_method_override=PATCH",
        ):
            with self.subTest(mutation=suffix):
                self.assertTrue(_unsafe_navigation(URL + suffix))
        for suffix in ("/view?_method=GET", "/view?method=HEAD",
                       "/view?http_method=GET", "/view?tab=details"):
            with self.subTest(read_only=suffix):
                self.assertFalse(_unsafe_navigation(URL + suffix))


    def test_method_and_url_override_headers_fail_closed(self):
        # A GET with X-HTTP-Method-Override: DELETE can mutate a legacy API.
        # Reverse proxies may also honor X-Original-URL/X-Rewrite-URL.
        for key in ("X-HTTP-Method-Override", "x_http_method_override",
                    "X-Method-Override", "X-HTTP-Method",
                    "X-Original-HTTP-Method", "X-Original-URL",
                    "X-Rewrite-URL"):
            with self.subTest(header=key):
                self.assertTrue(_unsafe_request_headers({key: "DELETE"}))
        for headers in ({}, {"accept": "text/html"},
                        {"X-Request-ID": "safe-canary"}):
            with self.subTest(headers=headers):
                self.assertFalse(_unsafe_request_headers(headers))
        self.assertTrue(_unsafe_request_headers(None))
        self.assertTrue(_unsafe_request_headers({"X-HTTP-Method-Override": 3}))

    def test_mutating_action_aliases_blocked_in_path_and_action_values(self):
        # All known state-changing aliases must be denied whether encoded as
        # the URL path or the value of a generic action/task query parameter.
        # A harmless control label does not make an unsafe destination safe.
        aliases = ("erase", "buy", "charge", "subscribe", "send",
                   "approve", "authorize", "submit", "upload")
        for verb in aliases:
            for suffix in (f"/api/{verb}Draft",
                           f"/view?action={verb}Draft",
                           f"/view?task={verb}Draft"):
                with self.subTest(url=suffix):
                    self.assertTrue(_unsafe_navigation(URL + suffix))
        for suffix in ("/api/approvalStatus", "/api/sender",
                       "/view?action=display", "/view?tab=details"):
            with self.subTest(read_only=suffix):
                self.assertFalse(_unsafe_navigation(URL + suffix))

    def test_numeric_suffixed_mutation_actions_fail_closed(self):
        # Numeric suffixes are common in 2FA/account APIs. They must not
        # hide a destructive verb from GET, SPA or clicked-label policy.
        for suffix in (
            "/api/delete2FA", "/api/transfer2Account",
            "/view?action=publish2026", "/view?task=upload9Files",
            "#/delete2FA", "#/view?operation=transfer2Account",
        ):
            with self.subTest(unsafe_url=suffix):
                self.assertTrue(_unsafe_navigation(URL + suffix))
        for label in ("Delete2FA", "DELETE2FA", "Transfer2Account",
                      "TRANSFER2ACCOUNT", "Publish2026", "Upload9Files"):
            with self.subTest(unsafe_label=label):
                self.assertTrue(_unsafe_control_label(label))
        for suffix in ("/api/Publisher2026", "/view?action=display2"):
            with self.subTest(safe_url=suffix):
                self.assertFalse(_unsafe_navigation(URL + suffix))
        for label in ("Publisher2026 profile", "Open 2FA details"):
            with self.subTest(safe_label=label):
                self.assertFalse(_unsafe_control_label(label))

    def test_mutating_preview_verbs_and_lowercase_compounds_fail_closed(self):
        # GET dispatchers and read-only-looking labels can still mutate state.
        # A lowercase concatenation must not bypass word/camelCase boundaries.
        for suffix in (
            "/api/saveProfile", "/api/saveprofile", "/api/deleteaccount",
            "/api/publishdraft", "/api/editprofile",
            "/view?op=transferfunds", "/view?cmd=savechanges",
            "/view?updateSettings=1", "#/api/editprofile",
        ):
            with self.subTest(unsafe_url=suffix):
                self.assertTrue(_unsafe_navigation(URL + suffix))
        for label in ("Save changes", "saveprofile", "CreateProject",
                      "Edit profile", "Archive9Logs", "publishdraft",
                      "transferfunds", "Confirm order", "Share file"):
            with self.subTest(unsafe_label=label):
                self.assertTrue(_unsafe_control_label(label))
        for suffix in ("/api/publisherprofile", "/view?tab=posts",
                       "/view?action=display", "/assets/updates.css"):
            with self.subTest(read_only_url=suffix):
                self.assertFalse(_unsafe_navigation(URL + suffix))
        for label in ("Publisher profile", "Open details",
                      "View updates", "Close panel"):
            with self.subTest(read_only_label=label):
                self.assertFalse(_unsafe_control_label(label))

    def test_spaced_or_delimited_dispatcher_action_must_fail_closed(self):
        # Legacy GET and SPA dispatchers can act on human-readable commands.
        # A read-only Preview URL cannot authorize "delete account" merely
        # because the verb is followed by whitespace or a slash.
        for suffix in (
            "/view?action=delete+account",
            "/view?action=delete%20account",
            "/view?command=Save%20changes",
            "/view?task=Publish%2FDraft",
            "/view?intent=Transfer%20Funds",
            "/view?op=prefix+delete",
            "/view?operation=Archive%20logs",
            "/view?do=create%20project",
            "/view?action=%2564elete%2520account",
            "#/view?cmd=Delete+User",
            "#/view?event=Publish%20Draft",
        ):
            with self.subTest(unsafe_dispatcher=suffix):
                self.assertTrue(_unsafe_navigation(URL + suffix))
        for suffix in (
            "/view?action=display",
            "/view?action=publisher",
            "/view?task=open+details",
            "/view?cmd=view%20profile",
            "#/view?intent=open%20details",
        ):
            with self.subTest(read_only_dispatcher=suffix):
                self.assertFalse(_unsafe_navigation(URL + suffix))

    def test_websocket_denial_counts_as_policy_block(self):
        # The callback must not block on sync close(), nor ever connect
        # upstream. A mock OPEN event cannot override the denial counters.
        class FakeWebSocketRoute:
            def __init__(self):
                self.sink = None

            def on_message(self, handler):
                self.sink = handler

            def close(self):
                raise AssertionError("sync route callback must not call close")

            def connect_to_server(self):
                raise AssertionError("read-only QA must not open a socket")

        browser = PreviewBrowser(URL, allowed_origin=URL,
                                 fixture_html="<p>Pending</p>")
        socket_route = FakeWebSocketRoute()
        browser._deny_websocket(socket_route)
        self.assertIsNotNone(socket_route.sink)
        socket_route.sink("SECRET_CANARY")
        self.assertEqual(browser._blocked_requests, 1)
        self.assertEqual(browser._blocked_unsafe_requests, 1)

    def test_destructive_query_key_denied_before_browser_launch(self):
        with self.assertRaises(PermissionError):
            PreviewBrowser(URL + "view?deleteUser=42", allowed_origin=URL)

    def test_unicode_compatibility_mutating_routes_are_denied(self):
        # Policy must inspect NFKC-equivalent text and reject invisible
        # format controls, including after URL percent-decoding.
        unsafe = (
            URL + "api/ｄｅｌｅｔｅAccount",
            URL + "api/De%E2%80%8BleteAccount",
            URL + "view?ＤｅｌｅｔｅUser=1",
            URL + "view?cmd=Ｓａｖｅ+changes",
            URL + "view?cmd=％64eleteAccount",
            URL + "view?cmd=De%E2%80%8BleteAccount",
            URL + "#/api/ＤｅｌｅｔｅAccount",
        )
        for url in unsafe:
            with self.subTest(url=url):
                self.assertTrue(_unsafe_navigation(url))
        for url in (URL + "view?action=display",
                    URL + "view?cmd=Publisher+profile"):
            with self.subTest(safe_url=url):
                self.assertFalse(_unsafe_navigation(url))

    def test_unicode_compatibility_mutating_labels_are_denied(self):
        for label in ("ＤｅｌｅｔｅAccount", "Ｓａｖｅ changes",
                      "De\u200bleteAccount", "Sa\u2060ve changes"):
            with self.subTest(label=label):
                self.assertTrue(_unsafe_control_label(label))
        for label in ("Publisher profile", "Open details", "Display 2026"):
            with self.subTest(safe_label=label):
                self.assertFalse(_unsafe_control_label(label))

    def test_safe_paths_are_allowed(self):
        for suffix in ("/dashboard", "/assets/my%20file.css", "/view?tab=details",
                       "/view?method=GET", "/view?action=display"):
            with self.subTest(suffix=suffix):
                self.assertFalse(_unsafe_navigation(URL + suffix))

    def test_destructive_camel_case_button_labels_are_denied(self):
        # Exact allowlist matching cannot turn a destructive control into
        # a read-only action. Normalized aliases must be blocked as well.
        for label in ("DeleteAccount", "publishDraft", "RemoveUser",
                      "ResetPassword", "ApproveRequest", "UploadFile",
                      "doDeleteAccount", "delete_user", "Publish-Draft",
                      "LogOut", "SignIn"):
            with self.subTest(label=label):
                self.assertTrue(_unsafe_control_label(label))
        for label in ("View details", "Publisher profile", "Open details"):
            with self.subTest(safe_label=label):
                self.assertFalse(_unsafe_control_label(label))

    def test_initial_destructive_url_rejected(self):
        with self.assertRaises(PermissionError):
            PreviewBrowser(URL + "api/%2564elete", allowed_origin=URL)

    def test_disabled_control_is_a_batch_policy_denial(self):
        # G5 cannot replan after a control becomes disabled between
        # observation and click. It must increment the denial counter so
        # run_batch safely halts, rather than treating it as a transient
        # click failure and spending more model requests.
        class DisabledControl:
            def inner_text(self):
                return "Open details"

            def is_enabled(self):
                return False

            def evaluate(self, *_):
                raise AssertionError("disabled control must not be dispatched")

        class FakePage:
            url = "about:blank"

        browser = PreviewBrowser(
            URL, allowed_origin=URL, fixture_html="<p>Pending</p>",
            allowed_click_labels=("Open details",),
        )
        browser.page = FakePage()
        browser._controls = [DisabledControl()]
        browser._observed_labels = ["Open details"]
        with self.assertRaisesRegex(PermissionError, "disabled control"):
            browser.click(0)
        self.assertEqual(browser._blocked_policy_actions, 1)

    def test_disabled_allowed_controls_do_not_hide_enabled_target(self):
        # Fifteen disabled owner-approved controls used to occupy all model
        # observation slots and conceal the next actionable safe control.
        # Keep disabled controls visible to G4 fixtures, but never offer them
        # as G5 clickable actions.
        class FakeHandle:
            def __init__(self, enabled):
                self.enabled = enabled
                self.disposed = False

            def evaluate(self, *_):
                return "BUTTON"

            def inner_text(self):
                return "Open details"

            def is_enabled(self):
                return self.enabled

            def dispose(self):
                self.disposed = True

        class FakeLocator:
            def __init__(self, handle):
                self.handle = handle

            def is_visible(self):
                return True

            def element_handle(self):
                return self.handle

        class FakeSelection:
            def __init__(self, handles):
                self.handles = handles

            def all(self):
                return [FakeLocator(handle) for handle in self.handles]

            def inner_text(self):
                return "Pending"

        class FakePage:
            url = "about:blank"

            def __init__(self, handles):
                self.handles = handles

            def locator(self, selector):
                if selector == "button:visible, a:visible":
                    return FakeSelection(self.handles)
                if selector == "body":
                    return FakeSelection(())
                raise AssertionError("unexpected DOM query")

            def title(self):
                return "Safe QA"

        handles = [FakeHandle(False) for _ in range(15)] + [FakeHandle(True)]
        browser = PreviewBrowser(
            URL, allowed_origin=URL, fixture_html="<p>Pending</p>",
            allowed_click_labels=("Open details",),
        )
        browser.page = FakePage(handles)
        observation = browser.observe()
        self.assertEqual(len(observation["controls"]), 1)
        self.assertTrue(observation["controls"][0]["enabled"])
        self.assertIs(browser._controls[0], handles[-1])
        self.assertTrue(all(handle.disposed for handle in handles[:-1]))

    def test_remote_preview_has_empty_click_allowlist_by_default(self):
        browser = PreviewBrowser("https://preview.example.test/",
                                 allowed_origin="https://preview.example.test/")
        self.assertEqual(browser.allowed_click_labels, frozenset())


@unittest.skipUnless(os.getenv("RUN_BROWSER_E2E") == "1", "opt-in Chromium")
class ChromiumGuardTests(unittest.TestCase):
    def test_encoded_delete_fetch_is_blocked(self):
        html = ('<html><head><base href="http://127.0.0.1:8765/"></head><body>'
                '<button onclick="fetch(\'/api/%2564elete\').catch(() => {})">'
                'View details</button><p id="s">Pending</p></body></html>')
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                            allowed_click_labels=("View details",)) as browser:
            browser.observe()
            browser.click(0)
            browser.page.wait_for_timeout(200)
            self.assertEqual(browser._blocked_unsafe_requests, 1)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Pending")

    def test_same_origin_get_with_destructive_query_is_blocked(self):
        html = ('<html><head><base href="http://127.0.0.1:8765/"></head><body>'
                '<button onclick="fetch(\'/view?deleteUser=42\').catch(() => {})">'
                'View details</button><p id="s">Pending</p></body></html>')
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                            allowed_click_labels=("View details",)) as browser:
            browser.observe()
            browser.click(0)
            browser.page.wait_for_timeout(200)
            self.assertEqual(browser._blocked_unsafe_requests, 1)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Pending")

    def test_spaced_destructive_dispatcher_is_blocked_by_real_chromium(self):
        # Uses local fixture DOM only, never navigates to an external site.
        html = (
            '<html><head><base href="http://127.0.0.1:8765/"></head><body>'
            '<button onclick="fetch(\'/view?action=delete%20account\').catch(() => {})">'
            'View details</button><p id="s">Pending</p></body></html>'
        )
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                            allowed_click_labels=("View details",)) as browser:
            browser.observe()
            browser.click(0)
            browser.page.wait_for_timeout(200)
            self.assertEqual(browser._blocked_unsafe_requests, 1)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Pending")

    def test_allowlisted_camel_case_delete_button_is_denied(self):
        # Real Chromium: the exact label is owner-allowlisted, but even
        # then a destructive onclick must be denied before DOM mutation.
        html = """<html><body><button onclick="document.getElementById('s').textContent='Deleted'">DeleteAccount</button><p id="s">Pending</p></body></html>"""
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                            allowed_click_labels=("DeleteAccount",)) as browser:
            browser.observe()
            with self.assertRaises(PermissionError):
                browser.click(0)
            self.assertEqual(browser._blocked_policy_actions, 1)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Pending")

    def test_allowlisted_button_cannot_open_websocket_transport(self):
        # Playwright's mock WebSocket may appear OPEN to page JavaScript.
        # Only an actual TCP handshake would mean upstream access. A policy
        # denial must override even a client-side "Connected" DOM state.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(0.25)
            port = listener.getsockname()[1]
            html = (
                '<html><body><button onclick="window.ws = new WebSocket('
                f"'ws://127.0.0.1:{port}/api/mutate');"
                "window.ws.onopen = () => {"
                "window.ws.send('SECRET_CANARY');"
                "document.querySelector('#s').textContent='Connected'"
                '}">Open details</button><p id="s">Pending</p></body></html>'
            )
            with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                                allowed_click_labels=("Open details",)) as browser:
                browser.observe()
                browser.click(0)
                browser.page.wait_for_function(
                    "window.ws && window.ws.readyState === 1", timeout=3000)
                self.assertEqual(browser._blocked_requests, 1)
                self.assertEqual(browser._blocked_unsafe_requests, 1)
                self.assertEqual(browser.page.locator("#s").inner_text(), "Connected")
                with self.assertRaises(socket.timeout):
                    listener.accept()


    def test_get_header_override_never_reaches_approved_loopback_server(self):
        # The document is served on the SAME loopback origin as the fetch.
        # This makes the custom request header observable without a CORS
        # preflight obscuring whether Chromium sent the blocked request.
        served = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                served.append((self.path, self.headers.get("X-HTTP-Method-Override")))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write((
                    '<html><body><button type="button" onclick="'
                    "fetch('/view?tab=details',"
                    "{headers:{'X-HTTP-Method-Override':'DELETE'}}).catch(()=>{})"
                    '">Open details</button><p id="s">Pending</p></body></html>'
                ).encode("utf-8"))

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        loopback = f"http://127.0.0.1:{server.server_port}/"
        try:
            with PreviewBrowser(loopback, allowed_origin=loopback,
                                allowed_click_labels=("Open details",)) as browser:
                browser.observe()
                browser.click(0)
                browser.page.wait_for_timeout(250)
                self.assertEqual(browser._blocked_requests, 1)
                self.assertEqual(browser._blocked_unsafe_requests, 1)
                self.assertEqual(browser.page.locator("#s").inner_text(), "Pending")
            # Chrome may fetch /favicon.ico independently of the test.
            # Assert only the security property, not a brittle request count.
            self.assertTrue(any(path == "/" for path, _ in served))
            self.assertFalse(any(path.startswith("/view?") for path, _ in served))
            self.assertTrue(all(override is None for _, override in served))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_form_submit_denied_before_mutation(self):
        html = ('<html><body><form onsubmit="document.querySelector(\'#s\').'
                'textContent=\'Submitted\';return false"><button>Continue</button>'
                '</form><p id="s">Pending</p></body></html>')
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html) as browser:
            browser.observe()
            with self.assertRaises(PermissionError):
                browser.click(0)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Pending")

    def test_hidden_controls_do_not_hide_later_approved_button(self):
        # Modern SPAs may render dozens of hidden controls before a visible
        # action. The 25-control cap must apply to VISIBLE controls only.
        # Old selector "button, a"[:25] would return an empty observation.
        html = (
            "<html><body>"
            + '<button hidden type="button">Hidden</button>' * 40
            + '<button id="target" type="button" onclick="document.getElementById(&quot;s&quot;).textContent=&quot;Completed&quot;">Open details</button><p id="s">Pending</p></body></html>'
        )
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                            allowed_click_labels=("Open details",)) as browser:
            observed = browser.observe()
            self.assertEqual([item["label"] for item in observed["controls"]],
                             ["Open details"])
            browser.click(0)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Completed")

    def test_unapproved_visible_controls_cannot_crowd_out_owner_allowed_target(self):
        # A real Preview may show many harmless but unapproved controls before
        # the one low-risk control the owner authorized. The scan cap must
        # apply to candidates, not make the 15 reported controls unusable.
        html = (
            "<html><body>"
            + "<button type='button'>Open unrelated</button>" * 35
            + "<button id='target' type='button' "
              "onclick=\"document.getElementById('s').textContent='Completed'\">"
              "Open details</button>"
            + "<p id='s'>Pending</p></body></html>"
        )
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                            allowed_click_labels=("Open details",)) as browser:
            observed = browser.observe()
            self.assertEqual([c["label"] for c in observed["controls"]],
                             ["Open details"])
            browser.click(0)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Completed")

    def test_safe_click_still_completes(self):
        html = ('<html><body><button onclick="document.querySelector(\'#s\').'
                'textContent=\'Completed\'">Repair</button><p id="s">Pending</p></body></html>')
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                            allowed_click_labels=("Repair",)) as browser:
            browser.observe()
            browser.click(0)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Completed")
