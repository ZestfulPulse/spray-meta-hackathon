"""Offline regression: an observed control must not drift to another DOM node.

Playwright Locator.nth() resolves against the CURRENT DOM. If controls reorder
between observe and click, a saved nth locator can act on an unobserved control.
These fakes exercise the PreviewBrowser adapter without launching Chromium.
"""
import unittest

from spray_schema.playwright_preview import PreviewBrowser


class FakeHandle:
    def __init__(self, label, page):
        self.label = label
        self.page = page
        self.detached = False

    def inner_text(self):
        # Match Playwright's real ElementHandle API: no timeout keyword.
        if self.detached:
            raise RuntimeError("observed element detached")
        return self.label

    def evaluate(self, script):
        if self.detached:
            raise RuntimeError("observed element detached")
        return "BUTTON" if script == "e => e.tagName" else False

    def is_enabled(self):
        return not self.detached

    def click(self, timeout=None):
        if self.detached:
            raise RuntimeError("observed element detached")
        self.page.clicked.append(self.label)

    def dispose(self):
        pass


class DynamicNth:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    def is_visible(self):
        return True

    def element_handle(self):
        return self.page.buttons[self.index]

    # This is how the old implementation drifted after a reorder:
    def inner_text(self, timeout=None):
        return self.page.buttons[self.index].inner_text(timeout)

    def evaluate(self, script):
        return self.page.buttons[self.index].evaluate(script)

    def is_enabled(self):
        return self.page.buttons[self.index].is_enabled()

    def click(self, timeout=None):
        return self.page.buttons[self.index].click(timeout)


class FakeCollection:
    def __init__(self, page):
        self.page = page

    def all(self):
        return [DynamicNth(self.page, i) for i in range(len(self.page.buttons))]


class FakeBody:
    def inner_text(self):
        return "Pending"


class FakePage:
    url = "about:blank"

    def __init__(self):
        self.clicked = []
        self.buttons = [
            FakeHandle("Open details", self),
            FakeHandle("Open insights", self),
        ]

    def locator(self, selector):
        # Production observe() scans visible controls via "button:visible,
        # a:visible" (hidden controls must not consume the scan budget and
        # is_visible() is re-checked). Mirror that selector here.
        if selector == "button:visible, a:visible":
            return FakeCollection(self)
        if selector == "body":
            return FakeBody()
        raise AssertionError(selector)

    def title(self):
        return "SPRAY synthetic"


def browser_with_page():
    url = "http://127.0.0.1:8765/"
    browser = PreviewBrowser(
        url, allowed_origin=url, fixture_html="<p>Pending</p>",
        allowed_click_labels=("Open details", "Open insights"),
    )
    browser.page = FakePage()
    return browser


class ObservedControlIdentityTests(unittest.TestCase):
    def test_reordered_dom_cannot_redirect_click_to_different_allowed_control(self):
        browser = browser_with_page()
        observed = browser.observe()
        self.assertEqual(observed["controls"][0]["label"], "Open details")
        browser.page.buttons.reverse()
        browser.click(0)
        self.assertEqual(browser.page.clicked, ["Open details"])
        self.assertEqual(browser._blocked_policy_actions, 0)

    def test_changed_label_on_same_element_is_policy_denied(self):
        browser = browser_with_page()
        browser.observe()
        browser.page.buttons[0].label = "Open insights"
        with self.assertRaises(PermissionError):
            browser.click(0)
        self.assertEqual(browser.page.clicked, [])
        self.assertEqual(browser._blocked_policy_actions, 1)

    def test_detached_observed_element_does_not_click_replacement(self):
        browser = browser_with_page()
        browser.observe()
        browser.page.buttons[0].detached = True
        browser.page.buttons[0] = FakeHandle("Open insights", browser.page)
        with self.assertRaises(RuntimeError):
            browser.click(0)
        self.assertEqual(browser.page.clicked, [])


if __name__ == "__main__":
    unittest.main()
