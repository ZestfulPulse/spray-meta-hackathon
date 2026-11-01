"""Offline CLI for the SPRAY Decision Record mechanism.

Two genuinely separate invocations keep the prediction honest:

    python3 scripts/decision_cli.py create   --decisions-dir decisions --json create.json
    python3 scripts/decision_cli.py finalize --decisions-dir decisions \
        --id D-0001 --actual-outcome "..." --prediction-vs-actual "..." --lesson "..."

``create`` writes a record whose prediction is committed and whose outcome is
null (status=PREDICTED). ``finalize`` (a later, separate run) fills only the
outcome/comparison/lesson and flips status to FINALIZED. ``validate`` checks a
candidate record file; ``lessons`` prints the prior lessons helper output.

This CLI performs NO network or model call and never prints any credential or
untrusted value: it prints only a short confirmation line (record id + status).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spray_schema.decision_record import (  # noqa: E402
    create_record,
    finalize_record,
    load_prior_lessons,
    load_records,
    validate_record,
)


def _load_json_arg(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(data) is not dict:
        raise ValueError("create payload must be a JSON object")
    return data


def _cmd_create(args: argparse.Namespace) -> int:
    payload = _load_json_arg(args.json)
    allowed = {
        "id", "observed_state", "prior_decision_ids_read", "candidates",
        "scoring_criteria", "candidate_scores", "chosen_action",
        "chosen_rationale", "rejected", "prediction", "lessons_applied",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError("unknown create fields: " + ",".join(sorted(unknown)))
    # Prediction phase must not carry any outcome.
    for forbidden in ("actual_outcome", "prediction_vs_actual", "lesson", "status"):
        if forbidden in payload:
            raise ValueError("create does not accept field: " + forbidden)
    record = create_record(
        args.decisions_dir,
        id=payload.get("id"),
        observed_state=payload.get("observed_state"),
        prior_decision_ids_read=payload.get("prior_decision_ids_read", []),
        candidates=payload.get("candidates", []),
        scoring_criteria=payload.get("scoring_criteria", []),
        candidate_scores=payload.get("candidate_scores", []),
        chosen_action=payload.get("chosen_action"),
        chosen_rationale=payload.get("chosen_rationale"),
        rejected=payload.get("rejected", []),
        prediction=payload.get("prediction"),
        lessons_applied=payload.get("lessons_applied", []),
    )
    print("created %s status=%s" % (record["id"], record["status"]))
    return 0


def _cmd_finalize(args: argparse.Namespace) -> int:
    record = finalize_record(
        args.decisions_dir,
        args.id,
        actual_outcome=args.actual_outcome,
        prediction_vs_actual=args.prediction_vs_actual,
        lesson=args.lesson,
    )
    print("finalized %s status=%s" % (record["id"], record["status"]))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    candidate = _load_json_arg(args.json)
    seq_id = candidate.get("id")
    priors = [
        r for r in load_records(args.decisions_dir)
        if r.get("id") != seq_id
    ]
    result = validate_record(candidate, prior_records=priors)
    print("valid" if result.valid else ("invalid: " + result.reason))
    return 0 if result.valid else 1


def _cmd_lessons(args: argparse.Namespace) -> int:
    lessons = load_prior_lessons(args.decisions_dir)
    print(json.dumps(lessons, sort_keys=True, ensure_ascii=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="write a PREDICTED record")
    create.add_argument("--decisions-dir", default="decisions")
    create.add_argument("--json", required=True, help="path to a JSON payload")
    create.set_defaults(func=_cmd_create)

    finalize = sub.add_parser("finalize", help="fill outcome/lesson, FINALIZE")
    finalize.add_argument("--decisions-dir", default="decisions")
    finalize.add_argument("--id", required=True)
    finalize.add_argument("--actual-outcome", required=True)
    finalize.add_argument("--prediction-vs-actual", required=True)
    finalize.add_argument("--lesson", required=True)
    finalize.set_defaults(func=_cmd_finalize)

    validate = sub.add_parser("validate", help="validate a candidate record file")
    validate.add_argument("--decisions-dir", default="decisions")
    validate.add_argument("--json", required=True)
    validate.set_defaults(func=_cmd_validate)

    lessons = sub.add_parser("lessons", help="print prior lessons for the planner")
    lessons.add_argument("--decisions-dir", default="decisions")
    lessons.set_defaults(func=_cmd_lessons)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
