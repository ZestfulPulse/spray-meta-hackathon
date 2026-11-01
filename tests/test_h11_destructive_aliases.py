"""H11 regression for hidden destructive GET operations, no external network."""
import os
import unittest
from spray_schema.playwright_preview import PreviewBrowser, _unsafe_navigation

URL = "http://127.0.0.1:8765/"


class UnsafeAliasTests(unittest.TestCase):
    def test_destructive_path_aliases(self):
        for path in ("/api/deleteUser", "/api/reset-password", "/api/publish_post",
                     "/api/%2564eleteAccount", "/api/revokeToken", "/api/remove;confirm"):
            with self.subTest(path=path):
                self.assertTrue(_unsafe_navigation(URL + path))

    def test_destructive_query_aliases_and_method_overrides(self):
        for query in ("action=deleteAll", "task=reset-password", "do=publish_post",
                      "operation=%2564eleteAccount", "method=POST", "method=patch",
                      "method=DELETE"):
            with self.subTest(query=query):
                self.assertTrue(_unsafe_navigation(URL + "view?" + query))

    def test_benign_read_only_routes_preserved(self):
        for path in ("/api/paymentHistory", "/assets/payload.js", "/dashboard",
                     "/view?method=GET", "/view?action=details", "/view?task=read"):
            with self.subTest(path=path):
                self.assertFalse(_unsafe_navigation(URL + path))

    def test_initial_alias_rejected(self):
        with self.assertRaises(PermissionError):
            PreviewBrowser(URL + "api/deleteUser", allowed_origin=URL)


@unittest.skipUnless(os.getenv("RUN_BROWSER_E2E") == "1", "opt-in real Chromium")
class AliasChromium(unittest.TestCase):
    def test_browser_blocks_disguised_destructive_get(self):
        html = ('<html><head><base href="http://127.0.0.1:8765/"></head><body>'
                '<button onclick="fetch(\'/api/deleteUser\').catch(() => {})">'
                'View details</button><p id="s">Pending</p></body></html>')
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html,
                            allowed_click_labels=("View details",),
                            chromium_path=os.getenv("SPRAY_CHROMIUM_PATH")) as browser:
            browser.observe()
            browser.click(0)
            browser.page.wait_for_timeout(150)
            self.assertEqual(browser._blocked_unsafe_requests, 1)
            self.assertEqual(browser.page.locator("#s").inner_text(), "Pending")


if __name__ == "__main__":
    unittest.main()
