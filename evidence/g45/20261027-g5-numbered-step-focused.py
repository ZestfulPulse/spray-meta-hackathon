"""Focused CPython reproduction of the numbered-trace guard only.

Not an original SPRAY module / repository test. Uses intentionally synthetic data.
"""
import copy
import hashlib
import json
import unittest

VALID = [
    {"step": 1, "outcome": "action_failed", "action": {"action": "click", "index": 0}},
    {"step": 2, "outcome": "executed", "action": {"action": "click", "index": 1}},
]
BAD = {
    "duplicate": (1, 1),
    "reverse": (2, 1),
    "wrong_start": (2, 3),
    "gap": (1, 3),
    "mixed_unnumbered": (1, None),
}

def prior_guard(trace, max_steps=4):
    """Pre-patch guard: checks each step's type/range but not continuity."""
    for entry in trace:
        seq = entry.get("step")
        if seq is not None and (type(seq) is not int or not 1 <= seq <= max_steps):
            return False
    return True

def updated_guard(trace, max_steps=4):
    """Post-patch guard: type/range plus all-or-none contiguous numbering."""
    for entry in trace:
        seq = entry.get("step")
        if "step" in entry and (type(seq) is not int or not 1 <= seq <= max_steps):
            return False
    if (any("step" in entry for entry in trace)
            and any(entry.get("step") != number
                    for number, entry in enumerate(trace, 1))):
        return False
    return True

class TraceGuard(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(prior_guard(VALID))
        self.assertTrue(updated_guard(VALID))
    def test_legacy_unnumbered(self):
        legacy = [{k: v for k, v in step.items() if k != "step"} for step in VALID]
        self.assertTrue(updated_guard(legacy))
    def test_invalid_numbering(self):
        for name, nums in BAD.items():
            with self.subTest(name=name):
                trace = copy.deepcopy(VALID)
                for e, number in zip(trace, nums):
                    if number is None:
                        e.pop("step")
                    else:
                        e["step"] = number
                self.assertTrue(prior_guard(trace))
                self.assertFalse(updated_guard(trace))
    def test_explicit_null(self):
        trace = copy.deepcopy(VALID)
        trace[0]["step"] = None
        self.assertTrue(prior_guard(trace))
        self.assertFalse(updated_guard(trace))

if __name__ == '__main__':
    unittest.main(verbosity=2)
