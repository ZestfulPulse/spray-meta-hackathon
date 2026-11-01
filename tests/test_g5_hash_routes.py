"""Offline regression: SPA hash routers must not bypass read-only Preview URL guards."""
import unittest
from types import SimpleNamespace

from spray_schema.g5_batch import TaskSpec
from spray_schema.playwright_preview import PreviewBrowser, _unsafe_navigation
from spray_schema.qa_flow import Requirement


URL = "http://127.0.0.1:8765/"


class HashRouteSafetyTests(unittest.TestCase):
    def test_destructive_fragment_routes_and_actions_are_denied(self):
        # URL fragments do not create a network request, but hash routers can
        # act on them. Their actions need the same denial as normal routes.
        for fragment in (
            "#/deleteUser",
            "#!/publishDraft",
            "#%2FdeleteAccount",
            "#/%2564eleteAccount",
            "#%21%2FdeleteAccount",
            "#/api/sendMessage",
            "#/view?action=deleteAccount",
            "#!/view?task=uploadFile",
            "#?action=approveDraft",
            "#/details?_method=DELETE",
            "#deleteUser",
            "#!publishDraft",
            "#sendMessage",
            "#%64eleteAccount",
            "#details?action=deleteAccount",
            "#!approveDraft",
            "#section?task=uploadFile",
        ):
            with self.subTest(fragment=fragment):
                self.assertTrue(_unsafe_navigation(URL + fragment))

    def test_safe_anchors_and_read_only_hash_routes_remain_allowed(self):
        for fragment in (
            "#section-3",
            "#/reports?tab=details",
            "#!/help",
            "#%2Foverview",
            "#/view?_method=GET",
            "#/view?action=display",
            "#reports",
            "#!help",
            "#section?tab=notes",
        ):
            with self.subTest(fragment=fragment):
                self.assertFalse(_unsafe_navigation(URL + fragment))

    def test_destructive_query_dispatch_aliases_are_denied(self):
        # GET dispatchers and hash routers frequently use cmd, intent,
        # action_type, operation_name or event instead of action. These must
        # not bypass the no-write policy just by renaming the query key.
        unsafe = (
            "reports?cmd=deleteUser",
            "reports?action_type=publishDraft",
            "reports?action-type=approveDraft",
            "reports?operation_name=uploadFile",
            "reports?intent=sendMessage",
            "reports#view?cmd=deleteUser",
            "reports#!view?event=submitForm",
        )
        for route in unsafe:
            with self.subTest(route=route):
                self.assertTrue(_unsafe_navigation(URL + route))
                with self.assertRaises(PermissionError):
                    PreviewBrowser(URL + route, allowed_origin=URL)

        for route in (
            "reports?cmd=viewSummary",
            "reports?action_type=readReport",
            "reports?operation_name=fetchOverview",
            "reports#view?intent=display",
            "reports#view?event=showDetails",
        ):
            with self.subTest(route=route):
                self.assertFalse(_unsafe_navigation(URL + route))

    def test_initial_browser_url_denied_before_launch(self):
        # Construction alone must reject an unsafe route. No Chromium needed.
        with self.assertRaises(PermissionError):
            PreviewBrowser(URL + "#/deleteAccount", allowed_origin=URL)

    def test_button_hash_change_fails_policy_after_click_without_network(self):
        # No Chromium needed: clicking a benign, owner-allowlisted button
        # can mutate location.hash and bypass HTTP request.route entirely.
        class FakeControl:
            def __init__(self, page, destination):
                self.page, self.destination = page, destination

            def inner_text(self):
                return "Open details"

            def is_enabled(self):
                return True

            def evaluate(self, script):
                return "BUTTON" if script == "e => e.tagName" else False

            def click(self, timeout):
                self.page.url = self.destination

        for route in ("#/deleteUser", "#!/publishDraft",
                      "#deleteUser", "#!publishDraft",
                      "#details?action=deleteAccount",
                      "#/view?action=submitForm", "/api/deleteUser"):
            with self.subTest(route=route):
                browser = PreviewBrowser(
                    URL, allowed_origin=URL,
                    allowed_click_labels=("Open details",))
                page = SimpleNamespace(url=URL)
                browser.page = page
                browser._controls = [FakeControl(page, URL + route)]
                browser._observed_labels = ["Open details"]
                with self.assertRaises(PermissionError):
                    browser.click(0)
                self.assertEqual(browser._blocked_policy_actions, 1)

        # A benign SPA fragment remains navigable with the same allowlist.
        browser = PreviewBrowser(URL, allowed_origin=URL,
                                 allowed_click_labels=("Open details",))
        page = SimpleNamespace(url=URL)
        browser.page = page
        browser._controls = [FakeControl(page, URL + "#/reports?tab=overview")]
        browser._observed_labels = ["Open details"]
        browser.click(0)
        self.assertEqual(browser._blocked_policy_actions, 0)

    def test_delayed_unsafe_hash_cannot_pass_observe_verify_or_wait(self):
        for method in ("observe", "verify", "wait"):
            with self.subTest(method=method):
                browser = PreviewBrowser(URL, allowed_origin=URL)
                page = SimpleNamespace(url=URL + "#/deleteUser")
                browser.page = page
                with self.assertRaises(PermissionError):
                    if method == "verify":
                        browser.verify("Completed")
                    elif method == "wait":
                        browser.wait(10)
                    else:
                        browser.observe()
                self.assertEqual(browser._blocked_policy_actions, 1)

        # Synthetic fixture pages remain safe on about:blank without network.
        browser = PreviewBrowser(URL, allowed_origin=URL,
                                 fixture_html="<p>Pending</p>")
        browser.page = SimpleNamespace(url="about:blank")
        browser._assert_safe_page_route()
        self.assertEqual(browser._blocked_policy_actions, 0)

    def test_malformed_or_non_http_link_is_counted_as_policy_denial(self):
        # A ValueError from _origin used to escape click() uncounted. G5
        # would treat that as a recoverable task error, not a batch STOP.
        class FakeLink:
            def __init__(self, href):
                self.href = href
                self.click_count = 0

            def inner_text(self):
                return "Open details"

            def is_enabled(self):
                return True

            def evaluate(self, script):
                if script == "e => e.tagName":
                    return "A"
                if script == "e => e.href":
                    return self.href
                return False

            def get_attribute(self, key):
                return self.href if key == "href" else None

            def click(self, timeout):
                self.click_count += 1

        for href in ("ftp://example.test/file", "ws://example.test/stream",
                     "http://", "https://other.example.test/",
                     URL + "reports?cmd=deleteUser"):
            with self.subTest(href=href):
                browser = PreviewBrowser(
                    URL, allowed_origin=URL,
                    allowed_click_labels=("Open details",))
                link = FakeLink(href)
                browser.page = SimpleNamespace(url=URL)
                browser._controls = [link]
                browser._observed_labels = ["Open details"]
                with self.assertRaises(PermissionError):
                    browser.click(0)
                self.assertEqual(browser._blocked_policy_actions, 1)
                self.assertEqual(link.click_count, 0)

        # A legitimate same-origin read-only destination remains clickable.
        browser = PreviewBrowser(URL, allowed_origin=URL,
                                 allowed_click_labels=("Open details",))
        safe = FakeLink(URL + "reports")
        browser.page = SimpleNamespace(url=URL)
        browser._controls = [safe]
        browser._observed_labels = ["Open details"]
        browser.click(0)
        self.assertEqual(browser._blocked_policy_actions, 0)
        self.assertEqual(safe.click_count, 1)

    def test_manifest_task_preflight_denies_hash_mutation(self):
        for fragment in ("#/view?action=publishDraft",
                         "#deleteUser", "#!publishDraft",
                         "#details?action=approveDraft"):
            with self.subTest(fragment=fragment):
                with self.assertRaises(PermissionError):
                    TaskSpec(
                        task_id="hash_denied",
                        url=URL + fragment,
                        approved_origin=URL,
                        target_text="Completed",
                        requirements=(Requirement("MUST-01", "Completed", "must"),),
                        allowed_click_labels=("Open details",),
                    )


if __name__ == "__main__":
    unittest.main()
