"""Regression: protocol-relative URL tokens must not reach Gemini observation."""
import json
import unittest

from spray_schema.model_observation import redact, sanitize_observation


class ProtocolRelativeRedactionTests(unittest.TestCase):
    def test_protocol_relative_url_userinfo_and_tokens_redacted(self):
        # A scheme-relative URL is a legitimate browser link without https:.
        raw = "Open //reader:FAKEPASS_PR@preview.example/reset/FAKETOKEN_PR?code=FAKEQUERY_PR"
        clean = redact(raw)
        self.assertEqual(clean, "Open [REDACTED_URL]")
        for secret in ("reader", "FAKEPASS_PR", "FAKETOKEN_PR", "FAKEQUERY_PR"):
            self.assertNotIn(secret, clean)

    def test_protocol_relative_urls_in_all_observation_text_fields(self):
        observation = {
            "url": "https://preview.example/dashboard",
            "title": "Inspect //reader:TITLEPASS_PR@preview.example/private/profile",
            "visible_text": "See //preview.example/reset/VISIBLETOKEN_PR?code=QUERYTOKEN_PR",
            "controls": [{"kind": "link", "label": "View //preview.example/private/LABELTOKEN_PR", "enabled": True}],
        }
        clean = sanitize_observation(observation)
        self.assertEqual(clean["url"], "/dashboard")
        self.assertEqual(json.dumps(clean).count("[REDACTED_URL]"), 3)
        for secret in ("TITLEPASS_PR", "VISIBLETOKEN_PR", "QUERYTOKEN_PR", "LABELTOKEN_PR"):
            self.assertNotIn(secret, json.dumps(clean))

    def test_internal_double_slashes_do_not_erase_regular_labels(self):
        # Do not mistake a slash-pair inside plain text for a URL authority.
        self.assertEqual(redact("ratio A//B and Open details"), "ratio A//B and Open details")
        self.assertEqual(redact("See https://preview.example/details"), "See [REDACTED_URL]")



if __name__ == "__main__":
    unittest.main()
