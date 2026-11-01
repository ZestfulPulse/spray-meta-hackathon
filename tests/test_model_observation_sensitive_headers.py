"""No-network regression: HTTP credential headers must never reach Gemini."""
import json
import unittest

from spray_schema.model_observation import redact, sanitize_observation


class SensitiveHeaderRedactionTests(unittest.TestCase):
    def test_basic_authorization_masks_full_spaced_value(self):
        cleaned = redact("Authorization: Basic RkFLRV9DUkVERU5USUFM")
        self.assertEqual(cleaned, "Authorization=[REDACTED]")

    def test_digest_authorization_masks_all_parameters(self):
        cleaned = redact('Authorization: Digest username="fake", response="FAKE_DIGEST"')
        self.assertEqual(cleaned, "Authorization=[REDACTED]")

    def test_cookie_and_set_cookie_hide_session_values(self):
        self.assertEqual(redact("Cookie: sid=FAKE_SESSION; auth=FAKE_COOKIE"),
                         "Cookie=[REDACTED]")
        self.assertEqual(redact("Set-Cookie: sid=FAKE_SESSION; HttpOnly; Secure"),
                         "Set-Cookie=[REDACTED]")

    def test_proxy_authorization_has_same_guard(self):
        cleaned = redact("Proxy-Authorization: Basic FAKE_PROXY_SECRET")
        self.assertEqual(cleaned, "Proxy-Authorization=[REDACTED]")

    def test_observation_title_body_and_control_are_sanitized(self):
        observation = {
            "url": "https://preview.example/dashboard",
            "title": "Authorization: Basic FAKE_TITLE_SECRET",
            "visible_text": "Cookie: sid=FAKE_BODY_SECRET",
            "controls": [{"kind": "button", "label": "Set-Cookie: FAKE_LABEL_SECRET", "enabled": True}],
        }
        serialized = json.dumps(sanitize_observation(observation))
        for marker in ("FAKE_TITLE_SECRET", "FAKE_BODY_SECRET", "FAKE_LABEL_SECRET"):
            self.assertNotIn(marker, serialized)
        self.assertEqual(serialized.count("[REDACTED]"), 3)

    def test_next_line_and_normal_non_header_text_survive(self):
        cleaned = redact("Authorization: Basic FAKE_SECRET\nOpen details")
        self.assertEqual(cleaned, "Authorization=[REDACTED]\nOpen details")
        self.assertEqual(redact("Open details"), "Open details")
        self.assertEqual(redact("password='FAKE MANY WORDS'"), "password=[REDACTED]")


if __name__ == "__main__":
    unittest.main()
