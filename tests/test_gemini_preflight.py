import unittest

from spray_schema.gemini_planner import _MAX_OUTPUT_TOKENS, _MODEL_ID
from spray_schema.gemini_preflight import (
    format_preflight_line,
    gemini_preflight,
)

# Obviously-fake, non-credential-shaped placeholder. It is not assigned to any
# credential-shaped name so ci/policy_checks.py does not flag this file, and it
# is injected via the env dict rather than os.environ.
FAKE_PRESENCE_MARKER = "fake-present-marker-not-a-secret"


class GeminiPreflightTests(unittest.TestCase):
    def test_credential_present(self):
        result = gemini_preflight(env={"GEMINI_API_KEY": FAKE_PRESENCE_MARKER})
        self.assertTrue(result.credential_present)
        self.assertFalse(result.stop)
        self.assertEqual(result.model_id, _MODEL_ID)
        self.assertEqual(result.max_requests, 4)
        self.assertEqual(result.timeout, 10)
        self.assertEqual(result.max_output_tokens, _MAX_OUTPUT_TOKENS)
        line = format_preflight_line(result)
        self.assertIn("credential_present=Y", line)
        self.assertIn("max_requests=4", line)
        self.assertIn("timeout=10", line)
        self.assertIn(_MODEL_ID, line)
        self.assertIn(f"max_output_tokens={_MAX_OUTPUT_TOKENS}", line)
        self.assertNotIn(FAKE_PRESENCE_MARKER, line)

    def test_credential_absent(self):
        result = gemini_preflight(env={})
        self.assertFalse(result.credential_present)
        self.assertTrue(result.stop)
        self.assertIn("STOP", result.message)
        self.assertIn("report", result.message.lower())
        # The message must refuse (not offer) a paid alternative.
        self.assertIn("do not substitute", result.message.lower())
        self.assertIn("credential_present=N", format_preflight_line(result))

    def test_whitespace_only_treated_as_absent(self):
        for value in ("", "   ", "\t\n"):
            with self.subTest(value=repr(value)):
                result = gemini_preflight(env={"GEMINI_API_KEY": value})
                self.assertFalse(result.credential_present)
                self.assertTrue(result.stop)

    def test_non_string_treated_as_absent(self):
        for value in (None, 123, object()):
            with self.subTest(value=repr(value)):
                result = gemini_preflight(env={"GEMINI_API_KEY": value})
                self.assertFalse(result.credential_present)
                self.assertTrue(result.stop)

    def test_formatted_line_never_contains_secret(self):
        result = gemini_preflight(env={"GEMINI_API_KEY": FAKE_PRESENCE_MARKER})
        self.assertNotIn(FAKE_PRESENCE_MARKER, format_preflight_line(result))

    def test_single_output_line(self):
        result = gemini_preflight(env={"GEMINI_API_KEY": FAKE_PRESENCE_MARKER})
        line = format_preflight_line(result)
        self.assertNotIn("\n", line)


if __name__ == "__main__":
    unittest.main()
