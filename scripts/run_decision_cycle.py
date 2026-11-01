"""Offline, live-gated CLI for the SPRAY decision-cycle runner.

This is a THIN wrapper over ``spray_schema.decision_cycle``. It mirrors the
conventions of ``scripts/decision_cli.py``: it inserts the repo root on
``sys.path``, parses args with ``argparse``, performs NO network or model call
itself, and never prints any credential or untrusted value.

Live mode (the default) refuses to run unless ALL live-gate preconditions hold:
``META_API_KEY`` present, the resolved model id is NOT the unconfirmed
placeholder, and ``credit_budget`` authorizes the worst-case spend. In THIS
sandbox (INTEGRATIONS_ONLY, ``META_API_KEY`` absent, placeholder model id) the
gate refuses: it prints a single line beginning with the literal token
``LIVE-GATED`` explaining the first unmet precondition, makes NO call, and exits
with code 0.

    python scripts/run_decision_cycle.py            # live mode -> LIVE-GATED here
    python scripts/run_decision_cycle.py --now 2026-11-01T00:00:00Z
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spray_schema import credit_budget  # noqa: E402
from spray_schema import decision_cycle  # noqa: E402
from spray_schema import meta_planner  # noqa: E402


def _resolve_model_id() -> str:
    """Resolve the Meta model id from the environment or the placeholder."""
    return os.environ.get(meta_planner._MODEL_ID_ENV) or meta_planner.META_MODEL_ID


def _cmd_live(args: argparse.Namespace) -> int:
    model_id = _resolve_model_id()
    budget_inputs = {
        "worst_case_cost_usd": args.estimated_credit_cost_usd,
        "lifetime_credit_spent_usd": args.lifetime_credit_spent_usd,
        "round_spent_usd": args.round_spent_usd,
        "now_iso": args.now,
    }
    status = decision_cycle.live_gate_status(
        os.environ, model_id, budget_inputs=budget_inputs)
    if not status.permitted:
        # Single clear line beginning with the LIVE-GATED token. No secret or
        # untrusted value is printed (status.reason is a fixed, safe string).
        print("%s: %s" % (decision_cycle.LIVE_GATED_TOKEN, status.reason))
        return 0

    # Live execution would occur here. This sandbox never reaches this branch
    # (the gate above refuses). A real live run requires the owner's key and a
    # confirmed model id, plus a concrete planner; the CLI does not fabricate
    # one, so without a live planner it reports readiness and makes no call.
    print("LIVE-READY: preconditions satisfied; owner must supply a planner")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decisions-dir", default="decisions",
        help="directory holding D-NNNN.json records (default: decisions)")
    parser.add_argument(
        "--now", default=credit_budget.CREDIT_EXPIRY_ISO[:10] + "T00:00:00Z",
        help="normalized UTC 'Z' instant used for the credit-budget check")
    parser.add_argument(
        "--estimated-credit-cost-usd", dest="estimated_credit_cost_usd",
        type=float, default=0.02,
        help="worst-case per-call credit cost for the budget check")
    parser.add_argument(
        "--lifetime-credit-spent-usd", dest="lifetime_credit_spent_usd",
        type=float, default=0.0,
        help="lifetime credit spent to date for the budget check")
    parser.add_argument(
        "--round-spent-usd", dest="round_spent_usd", type=float, default=0.0,
        help="credit spent this round to date for the budget check")
    parser.set_defaults(func=_cmd_live)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
