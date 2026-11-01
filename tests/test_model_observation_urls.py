"""No-network regression tests: URL-shaped page text must not expose secrets to Gemini."""
import json
import unittest

from spray_schema.model_observation import redact, sanitize_observation


class ModelObservationUrlRedactionTests(unittest.TestCase):
    def test_embedded_url_userinfo_is_never_sent_to_model(self):
        raw = "Review https://reader:FAKEPASS123@preview.example/private/report"
        redacted = redact(raw)
        self.assertTrue(redacted.startswith("Review "))
        self.assertIn("[REDACTED_URL]", redacted)
        for secret in ("reader", "FAKEPASS123", "/private/report"):
            self.assertNotIn(secret, redacted)

    def test_embedded_url_path_and_query_are_not_sent_to_model(self):
        raw = "Open https://preview.example/reset/FAKEPATH123?token=FAKEQUERY123"
        redacted = redact(raw)
        self.assertIn("[REDACTED_URL]", redacted)
        for secret in ("FAKEPATH123", "FAKEQUERY123", "/reset/"):
            self.assertNotIn(secret, redacted)

    def test_title_visible_text_and_control_label_are_all_redacted(self):
        observation = {
            "url": "https://preview.example/dashboard?token=URL_QUERY",
            "title": "View https://a:TITLEPASS@preview.example/records",
            "visible_text": "Export from https://preview.example/export/LEAKEDPATH",
            "controls": [{"kind": "button", "label": "See https://preview.example/path/LABELPASS", "enabled": True}],
        }
        clean = sanitize_observation(observation)
        self.assertEqual(clean["url"], "/dashboard")
        serialized = json.dumps(clean)
        for secret in ("URL_QUERY", "TITLEPASS", "LEAKEDPATH", "LABELPASS"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(serialized.count("[REDACTED_URL]"), 3)

    def test_non_url_text_and_existing_secret_masking_remain_compatible(self):
        self.assertEqual(redact("Open details"), "Open details")
        self.assertEqual(redact("password=FAKESECRET"), "password=[REDACTED]")
        self.assertEqual(redact("Plain product label", 5), "Plain")

class ModelObservationPathLeakTests(unittest.TestCase):
    def test_sensitive_path_segments_are_not_model_visible(self):
        cases = (
            ("https://preview.example/dashboard/customer/FAKEPATH123?token=FAKEQUERY123",
             ("customer", "FAKEPATH123", "FAKEQUERY123")),
            ("https://preview.example/reports/alice", ("alice",)),
            ("https://preview.example/orders/731234", ("orders", "731234")),
            ("https://preview.example/reset/%46%41%4B%45SECRET", ("%46", "SECRET", "reset")),
            ("/projects/private-audit", ("projects", "private-audit")),
        )
        for url, secrets in cases:
            with self.subTest(url=url):
                clean = sanitize_observation({"url": url})
                serialized = json.dumps(clean)
                for secret in secrets:
                    self.assertNotIn(secret, serialized)
                self.assertIn("[REDACTED_PATH]", clean["url"])

    def test_allowlisted_generic_routes_keep_navigation_context(self):
        for url, expected in (
            ("https://preview.example/dashboard?session_id=PRIVATE", "/dashboard"),
            ("https://preview.example/reports/summary#tab", "/reports/summary"),
            ("https://preview.example/", "/"),
            ("https://preview.example", ""),
        ):
            with self.subTest(url=url):
                self.assertEqual(sanitize_observation({"url": url})["url"], expected)

    def test_untrusted_path_must_remain_bounded(self):
        url = "https://preview.example/dashboard/" + "PRIVATEXYZ" * 70
        clean = sanitize_observation({"url": url})
        self.assertLessEqual(len(clean["url"]), 250)
        self.assertNotIn("PRIVATEXYZ", clean["url"])


if __name__ == "__main__":
    unittest.main()
