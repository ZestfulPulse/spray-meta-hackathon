"""No-network regression: common token-bearing fields and headers stay out of Gemini observations."""
import json
import unittest

from spray_schema.model_observation import redact, sanitize_observation


class AdditionalTokenRedactionTests(unittest.TestCase):
    def test_oidc_id_token_quoted_value(self):
        self.assertEqual(redact('id_token="FAKE_OIDC_TOKEN_123"'),
                         'id_token=[REDACTED]')

    def test_csrf_xsrf_and_session_tokens(self):
        for key in ("csrf_token", "xsrfToken", "session-token", "authToken"):
            with self.subTest(key=key):
                self.assertEqual(redact(f'{key}: "FAKE TOKEN VALUE"'),
                                 f'{key}=[REDACTED]')

    def test_x_auth_token_header_multiple_values(self):
        self.assertEqual(redact('X-Auth-Token: FAKE_TOKEN_123; scope=private'),
                         'X-Auth-Token=[REDACTED]')

    def test_observation_title_text_and_labels_never_include_tokens(self):
        observation = {
            "url": "https://preview.example/dashboard",
            "title": 'id_token="FAKE_TITLE_TOKEN"',
            "visible_text": 'csrf_token="FAKE BODY TOKEN"',
            "controls": [{"kind": "button", "label": "X-Auth-Token: FAKE_LABEL_TOKEN; scope=private", "enabled": True}],
        }
        result = json.dumps(sanitize_observation(observation))
        for secret in ("FAKE_TITLE_TOKEN", "FAKE BODY TOKEN", "FAKE_LABEL_TOKEN"):
            self.assertNotIn(secret, result)
        self.assertEqual(result.count('[REDACTED]'), 3)

    def test_regular_labels_remain_unchanged(self):
        self.assertEqual(redact('Open details'), 'Open details')
        self.assertEqual(redact('Showing 5 reports'), 'Showing 5 reports')


if __name__ == "__main__":
    unittest.main()
