"""No-cost H8 tests for the redacted model boundary and Chromium evidence."""
from hashlib import sha256
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from spray_schema.bedrock_planner import BedrockPlanner
from tests.bedrock_test_support import make_test_budget
from spray_schema.evidence_capture import capture_fixture_evidence
from spray_schema.model_observation import sanitize_observation
from spray_schema.agent_loop import run_loop
from spray_schema.playwright_preview import PreviewBrowser

URL = "http://127.0.0.1:8765/"


def _png_has_dark_pixels(path):
    """Decode Chromium's 8-bit RGB/RGBA PNG without an imaging dependency."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("invalid PNG signature")
    offset = 8
    compressed = bytearray()
    width = height = bit_depth = color_type = None
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + length]
        offset += length + 12
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk)
            if (bit_depth != 8 or color_type not in (2, 6) or compression != 0
                    or filtering != 0 or interlace != 0):
                raise AssertionError("unsupported Chromium PNG format")
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    raw = zlib.decompress(compressed)
    previous = bytearray(stride)
    pos = 0

    def paeth(left, up, upper_left):
        estimate = left + up - upper_left
        distances = (abs(estimate - left), abs(estimate - up),
                     abs(estimate - upper_left))
        return (left, up, upper_left)[distances.index(min(distances))]

    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        scan = bytearray(raw[pos:pos + stride])
        pos += stride
        for i in range(stride):
            left = scan[i - channels] if i >= channels else 0
            up = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                scan[i] = (scan[i] + left) & 255
            elif filter_type == 2:
                scan[i] = (scan[i] + up) & 255
            elif filter_type == 3:
                scan[i] = (scan[i] + ((left + up) // 2)) & 255
            elif filter_type == 4:
                scan[i] = (scan[i] + paeth(left, up, upper_left)) & 255
            elif filter_type != 0:
                raise AssertionError("unsupported PNG row filter")
        for i in range(0, stride, channels):
            if max(scan[i:i + 3]) < 200:
                return True
        previous = scan
    return False

class FakeConverse:
    __spray_offline_only__ = True
    def __init__(self):
        self.calls = []
    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"stopReason": "end_turn",
                "output": {"message": {"content": [
                    {"text": '{"action":"click","index":0}'}]}}}

class RedactionUnit(unittest.TestCase):
    def test_observation_is_allowlisted(self):
        clean = sanitize_observation({
            "url": "https://preview.example.test/?token=URLSECRET",
            "headers": {"Authorization": "PRIVATE"},
            "raw_dom": "DOMSECRET",
            "title": "api_key=TITLESECRET",
            "visible_text": "password=TEXTSECRET",
            "controls": [{"kind": "button", "label": "Repair"}]})
        blob = json.dumps(clean)
        for secret in ("URLSECRET", "PRIVATE", "DOMSECRET",
                       "TITLESECRET", "TEXTSECRET"):
            self.assertNotIn(secret, blob)
        self.assertEqual(clean["controls"][0]["label"], "Repair")

    def test_converse_receives_redacted_observation(self):
        fake = FakeConverse()
        budget = make_test_budget(self, "mock", "us-east-1")
        planner = BedrockPlanner("mock", "us-east-1", authorized=True,
                                 client=fake, budget=budget, max_requests=1,
                                 test_mode=True)
        result = planner.choose(
            observation={"url": "/?token=URLSECRET",
                         "visible_text": "api_key=PAGESECRET",
                         "controls": [{"kind": "button", "label": "Repair"}]},
            target_text="Completed", history=())
        self.assertEqual(result, {"action": "click", "index": 0})
        payload = fake.calls[0]["messages"][0]["content"][0]["text"]
        self.assertNotIn("URLSECRET", payload)
        self.assertNotIn("PAGESECRET", payload)
        with self.assertRaises(RuntimeError):
            planner.choose(observation={"controls": []},
                           target_text="Completed", history=())

    def test_unapproved_live_call_is_denied(self):
        planner = BedrockPlanner("test", "us-east-1")
        with self.assertRaises(PermissionError):
            planner.choose(observation={"controls": []},
                           target_text="Completed", history=())
        self.assertEqual(planner.requests, 0)

    def test_remote_screenshot_is_denied(self):
        class Remote:
            fixture_html = None
        with self.assertRaises(PermissionError):
            capture_fixture_evidence(Remote(), "/tmp/never-create", target_verified=True)

@unittest.skipUnless(os.getenv("RUN_BROWSER_E2E") == "1", "opt-in Chromium")
class RealChromiumPrivacy(unittest.TestCase):
    def test_redacted_evidence_and_real_browser_action(self):
        html = ("<html><head><title>api_key=TITLESECRET</title></head>"
                "<body style='margin:0;background:white'>"
                "<p style='margin:0;color:black'>password=PAGESECRET</p>"
                "<button style='border:0;background:white;color:black' "
                "onclick=\"document.getElementById('s').textContent='Completed'\">"
                "Repair</button><p id='s'>Pending</p></body></html>")
        fake = FakeConverse()
        budget = make_test_budget(self, "mock", "us-east-1")
        planner = BedrockPlanner("mock", "us-east-1", authorized=True,
                                 client=fake, budget=budget, test_mode=True)
        with tempfile.TemporaryDirectory() as folder:
            with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html) as browser:
                result = run_loop(browser, planner, target_text="Completed")
                self.assertEqual(result.status, "PASS")
                evidence = capture_fixture_evidence(
                    browser, folder, target_verified=browser.verify("Completed"))
                self.assertTrue(browser.verify("Completed"))
            self.assertEqual(evidence["title"], "api_key=[REDACTED]")
            self.assertTrue(evidence["target_verified"])
            self.assertFalse(_png_has_dark_pixels(Path(folder) / "layout.png"))
            self.assertEqual(sha256((Path(folder) / "layout.png").read_bytes()).hexdigest(),
                             evidence["screenshot_sha256"])
            self.assertNotIn("PAGESECRET", json.dumps(evidence))
            self.assertNotIn("TITLESECRET", json.dumps(evidence))

if __name__ == "__main__":
    unittest.main()
