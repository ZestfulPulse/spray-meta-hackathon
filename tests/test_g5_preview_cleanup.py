"""Offline G5 adapter cleanup regression: no browser or network required."""
import sys
import types
import unittest
from unittest.mock import patch

from spray_schema.playwright_preview import PreviewBrowser


class PreviewCleanupTests(unittest.TestCase):
    def test_navigation_failure_releases_context_browser_and_playwright(self):
        closed = []
        registered_guards = []
        class FakePage:
            def goto(self, *_args, **_kwargs):
                raise TimeoutError("SECRET_CANARY")

        class FakeContext:
            def route(self, *_args):
                registered_guards.append("http")
            def route_web_socket(self, *_args):
                # New adapter guard must be installed before any page exists.
                registered_guards.append("websocket")
            def new_page(self):
                if registered_guards != ["http", "websocket"]:
                    raise AssertionError("network guards must precede page creation")
                return FakePage()
            def close(self):
                closed.append("context")

        class FakeBrowser:
            def new_context(self, **_kwargs):
                return FakeContext()
            def close(self):
                closed.append("browser")

        class FakeChromium:
            def launch(self, **_kwargs):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()
            def stop(self):
                closed.append("playwright")

        class FakeSync:
            def start(self):
                return FakePlaywright()

        playwright_pkg = types.ModuleType("playwright")
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = lambda: FakeSync()
        playwright_pkg.sync_api = sync_api
        adapter = PreviewBrowser(
            "https://preview.zestfulpulse.com/read-only",
            allowed_origin="https://preview.zestfulpulse.com",
            allowed_click_labels=("Open details",),
        )
        with patch.dict(sys.modules, {
            "playwright": playwright_pkg,
            "playwright.sync_api": sync_api,
        }):
            with self.assertRaises(TimeoutError):
                adapter.__enter__()
        self.assertEqual(registered_guards, ["http", "websocket"])
        self.assertEqual(closed, ["context", "browser", "playwright"])

    def test_browser_launch_failure_still_stops_playwright(self):
        closed = []
        class FakeChromium:
            def launch(self, **_kwargs):
                raise RuntimeError("launch failed")
        class FakePlaywright:
            chromium = FakeChromium()
            def stop(self):
                closed.append("playwright")
        class FakeSync:
            def start(self):
                return FakePlaywright()

        pkg = types.ModuleType("playwright")
        api = types.ModuleType("playwright.sync_api")
        api.sync_playwright = lambda: FakeSync()
        pkg.sync_api = api
        adapter = PreviewBrowser(
            "http://127.0.0.1:8765/",
            allowed_origin="http://127.0.0.1:8765/",
            fixture_html="<p>synthetic</p>",
        )
        with patch.dict(sys.modules, {"playwright": pkg, "playwright.sync_api": api}):
            with self.assertRaises(RuntimeError):
                adapter.__enter__()
        self.assertEqual(closed, ["playwright"])


if __name__ == "__main__":
    unittest.main()
