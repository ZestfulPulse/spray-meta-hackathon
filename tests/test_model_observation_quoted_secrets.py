"""Synthetic no-network security regressions: redact entire quoted secret values."""
import json
import unittest

from spray_schema.model_observation import redact, sanitize_observation


class QuotedSecretBoundaryTests(unittest.TestCase):
    def test_double_quoted_spaced_password_is_fully_redacted(self):
        clean = redact('password="FAKE ALPHA BETA"')
        self.assertEqual(clean, 'password=[REDACTED]')

    def test_single_quoted_spaced_token_is_fully_redacted(self):
        clean = redact("access_token: 'FAKE FIRST SECOND'")
        self.assertEqual(clean, 'access_token=[REDACTED]')

    def test_escaped_quote_and_spaces_are_fully_redacted(self):
        raw = 'client_secret="FAKE\\\" PRIVATE SUFFIX"'
        clean = redact(raw)
        self.assertEqual(clean, 'client_secret=[REDACTED]')

    def test_json_quoted_api_key_and_session_id_are_fully_redacted(self):
        raw = '{"api_key": "FAKE MANY WORDS", "session_id":"FAKE LONG ID"}'
        clean = redact(raw)
        for fragment in ('FAKE', 'MANY', 'WORDS', 'LONG', 'ID'):
            self.assertNotIn(fragment, clean)
        self.assertEqual(clean.count('[REDACTED]'), 2)

    def test_observation_title_visible_text_and_control_label(self):
        clean = sanitize_observation({
            'url': 'https://preview.example/dashboard',
            'title': 'Report password="TITLE SECRET WORDS"',
            'visible_text': "access_token='VISIBLE SECRET WORDS'",
            'controls': [{'kind': 'link', 'label': 'Go client_secret="BUTTON SECRET WORDS"', 'enabled': True}],
        })
        encoded = json.dumps(clean)
        for fragment in ('TITLE SECRET WORDS', 'VISIBLE SECRET WORDS', 'BUTTON SECRET WORDS'):
            self.assertNotIn(fragment, encoded)
        self.assertEqual(encoded.count('[REDACTED]'), 3)

    def test_plain_unquoted_values_and_normal_labels_compatible(self):
        self.assertEqual(redact('password=FAKEVALUE'), 'password=[REDACTED]')
        self.assertEqual(redact('Open details'), 'Open details')
        self.assertEqual(redact('api_key="FAKEVALUE"'), 'api_key=[REDACTED]')  # pragma: allowlist secret


if __name__ == '__main__':
    unittest.main()
