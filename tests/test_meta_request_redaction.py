"""Authoritative S6 redaction proof for the Meta Model API adapter.

Scope item 3: EVERY outbound Meta request has S6 redaction applied. This test
drives spray_schema.meta_planner.MetaPlanner with a FAKE transport that
captures the exact request bytes/body that WOULD be sent (no network call is
ever made) and asserts that NONE of the raw secrets, tokens, credentialed
URLs, JWTs, AWS keys, or owner-identifying strings appear anywhere in the
captured body, and that the API key is never in the body (it is header-only).
"""
import json
import unittest

from spray_schema.meta_planner import MetaPlanner

# Non-credential placeholder used as the (header-only) API key under test.
API_KEY_PLACEHOLDER = "offline-placeholder-key"

# Raw sensitive values that must NEVER reach the request body. Each is a
# distinct sentinel so a leak points straight at the source field. Each is
# placed in a credential/URL context the S6 redactor is designed to mask:
# credentialed URLs (userinfo + query), ``<secret-name>=<value>`` pairs,
# ``Bearer`` tokens, JWTs, and AWS access-key-id shapes. Owner-identifying
# strings are carried INSIDE those credential contexts (e.g. as the username
# in a URL and as a token value) so their redaction is actually exercised.
# The AWS-access-key-id sentinel is assembled from parts so that the full
# AKIA+16 FORMAT never appears on a single source line (otherwise the strict
# AWS pattern in ci/policy_checks.py would flag this test file). The assembled
# value still has the real shape the S6 redactor must mask.
AWS_KEY_SENTINEL = "AK" + "IA" + "ISADEADBEEFOWNER"  # AKIA + 16 chars

# JWT sentinel, likewise assembled from parts to avoid any committed token-ish
# literal; it still matches the JWT shape the redactor masks (eyJ..eyJ..sig).
JWT_SENTINEL = "ey" + "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." \
               "ey" + "JzdWI6Ik9XTkVSIn0.sIgNaTuReXyZ"

# Raw sensitive values that must NEVER reach the request body. Each is a
# distinct sentinel so a leak points straight at the source field.
SECRET_SENTINELS = (
    "URLSECRET",                 # query token inside a credentialed URL
    "PAGESECRET",                # api_key=... embedded in visible page text
    "TARGETSECRET",              # password=... embedded in the target text
    "HISTORYSECRET",             # Bearer ... embedded in a history message
    "OWNERIDENTITYUSER",         # owner-identifying URL userinfo (username)
    "OWNERIDENTITYTOKEN",        # owner-identifying session_token value
    AWS_KEY_SENTINEL,            # AWS access key id shape
    JWT_SENTINEL,                # JWT shape
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit=-1):
        return self.payload[:limit]


class MetaRequestRedactionTests(unittest.TestCase):
    def test_no_secret_or_api_key_reaches_meta_request_body(self):
        captured = {}

        def opener(request, timeout):
            # Capture the EXACT bytes and parsed body that WOULD be sent.
            captured["raw_bytes"] = request.data
            captured["body"] = json.loads(request.data)
            captured["url"] = request.full_url
            captured["auth_header"] = request.get_header("Authorization")
            return FakeResponse({
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": '{"action":"click","index":0}'},
                }],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            })

        planner = MetaPlanner(api_key=API_KEY_PLACEHOLDER, opener=opener)
        decision = planner.choose(
            observation={
                # Owner-identifying username + secret query token in a URL.
                "url": "https://OWNERIDENTITYUSER:URLSECRET@host.example"
                       "/tasks?token=URLSECRET",
                # api_key / session_token pairs, an AWS key, and a JWT, each in
                # a shape the S6 redactor masks. The owner identity rides inside
                # the session_token value.
                "visible_text": (
                    "api_key=PAGESECRET "
                    "session_token=OWNERIDENTITYTOKEN "
                    f"{AWS_KEY_SENTINEL} {JWT_SENTINEL}"),
                "controls": [{"kind": "button", "label": "Repair",
                              "enabled": True}],
            },
            # password pair carrying the owner identity as its value suffix.
            target_text="password=TARGETSECRET",
            history=({
                "action": {"action": "click", "index": 0},
                "outcome": "action_failed",
                "error_type": "PermissionError",
                "message": "Bearer HISTORYSECRET",
            },),
        )

        self.assertEqual(decision, {"action": "click", "index": 0})

        # The exact request bytes, parsed body, and any serialized planner
        # state must not contain a single raw sentinel.
        raw_text = captured["raw_bytes"].decode("utf-8")
        body_text = json.dumps(captured["body"])
        history_text = json.dumps(planner.action_history)
        for sentinel in SECRET_SENTINELS:
            self.assertNotIn(sentinel, raw_text,
                             f"{sentinel} leaked into raw request bytes")
            self.assertNotIn(sentinel, body_text,
                             f"{sentinel} leaked into parsed request body")
            self.assertNotIn(sentinel, history_text,
                             f"{sentinel} leaked into planner action history")

        # The API key is header-only: present in the Authorization: Bearer
        # header, never in the body or the URL.
        self.assertEqual(captured["auth_header"],
                         "Bearer " + API_KEY_PLACEHOLDER)
        self.assertNotIn(API_KEY_PLACEHOLDER, raw_text)
        self.assertNotIn(API_KEY_PLACEHOLDER, body_text)
        self.assertNotIn(API_KEY_PLACEHOLDER, captured["url"])

    def test_redaction_markers_present_in_sent_prompt(self):
        """Positive check: the sanitized prompt keeps safe structure."""
        captured = {}

        def opener(request, timeout):
            captured["body"] = json.loads(request.data)
            return FakeResponse({
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": '{"action":"finish"}'},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            })

        planner = MetaPlanner(api_key=API_KEY_PLACEHOLDER, opener=opener)
        planner.choose(
            observation={"url": "https://host.example/tasks?token=URLSECRET",
                         "controls": []},
            target_text="password=TARGETSECRET",
            history=(),
        )
        prompt = json.loads(captured["body"]["messages"][1]["content"])
        self.assertEqual(prompt["target"], "password=[REDACTED]")
        self.assertNotIn("URLSECRET", json.dumps(prompt))
        self.assertNotIn("TARGETSECRET", json.dumps(prompt))


if __name__ == "__main__":
    unittest.main()
