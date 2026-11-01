"""Run the offline Gemini free-tier credential preflight (no network call).

Prints exactly one redacted status line reporting credential presence (Y/N)
and the planner's configured quota-guard values. The credential value itself
is never printed. If the credential is absent the script also prints a
STOP/report message and exits non-zero; otherwise it exits 0.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spray_schema.gemini_preflight import format_preflight_line, gemini_preflight


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    result = gemini_preflight()
    print(format_preflight_line(result))
    if result.stop:
        print(result.message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
