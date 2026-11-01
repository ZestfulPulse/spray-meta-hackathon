"""Offline tests for the SPRAY decision-cycle runner and verifier extension.

These prove scope items 2, 3 and 5 of R2-S4 entirely offline (no network, no
model backend). Every decisions dir is an isolated ``tempfile.TemporaryDirectory``
so the repo's real ``decisions/`` dir is NEVER touched. A FAKE planner conforms
to ``spray_schema.provider_planner.Planner`` (read-only ``provider`` /
``model_id`` and ``choose(*, observation, target_text, history)``) and makes NO
network call; it encodes a deterministic self-direction policy so we can show a
later cycle CHANGE its choice because of an earlier cycle's recorded lesson.

Secret-shaped sentinels (the fake Meta key used only in the satisfied-live-gate
precondition test) are assembled from concatenated string parts so no AWS / JWT
/ PEM shape ever appears on a single source line (ci/policy_checks.py scans every
line, including tests) and the key is never written to any persisted file.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from spray_schema import credit_budget
from spray_schema import decision_cycle
from spray_schema import meta_planner
from spray_schema.decision_cycle import (
    LIVE_GATED_TOKEN,
    LiveGateStatus,
    live_gate_status,
    run_one_cycle,
)
from spray_schema.decision_record import (
    STATUS_FINALIZED,
    STATUS_PREDICTED,
    create_record,
    load_records,
    validate_record,
)
from spray_schema.provider_planner import Planner


# --------------------------------------------------------------------------- #
# Clock: a strictly-increasing normalized UTC 'Z' instant provider. The runner
# samples predicted_at before the planner call and outcome_at after the
# artifact exists, so successive samples must strictly advance.
# --------------------------------------------------------------------------- #
class StrictClock:
    """Return a strictly-increasing UTC 'Z' instant on each call."""

    def __init__(self, start_minute=0):
        self._n = start_minute

    def __call__(self):
        value = "2026-11-01T00:%02d:00Z" % self._n
        self._n += 1
        return value


# --------------------------------------------------------------------------- #
# Fake self-direction planner conforming to provider_planner.Planner. It makes
# NO network call; choose(...) returns a canned PLAN dict (domain, >=3
# candidates, scoring, choice, prediction, outcome, lesson). A policy callable
# computes the plan from the (observation, target_text, history) so tests can
# make a later cycle change its choice based on a prior lesson carried in
# ``history``.
# --------------------------------------------------------------------------- #
class FakePlanner:
    def __init__(self, policy, *, provider="meta", model_id="fake-muse-spark-001"):
        self.provider = provider
        self.model_id = model_id
        self._policy = policy
        self.calls = []

    def choose(self, *, observation, target_text, history):
        self.calls.append(
            {"observation": observation, "target_text": target_text,
             "history": history})
        return self._policy(observation=observation, target_text=target_text,
                            history=history)


class RaisingPlanner:
    """A sentinel planner whose choose() must never be called (live-gate)."""

    provider = "meta"
    model_id = "fake-muse-spark-001"

    def choose(self, *, observation, target_text, history):  # pragma: no cover
        raise AssertionError("planner.choose must not be called when live-gated")


def _candidates(names):
    return [{"name": n, "description": "opportunity " + n} for n in names]


def _scores(names):
    return [{"candidate": n, "impact": i} for i, n in enumerate(names, start=1)]


def _plan(*, domain, chosen, names, lesson, prediction="will improve throughput",
          observed_note=None):
    """Build a well-formed self-direction plan dict the runner accepts."""
    rejected = [{"candidate": n, "why": "lower expected value"}
                for n in names if n != chosen]
    plan = {
        "domain": domain,
        "candidates": _candidates(names),
        "scoring_criteria": ["impact", "cost", "risk"],
        "candidate_scores": _scores(names),
        "chosen_action": "pursue " + chosen,
        "chosen_rationale": "best scored option is " + chosen,
        "rejected": rejected,
        "prediction": prediction,
        "actual_outcome": {"note": "completed offline analysis for " + chosen},
        "prediction_vs_actual": "matched: analysis produced as predicted",
        "lesson": lesson,
    }
    if observed_note is not None:
        plan["observed_note"] = observed_note
    return plan


# --------------------------------------------------------------------------- #
# The 3-cycle policy. Cycle 1 and cycle 2 choose candidate A by default. Cycle
# 3 inspects the history (the prior finalized lessons the runner passes in): if
# cycle 2's lesson is present and says to prefer B, cycle 3 CHANGES its choice
# to B. With no such lesson in history, the default choice stays A. This is the
# mechanism proving "each decision is the basis of the next".
# --------------------------------------------------------------------------- #
CANDS = ["alpha", "bravo", "charlie"]
PREFER_B_LESSON = "prefer bravo over alpha because bravo compounded returns"


def _three_cycle_policy(*, observation, target_text, history):
    n = len(history)  # number of prior finalized lessons carried in
    if n == 0:
        # Cycle 1: no priors; records the first lesson.
        return _plan(domain="growth", chosen="alpha", names=CANDS,
                     lesson="alpha is a reasonable baseline first move",
                     observed_note="first cycle: none available")
    if n == 1:
        # Cycle 2: records the lesson that should steer the NEXT decision.
        return _plan(domain="growth", chosen="alpha", names=CANDS,
                     lesson=PREFER_B_LESSON)
    # Cycle 3: CHANGE the choice iff cycle 2's steering lesson is in history.
    steered = any(e.get("lesson") == PREFER_B_LESSON for e in history)
    chosen = "bravo" if steered else "alpha"
    return _plan(domain="growth", chosen=chosen, names=CANDS,
                 lesson="applied prior lesson; chose " + chosen)


class DecisionCycleChainTests(unittest.TestCase):
    def test_fake_planner_conforms_to_planner_protocol(self):
        fake = FakePlanner(_three_cycle_policy)
        self.assertIsInstance(fake, Planner)
        self.assertEqual(fake.provider, "meta")
        self.assertTrue(fake.model_id)

    def test_three_cycle_chain_cycle3_changes_due_to_cycle2_lesson(self):
        with tempfile.TemporaryDirectory() as decisions_dir:
            fake = FakePlanner(_three_cycle_policy)
            clock = StrictClock()
            summaries = [
                run_one_cycle(decisions_dir, planner=fake,
                              now_iso_provider=clock)
                for _ in range(3)
            ]

            # Three finalized records D-0001..D-0003 exist on disk.
            records = load_records(decisions_dir)
            self.assertEqual([r["id"] for r in records],
                             ["D-0001", "D-0002", "D-0003"])
            for record in records:
                self.assertEqual(record["status"], STATUS_FINALIZED)

            # Cycle 1 and cycle 2 chose alpha; cycle 3 CHANGED to bravo.
            self.assertEqual(summaries[0]["id"], "D-0001")
            self.assertEqual(records[0]["chosen_action"], "pursue alpha")
            self.assertEqual(records[1]["chosen_action"], "pursue alpha")
            self.assertEqual(records[2]["chosen_action"], "pursue bravo")
            self.assertNotEqual(records[2]["chosen_action"],
                                records[1]["chosen_action"])

            # The change is attributable to cycle 2's recorded lesson: cycle 3
            # cites D-0002's verbatim lesson in lessons_applied.
            applied = summaries[2]["lessons_applied"]
            self.assertEqual(applied,
                             [{"decision_id": "D-0002", "lesson": PREFER_B_LESSON}])
            self.assertEqual(records[2]["lessons_applied"], applied)

    def test_control_without_cycle2_lesson_keeps_default_choice(self):
        """Control: with no steering lesson in history, cycle 3 stays on alpha.

        This proves cycle 3's change in the chain test is BECAUSE of cycle 2's
        lesson, not an unconditional switch. Here the planner is driven with an
        EMPTY history (as if no prior lesson existed), so the default (alpha)
        choice is produced.
        """
        self.assertEqual(
            _three_cycle_policy(observation={}, target_text="t", history=())
            ["chosen_action"],
            "pursue alpha",
        )
        # With only cycle 1's (non-steering) lesson, still alpha.
        hist_no_steer = ({"decision_id": "D-0001", "lesson": "unrelated"},)
        self.assertEqual(
            _three_cycle_policy(observation={}, target_text="t",
                                history=hist_no_steer)["chosen_action"],
            "pursue alpha",
        )
        # With cycle 2's steering lesson present, the choice becomes bravo.
        hist_steer = (
            {"decision_id": "D-0001", "lesson": "unrelated"},
            {"decision_id": "D-0002", "lesson": PREFER_B_LESSON},
        )
        self.assertEqual(
            _three_cycle_policy(observation={}, target_text="t",
                                history=hist_steer)["chosen_action"],
            "pursue bravo",
        )


class OrderingTests(unittest.TestCase):
    def test_prediction_written_before_action_and_finalized_after(self):
        """The PREDICTED D-NNNN.json exists on disk before the artifact.

        The planner's choose() fires before create_record, so we assert the
        filesystem state via an observing planner whose choose records whether
        any D-*.json exists yet (it must not, this is cycle 1), and after the
        cycle we assert ordering by inode/mtime is impossible to invert because
        the artifact only appears after the record and the record is PREDICTED
        at creation.
        """
        with tempfile.TemporaryDirectory() as decisions_dir:
            dpath = Path(decisions_dir)
            record_path = dpath / "D-0001.json"
            # R2-S6: the fake plan's chosen_action ("pursue alpha") maps to no
            # known analysis keyword, so the executor falls back to the safe
            # default (inventory) and writes inventory.json.
            artifact_path = dpath / "artifacts" / "D-0001" / "inventory.json"

            observed = {}

            class ObservingPlanner(FakePlanner):
                def choose(self, *, observation, target_text, history):
                    # At plan time nothing is written yet (cycle 1, no priors).
                    observed["record_before_plan"] = record_path.exists()
                    observed["artifact_before_plan"] = artifact_path.exists()
                    return super().choose(observation=observation,
                                          target_text=target_text,
                                          history=history)

            fake = ObservingPlanner(_three_cycle_policy)
            run_one_cycle(decisions_dir, planner=fake, now_iso_provider=StrictClock())

            # Nothing existed at plan time.
            self.assertFalse(observed["record_before_plan"])
            self.assertFalse(observed["artifact_before_plan"])

            # After the cycle both exist; the record is FINALIZED and the
            # artifact lives under decisions/artifacts/D-0001/.
            self.assertTrue(record_path.is_file())
            self.assertTrue(artifact_path.is_file())
            final = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(final["status"], STATUS_FINALIZED)

            # Ordering proof via timestamps: the prediction (predicted_at) is
            # sampled before the outcome (outcome_at), and the record is created
            # (hence the file written PREDICTED) before the artifact is written.
            self.assertLess(final["predicted_at"], final["outcome_at"])

    def test_record_file_predates_artifact_on_disk(self):
        """The PREDICTED record is committed to disk before the action runs.

        This proves the ordering deterministically (no sleeps, no filesystem
        mtime). The runner samples outcome_at via ``now_iso_provider`` exactly
        once AFTER the artifact has been written and BEFORE ``finalize_record``
        rewrites the record. An ObservingClock reads the on-disk record and the
        artifact at that precise instant: the artifact already exists while the
        record file still carries status=PREDICTED with a null actual_outcome.
        So the prediction was committed to disk before the action's artifact,
        and finalize had not yet run when the action completed.
        """
        with tempfile.TemporaryDirectory() as decisions_dir:
            dpath = Path(decisions_dir)
            record_path = dpath / "D-0001.json"
            # R2-S6: see note above; fallback analysis writes inventory.json.
            artifact_path = dpath / "artifacts" / "D-0001" / "inventory.json"

            observations = []

            class ObservingClock(StrictClock):
                """Strictly-increasing clock that records the mid-cycle disk
                state at each sample. The runner calls it for predicted_at
                (before create_record) and for outcome_at (after the artifact
                exists, before finalize). We capture a parsed snapshot of the
                record file (if any) and whether the artifact exists."""

                def __call__(self):
                    snapshot = None
                    if record_path.is_file():
                        snapshot = json.loads(
                            record_path.read_text(encoding="utf-8"))
                    observations.append({
                        "record": snapshot,
                        "artifact_exists": artifact_path.is_file(),
                    })
                    return super().__call__()

            fake = FakePlanner(_three_cycle_policy)
            summary = run_one_cycle(decisions_dir, planner=fake,
                                    now_iso_provider=ObservingClock())

            # The clock was sampled exactly twice (predicted_at, outcome_at).
            self.assertEqual(len(observations), 2)

            # First sample (predicted_at): taken before create_record, so no
            # record and no artifact exist yet.
            self.assertIsNone(observations[0]["record"])
            self.assertFalse(observations[0]["artifact_exists"])

            # Second sample (outcome_at): taken AFTER the action wrote the
            # artifact but BEFORE finalize. The artifact already exists, and the
            # on-disk record is still the PREDICTED prediction with a null
            # actual_outcome. This is the deterministic ordering proof: the
            # record file was written before the artifact, and the prediction
            # had not been finalized when the action completed.
            self.assertTrue(observations[1]["artifact_exists"])
            mid_record = observations[1]["record"]
            self.assertIsNotNone(mid_record)
            self.assertEqual(mid_record["status"], STATUS_PREDICTED)
            self.assertIsNone(mid_record["actual_outcome"])

            # After the cycle both files exist and the record is FINALIZED.
            self.assertTrue(record_path.is_file())
            self.assertTrue(artifact_path.is_file())
            self.assertEqual(Path(summary["artifact_path"]), artifact_path)
            final = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(final["status"], STATUS_FINALIZED)
            self.assertIsNotNone(final["actual_outcome"])


class CitationTests(unittest.TestCase):
    def test_first_cycle_cites_none_available_and_validates(self):
        with tempfile.TemporaryDirectory() as decisions_dir:
            fake = FakePlanner(_three_cycle_policy)
            summary = run_one_cycle(decisions_dir, planner=fake,
                                    now_iso_provider=StrictClock())
            self.assertEqual(summary["lessons_applied"], [])
            record = load_records(decisions_dir)[0]
            self.assertEqual(record["lessons_applied"], [])
            # The "none available" intent is visible in the record.
            blob = json.dumps(record)
            self.assertIn("none available", blob)
            # D-0001 (no priors) validates despite citing no lessons.
            self.assertTrue(
                validate_record(record, prior_records=[]).valid)

    def test_available_prior_lesson_not_cited_is_rejected(self):
        """A later record with an available finalized lesson but no citation
        is rejected with reason 'available_prior_lessons_not_cited'."""
        with tempfile.TemporaryDirectory() as decisions_dir:
            fake = FakePlanner(_three_cycle_policy)
            run_one_cycle(decisions_dir, planner=fake,
                          now_iso_provider=StrictClock())  # D-0001 finalized
            prior = load_records(decisions_dir)
            # Hand-build a D-0002 that cites nothing despite an available lesson.
            bad = dict(prior[0])
            bad["id"] = "D-0002"
            bad["prior_decision_ids_read"] = ["D-0001"]
            bad["lessons_applied"] = []
            result = validate_record(bad, prior_records=prior)
            self.assertFalse(result.valid)
            self.assertEqual(result.reason, "available_prior_lessons_not_cited")


class LiveGateTests(unittest.TestCase):
    def test_refuses_when_api_key_absent_no_call(self):
        status = live_gate_status({}, "some-real-model-id")
        self.assertFalse(status.permitted)
        self.assertIn("META_API_KEY", status.reason)

    def test_refuses_when_model_is_placeholder(self):
        # Key present but model id is the unconfirmed placeholder -> refuse.
        env = {meta_planner._API_KEY_ENV: _fake_meta_key()}
        status = live_gate_status(env, meta_planner.META_MODEL_ID)
        self.assertFalse(status.permitted)
        self.assertIn("placeholder", status.reason)

    def test_script_main_prints_live_gated_and_exits_zero_no_call(self):
        """The CLI refuses live in this sandbox: a LIVE-GATED line, exit 0, and
        NO planner call (nothing in the script constructs/calls a planner)."""
        import io
        import contextlib
        from scripts import run_decision_cycle

        # Ensure the env lacks the key and the model id is the placeholder.
        saved = {k: os.environ.get(k) for k in
                 (meta_planner._API_KEY_ENV, meta_planner._MODEL_ID_ENV)}
        try:
            os.environ.pop(meta_planner._API_KEY_ENV, None)
            os.environ.pop(meta_planner._MODEL_ID_ENV, None)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_decision_cycle.main([])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertTrue(out.startswith(LIVE_GATED_TOKEN),
                            "output must begin with the LIVE-GATED token")
            self.assertIn("META_API_KEY", out)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_satisfied_gate_would_proceed_with_fake_transport(self):
        """Key present + non-placeholder id + authorizing budget -> the live
        precondition PASSES (so a real run WOULD proceed). No network occurs:
        we only exercise the precondition check, with a fake-shaped key that is
        never persisted."""
        env = {meta_planner._API_KEY_ENV: _fake_meta_key()}
        budget_inputs = {
            "worst_case_cost_usd": 0.02,
            "lifetime_credit_spent_usd": 0.0,
            "round_spent_usd": 0.0,
            # Strictly before CREDIT_EXPIRY_ISO so credits are still valid.
            "now_iso": "2026-11-01T00:00:00Z",
        }
        status = live_gate_status(env, "fake-muse-spark-001",
                                  budget_inputs=budget_inputs)
        self.assertTrue(status.permitted, status.reason)
        self.assertIsInstance(status, LiveGateStatus)

    def test_satisfied_gate_rejects_once_credit_expired(self):
        """Same inputs but at/after CREDIT_EXPIRY_ISO -> the budget refuses."""
        env = {meta_planner._API_KEY_ENV: _fake_meta_key()}
        budget_inputs = {
            "worst_case_cost_usd": 0.02,
            "lifetime_credit_spent_usd": 0.0,
            "now_iso": credit_budget.CREDIT_EXPIRY_ISO,
        }
        status = live_gate_status(env, "fake-muse-spark-001",
                                  budget_inputs=budget_inputs)
        self.assertFalse(status.permitted)
        self.assertIn("credit budget refused", status.reason)


class VerifierRejectionTests(unittest.TestCase):
    """Verifier rejection evidence (scope item 5)."""

    def _finalized_prior(self, decisions_dir):
        fake = FakePlanner(_three_cycle_policy)
        run_one_cycle(decisions_dir, planner=fake, now_iso_provider=StrictClock())
        return load_records(decisions_dir)

    def test_rejects_lesson_for_nonexistent_decision_id(self):
        with tempfile.TemporaryDirectory() as decisions_dir:
            prior = self._finalized_prior(decisions_dir)  # only D-0001 exists
            bad = dict(prior[0])
            bad["id"] = "D-0002"
            bad["prior_decision_ids_read"] = ["D-0001"]
            # Cite a lesson for a decision id that is NOT among the priors.
            bad["lessons_applied"] = [
                {"decision_id": "D-0099", "lesson": "nonexistent"}]
            result = validate_record(bad, prior_records=prior)
            self.assertFalse(result.valid)
            # Captured exact reason string (reported as rejection evidence).
            self.assertEqual(result.reason, "lesson_decision_id_not_found")

    def test_rejects_wrong_lesson_text_for_existing_decision(self):
        with tempfile.TemporaryDirectory() as decisions_dir:
            prior = self._finalized_prior(decisions_dir)
            bad = dict(prior[0])
            bad["id"] = "D-0002"
            bad["prior_decision_ids_read"] = ["D-0001"]
            # Cite an existing decision but with WRONG lesson text.
            bad["lessons_applied"] = [
                {"decision_id": "D-0001", "lesson": "this is not the real lesson"}]
            result = validate_record(bad, prior_records=prior)
            self.assertFalse(result.valid)
            self.assertEqual(result.reason, "lesson_text_mismatch")


class TimestampVerifierTests(unittest.TestCase):
    """Additive outcome-precedes-prediction check + backward compatibility."""

    def _d1(self, decisions_dir, *, predicted_at=None):
        return create_record(
            decisions_dir,
            observed_state={"note": "s"},
            prior_decision_ids_read=[],
            candidates=_candidates(CANDS),
            scoring_criteria=["impact"],
            candidate_scores=_scores(CANDS),
            chosen_action="pursue alpha",
            chosen_rationale="baseline",
            rejected=[{"candidate": "bravo", "why": "x"},
                      {"candidate": "charlie", "why": "y"}],
            prediction="will work",
            lessons_applied=[],
            predicted_at=predicted_at,
        )

    def test_outcome_before_prediction_is_rejected(self):
        with tempfile.TemporaryDirectory() as decisions_dir:
            record = self._d1(decisions_dir, predicted_at="2026-11-01T00:05:00Z")
            record = dict(record)
            record["status"] = STATUS_FINALIZED
            record["actual_outcome"] = {"note": "done"}
            record["prediction_vs_actual"] = "matched"
            record["lesson"] = "learned"
            # outcome_at strictly BEFORE predicted_at.
            record["outcome_at"] = "2026-11-01T00:04:00Z"
            result = validate_record(record, prior_records=[])
            self.assertFalse(result.valid)
            self.assertEqual(result.reason, "outcome_precedes_prediction")

    def test_outcome_at_or_after_prediction_validates(self):
        with tempfile.TemporaryDirectory() as decisions_dir:
            record = self._d1(decisions_dir, predicted_at="2026-11-01T00:05:00Z")
            record = dict(record)
            record["status"] = STATUS_FINALIZED
            record["actual_outcome"] = {"note": "done"}
            record["prediction_vs_actual"] = "matched"
            record["lesson"] = "learned"
            record["outcome_at"] = "2026-11-01T00:06:00Z"
            self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_record_without_timestamps_still_validates(self):
        """Backward compat: a record omitting both timestamps validates."""
        with tempfile.TemporaryDirectory() as decisions_dir:
            record = self._d1(decisions_dir)  # no predicted_at
            self.assertNotIn("predicted_at", record)
            self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_predicted_at_immutable_across_finalize(self):
        from spray_schema.decision_record import finalize_record
        with tempfile.TemporaryDirectory() as decisions_dir:
            self._d1(decisions_dir, predicted_at="2026-11-01T00:05:00Z")
            finalized = finalize_record(
                decisions_dir, "D-0001",
                actual_outcome={"note": "done"},
                prediction_vs_actual="matched",
                lesson="learned",
                outcome_at="2026-11-01T00:06:00Z")
            self.assertEqual(finalized["predicted_at"], "2026-11-01T00:05:00Z")
            self.assertEqual(finalized["outcome_at"], "2026-11-01T00:06:00Z")


class MeasuredOutcomeTests(unittest.TestCase):
    """R2-S6: the recorded outcome is the EXECUTOR's measurement, not the plan.

    The runner analyzes a SMALL temp repo_root (not the real SPRAY tree) so the
    counts are known and the test is fast and isolated.
    """

    def _seed_repo(self, root: Path):
        (root / "a.py").write_text("x = 1\n# TODO fix me\n", encoding="utf-8")
        (root / "b.txt").write_text("hello\nworld\n", encoding="utf-8")

    def test_recorded_outcome_is_measured_not_model_asserted(self):
        import hashlib
        from spray_schema.decision_record import validate_record
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_repo(repo_root)
            fake = FakePlanner(_three_cycle_policy)
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root)

            record = load_records(decisions_dir)[0]
            outcome = record["actual_outcome"]
            # The outcome is executor-measured, NOT the fake plan's asserted
            # text ("completed offline analysis for alpha").
            self.assertTrue(outcome["measured"])
            self.assertNotIn("completed offline analysis", json.dumps(outcome))
            # "pursue alpha" maps to no keyword -> safe-default inventory.
            self.assertEqual(outcome["analysis"], "inventory")
            self.assertTrue(outcome["fallback"])

            # The recorded artifact exists with a matching sha256.
            artifact_abs = Path(decisions_dir) / outcome["artifact_path"]
            self.assertTrue(artifact_abs.is_file())
            self.assertGreater(outcome["artifact_bytes"], 0)
            self.assertEqual(
                outcome["artifact_sha256"],
                hashlib.sha256(artifact_abs.read_bytes()).hexdigest())
            self.assertEqual(record["artifact_path"], outcome["artifact_path"])
            self.assertEqual(record["artifact_sha256"],
                             outcome["artifact_sha256"])
            # The measured inventory counted exactly the two seeded files.
            self.assertEqual(outcome["counts"]["total_files"], 2)

            # Re-validate the finalized record with the decisions dir as the
            # artifact resolution root: it is valid (file present, sha matches).
            self.assertTrue(
                validate_record(
                    record, prior_records=[],
                    artifacts_resolution_root=decisions_dir).valid)

    def test_prediction_vs_actual_is_mechanical_not_model_text(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_repo(repo_root)

            def policy(*, observation, target_text, history):
                # A prediction with a numeric range the executor can check.
                return _plan(domain="growth", chosen="inventory of files",
                             names=CANDS,
                             lesson="baseline",
                             prediction="expect between 1 and 5 files",
                             observed_note="first")

            fake = FakePlanner(policy)
            run_one_cycle(decisions_dir, planner=fake,
                          now_iso_provider=StrictClock(), repo_root=repo_root)
            record = load_records(decisions_dir)[0]
            pva = record["prediction_vs_actual"]
            # Mechanical verdict, not the fake plan's "matched: ..." text.
            self.assertIn("artifact produced as predicted", pva)
            self.assertIn("within the predicted range", pva)
            self.assertNotIn("matched: analysis produced as predicted", pva)
            # "inventory of files" maps to the inventory analysis (no fallback).
            self.assertEqual(record["actual_outcome"]["analysis"], "inventory")
            self.assertFalse(record["actual_outcome"]["fallback"])


class MechanicalPredictionTests(unittest.TestCase):
    """R2-S6 review issue 1: the mechanical prediction_vs_actual must only emit
    a within/outside verdict for an EXPLICIT labeled numeric bound tied to the
    measured quantity, and must say "no numeric claim" for prose full of
    incidental integers. These exercise the helper directly (no network)."""

    def _measured(self, analysis, **counts):
        return {"analysis": analysis, "counts": dict(counts)}

    def test_explicit_upper_bound_reports_within(self):
        measured = self._measured("markers", total_markers=42)
        pva = decision_cycle._mechanical_prediction_vs_actual(
            "expect under 50 markers in the scan", measured)
        self.assertIn("within", pva)
        self.assertIn("under 50", pva)
        self.assertNotIn("no numeric claim", pva)

    def test_explicit_upper_bound_reports_outside(self):
        measured = self._measured("markers", total_markers=70)
        pva = decision_cycle._mechanical_prediction_vs_actual(
            "predict fewer than 50 markers", measured)
        self.assertIn("outside", pva)
        self.assertIn("fewer than 50", pva)

    def test_explicit_lower_bound_reports_within(self):
        measured = self._measured("total", total_files=12)
        # total_files headline; "at least 3 files" is tied to files.
        pva = decision_cycle._mechanical_prediction_vs_actual(
            "there should be at least 3 files", measured)
        self.assertIn("within", pva)
        self.assertIn("at least 3", pva)

    def test_incidental_integers_yield_no_numeric_claim(self):
        """The D-0004-style prediction: prose full of incidental integers
        ("0 network calls, 0 cash, under 60s") about side effects and TIME, not
        about the line count. The old harvest-all-integers behavior built a
        bogus [0, 60] range and declared the 24807 line count "outside"; the
        fix must report NO numeric claim instead."""
        measured = self._measured("stats", total_lines=24807)
        prediction = (
            "Artifact analysis/linecount_hotspots.md will be created locally "
            "with total counts and top 20 list, with 0 network calls, 0 cash "
            "spent, 0 external side effects in under 60s")
        pva = decision_cycle._mechanical_prediction_vs_actual(
            prediction, measured)
        self.assertIn("no numeric claim in the prediction to verify", pva)
        # The bogus range and spurious verdict must be gone.
        self.assertNotIn("[0, 60]", pva)
        self.assertNotIn("outside", pva)
        self.assertNotIn("within", pva)

    def test_bound_for_unrelated_quantity_is_not_graded(self):
        """"under 60s" (a TIME bound) must not be graded against a LINE count:
        "s"/"seconds" is not a line/byte noun, so there is no checkable claim
        about the measured quantity."""
        measured = self._measured("stats", total_lines=24807)
        pva = decision_cycle._mechanical_prediction_vs_actual(
            "will finish in under 60 seconds", measured)
        self.assertIn("no numeric claim in the prediction to verify", pva)

    def test_between_range_tied_to_count(self):
        measured = self._measured("stats", total_lines=30)
        pva = decision_cycle._mechanical_prediction_vs_actual(
            "expect between 10 and 100 lines of output", measured)
        self.assertIn("within", pva)
        self.assertIn("between 10 and 100", pva)


class HonestVerdictTests(unittest.TestCase):
    """R2-S7: fallback OR executed-type-mismatch is an honest MISS; a faithful
    supported run with an explicit tied numeric bound is within/outside HIT;
    a faithful run with no falsifiable claim is honestly reported."""

    def _seed_dup_tree(self, root: Path):
        # Two files sharing a basename -> the duplicates analysis runs for real.
        (root / "sub").mkdir()
        (root / "config.py").write_text("X = 1\n", encoding="utf-8")
        (root / "sub" / "config.py").write_text("X = 1\n", encoding="utf-8")
        (root / "other.py").write_text("Y = 2\n", encoding="utf-8")

    def test_fallback_run_is_graded_miss(self):
        """A chosen action that maps to no analysis falls back; the cycle must
        grade it MISS (string starts 'MISS') and set outcome_verdict MISS."""
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            # "pursue alpha" maps to no analysis keyword -> fallback inventory.
            fake = FakePlanner(_three_cycle_policy)
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root)
            self.assertEqual(summary["outcome_verdict"], "MISS")
            self.assertTrue(summary["prediction_vs_actual"].startswith("MISS"))
            self.assertIn("fell back", summary["prediction_vs_actual"])
            self.assertNotIn("produced as predicted",
                             summary["prediction_vs_actual"])
            record = load_records(decisions_dir)[0]
            self.assertEqual(record["outcome_verdict"], "MISS")

    def test_executed_type_mismatch_is_miss_via_grader(self):
        """When the executor ran a DIFFERENT analysis than the chosen action
        intended, the grader reports MISS even with fallback False."""
        measured = {"analysis": "inventory", "fallback": False,
                    "counts": {"total_files": 3}}
        pva = decision_cycle._mechanical_prediction_vs_actual(
            "expect a few files", measured, intended_analysis="duplicates")
        self.assertTrue(pva.startswith("MISS"))
        self.assertIn("intended duplicates", pva)
        self.assertEqual(
            decision_cycle._outcome_verdict(measured, "duplicates"), "MISS")

    def test_faithful_supported_run_with_bound_is_hit_within(self):
        """A faithful duplicates run whose prediction carries an explicit bound
        tied to the headline count grades within/outside and verdict HIT."""
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)

            def policy(*, observation, target_text, history):
                return _plan(
                    domain="repo quality",
                    chosen="duplicate filename triage",
                    names=CANDS,
                    lesson="duplicates triage is a good offline move",
                    prediction="expect at most 3 duplicate groups",
                    observed_note="first")

            fake = FakePlanner(policy)
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root)
            self.assertEqual(summary["analysis"], "duplicates")
            self.assertFalse(summary["fallback"])
            self.assertEqual(summary["outcome_verdict"], "HIT")
            pva = summary["prediction_vs_actual"]
            self.assertIn("artifact produced as predicted", pva)
            self.assertIn("within", pva)
            self.assertNotIn("MISS", pva)

    def test_faithful_run_no_numeric_claim_is_honest(self):
        """A faithful supported run with NO falsifiable numeric claim is
        recorded honestly (no fabricated numeric pass) and verdict HIT."""
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)

            def policy(*, observation, target_text, history):
                return _plan(
                    domain="repo quality",
                    chosen="duplicate filename triage",
                    names=CANDS,
                    lesson="duplicates triage is a good offline move",
                    prediction="will produce a local duplicates artifact",
                    observed_note="first")

            fake = FakePlanner(policy)
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root)
            self.assertEqual(summary["analysis"], "duplicates")
            self.assertFalse(summary["fallback"])
            self.assertEqual(summary["outcome_verdict"], "HIT")
            pva = summary["prediction_vs_actual"]
            self.assertIn("no numeric claim in the prediction to verify", pva)
            self.assertNotIn("MISS", pva)
            self.assertNotIn("within", pva)
            self.assertNotIn("outside", pva)


class FalsifiableCycleTests(unittest.TestCase):
    """R2-S8: a falsifiable cycle records a REAL numeric_error computed from the
    executor's measured value and a prediction_vs_actual stating that error (it
    never says 'no numeric claim' for such a cycle). Within-range and over-range
    cases are both exercised; a follow-up on a prior trigger is injected and a
    conforming followup_decision is recorded. Entirely offline (temp repo)."""

    def _seed_dup_tree(self, root: Path):
        # Two files sharing a basename -> duplicate_basename_groups == 1.
        (root / "sub").mkdir()
        (root / "config.py").write_text("X = 1\n", encoding="utf-8")
        (root / "sub" / "config.py").write_text("X = 1\n", encoding="utf-8")
        (root / "other.py").write_text("Y = 2\n", encoding="utf-8")

    def _dup_plan(self, *, low, high, **extra):
        plan = _plan(
            domain="repo quality",
            chosen="duplicate filename triage",
            names=CANDS,
            lesson="duplicates triage is a good offline move",
            prediction="expect between %d and %d duplicate groups" % (low, high),
            observed_note="first")
        plan["quantitative_claim"] = {
            "metric_key": "duplicate_basename_groups",
            "kind": "range",
            "predicted_low": low,
            "predicted_high": high,
            "confidence": 0.8,
        }
        plan.update(extra)
        return plan

    def test_within_range_numeric_error_recorded(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            fake = FakePlanner(lambda **kw: self._dup_plan(low=1, high=3))
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root, falsifiable=True)

            record = load_records(decisions_dir)[0]
            self.assertIs(record["falsifiable"], True)
            # The committed claim is immutable and present.
            self.assertEqual(
                record["quantitative_claim"]["metric_key"],
                "duplicate_basename_groups")
            ne = record["numeric_error"]
            self.assertEqual(ne["metric_key"], "duplicate_basename_groups")
            # The real measured value is exactly 1 duplicate group.
            self.assertEqual(ne["measured"], 1)
            self.assertEqual(ne["predicted"], [1, 3])
            self.assertEqual(ne["absolute_error"], 0)
            self.assertEqual(ne["relative_error"], 0.0)
            self.assertTrue(ne["within_range"])
            # prediction_vs_actual states the real error, never 'no numeric claim'.
            pva = record["prediction_vs_actual"]
            self.assertIn("predicted range [1, 3]", pva)
            self.assertIn("measured 1", pva)
            self.assertIn("within", pva)
            self.assertNotIn("no numeric claim", pva)
            self.assertTrue(
                validate_record(
                    record, prior_records=[],
                    artifacts_resolution_root=decisions_dir).valid)

    def test_over_range_numeric_error_recorded(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            # Predict 5..9 groups but the truth is 1 -> over-range (below low).
            fake = FakePlanner(lambda **kw: self._dup_plan(low=5, high=9))
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root, falsifiable=True)
            ne = summary["numeric_error"]
            self.assertEqual(ne["measured"], 1)
            self.assertFalse(ne["within_range"])
            # Distance to the nearest range edge (low=5) is 4.
            self.assertEqual(ne["absolute_error"], 4)
            self.assertEqual(ne["relative_error"], 4.0)
            pva = summary["prediction_vs_actual"]
            self.assertIn("outside", pva)
            self.assertNotIn("no numeric claim", pva)

    def test_single_value_claim_numeric_error(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)

            def policy(**kw):
                plan = self._dup_plan(low=1, high=1)
                plan["quantitative_claim"] = {
                    "metric_key": "duplicate_basename_groups",
                    "kind": "point", "predicted_value": 3, "confidence": 0.5}
                return plan

            fake = FakePlanner(policy)
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root, falsifiable=True)
            ne = summary["numeric_error"]
            self.assertEqual(ne["predicted"], 3)
            self.assertEqual(ne["measured"], 1)
            self.assertEqual(ne["absolute_error"], 2)
            self.assertFalse(ne["within_range"])

    def test_falsifiable_without_claim_raises(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            # A plan with NO quantitative_claim on a falsifiable cycle fails
            # closed before anything is written.
            fake = FakePlanner(lambda **kw: _plan(
                domain="x", chosen="duplicate triage", names=CANDS,
                lesson="l", prediction="p"))
            with self.assertRaises(ValueError) as ctx:
                run_one_cycle(decisions_dir, planner=fake,
                              now_iso_provider=StrictClock(),
                              repo_root=repo_root, falsifiable=True)
            self.assertIn("quantitative_claim", str(ctx.exception))

    def test_non_falsifiable_cycle_keeps_prior_shape(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            fake = FakePlanner(_three_cycle_policy)
            run_one_cycle(decisions_dir, planner=fake,
                          now_iso_provider=StrictClock(), repo_root=repo_root)
            record = load_records(decisions_dir)[0]
            self.assertNotIn("falsifiable", record)
            self.assertNotIn("quantitative_claim", record)
            self.assertNotIn("numeric_error", record)

    def test_followup_injected_and_recorded_for_prior_trigger(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            clock = StrictClock()

            # Cycle 1: over-range prediction so the finalized record carries a
            # numeric_error with within_range False -> a trigger for cycle 2.
            fake1 = FakePlanner(lambda **kw: self._dup_plan(low=5, high=9))
            run_one_cycle(decisions_dir, planner=fake1,
                          now_iso_provider=clock, repo_root=repo_root,
                          falsifiable=True)
            first = load_records(decisions_dir)[0]
            self.assertFalse(first["numeric_error"]["within_range"])

            # Cycle 2: must address the prior trigger. The planner cites the
            # missed lesson (the runner handles that) and supplies a claim.
            def policy2(*, observation, target_text, history):
                plan = self._dup_plan(low=1, high=3)
                # Cite the prior MISS-style lesson so miss/citation rules pass.
                return plan

            fake2 = FakePlanner(policy2)
            summary2 = run_one_cycle(decisions_dir, planner=fake2,
                                     now_iso_provider=clock, repo_root=repo_root,
                                     falsifiable=True)
            second = load_records(decisions_dir)[1]
            # A conforming followup_decision citing D-0001 was recorded.
            self.assertEqual(summary2["id"], "D-0002")
            followup = second["followup_decision"]
            self.assertEqual(followup["prior_decision_id"], "D-0001")
            self.assertTrue(followup["why"])
            self.assertIn("chose", followup)
            self.assertTrue(
                validate_record(
                    second,
                    prior_records=[first],
                    artifacts_resolution_root=decisions_dir).valid)


class EnrichedHistoryTests(unittest.TestCase):
    """R2-S8: _augment_history_with_outcomes carries prior numeric_error summary
    and findings (redacted) in addition to lesson + HIT/MISS verdict."""

    def test_history_carries_numeric_error_and_findings(self):
        prior_records = [{
            "id": "D-0001",
            "status": STATUS_FINALIZED,
            "lesson": "a measured lesson",
            "outcome_verdict": "MISS",
            "prediction_vs_actual": "quantitative claim on total_files: ...",
            "numeric_error": {
                "metric_key": "total_files", "predicted": [1, 2],
                "measured": 50, "within_range": False},
            "findings": ["undocumented=12", "edges=5"],
        }]
        prior_lessons = [{"decision_id": "D-0001", "lesson": "a measured lesson"}]
        augmented = decision_cycle._augment_history_with_outcomes(
            prior_lessons, prior_records)
        entry = augmented[0]
        self.assertEqual(entry["outcome_verdict"], "MISS")
        self.assertEqual(entry["numeric_error"]["metric_key"], "total_files")
        self.assertEqual(entry["numeric_error"]["measured"], 50)
        self.assertFalse(entry["numeric_error"]["within_range"])
        self.assertEqual(entry["findings"], ["undocumented=12", "edges=5"])

    def test_history_without_s8_fields_unchanged(self):
        prior_records = [{
            "id": "D-0001", "status": STATUS_FINALIZED,
            "lesson": "a lesson", "outcome_verdict": "HIT",
            "prediction_vs_actual": "graded",
        }]
        prior_lessons = [{"decision_id": "D-0001", "lesson": "a lesson"}]
        entry = decision_cycle._augment_history_with_outcomes(
            prior_lessons, prior_records)[0]
        self.assertNotIn("numeric_error", entry)
        self.assertNotIn("findings", entry)


class RequireExecutedMatchesChosenTests(unittest.TestCase):
    """R2-S9: run_one_cycle(require_executed_matches_chosen=True) refuses a
    fallback choice at create (raising before anything is written) and otherwise
    writes a FINALIZED record carrying executed_matches_chosen=True with
    fallback=false, executed==intended and an HIT verdict. Entirely offline."""

    def _seed_dup_tree(self, root: Path):
        (root / "sub").mkdir()
        (root / "config.py").write_text("X = 1\n", encoding="utf-8")
        (root / "sub" / "config.py").write_text("X = 1\n", encoding="utf-8")
        (root / "other.py").write_text("Y = 2\n", encoding="utf-8")

    def test_fallback_choice_raises_before_writing(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            # 'dead-export audit' maps to the inventory fallback.
            fake = FakePlanner(lambda **kw: _plan(
                domain="repo quality", chosen="dead-export audit", names=CANDS,
                lesson="l", prediction="p"))
            with self.assertRaises(ValueError) as ctx:
                run_one_cycle(
                    decisions_dir, planner=fake,
                    now_iso_provider=StrictClock(), repo_root=repo_root,
                    require_executed_matches_chosen=True)
            self.assertIn("fall back", str(ctx.exception))
            # Nothing was written: no record on disk.
            self.assertEqual(load_records(decisions_dir), [])

    def test_supported_choice_writes_marked_hit_record(self):
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            # A duplicate-filename triage maps to the 'duplicates' analysis and
            # a correct range (truth is 1 group) makes it a faithful HIT.
            def policy(**kw):
                plan = _plan(
                    domain="repo quality",
                    chosen="duplicate filename triage",
                    names=CANDS,
                    lesson="duplicates triage is a good offline move",
                    prediction="expect between 1 and 3 duplicate groups",
                    observed_note="first")
                plan["quantitative_claim"] = {
                    "metric_key": "duplicate_basename_groups",
                    "kind": "range", "predicted_low": 1, "predicted_high": 3,
                    "confidence": 0.8}
                return plan

            fake = FakePlanner(policy)
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root, falsifiable=True,
                require_executed_matches_chosen=True)

            self.assertIs(summary["executed_matches_chosen"], True)
            self.assertFalse(summary["fallback"])
            self.assertEqual(summary["analysis"], "duplicates")
            self.assertEqual(summary["outcome_verdict"], "HIT")

            record = load_records(decisions_dir)[0]
            self.assertIs(record["executed_matches_chosen"], True)
            self.assertEqual(record["outcome_verdict"], "HIT")
            self.assertTrue(
                validate_record(
                    record, prior_records=[],
                    artifacts_resolution_root=decisions_dir).valid)

    def test_default_cycle_omits_marker(self):
        # Without the opt-in the record carries no S9 marker (prior shape).
        with tempfile.TemporaryDirectory() as decisions_dir, \
                tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            self._seed_dup_tree(repo_root)
            fake = FakePlanner(_three_cycle_policy)
            summary = run_one_cycle(
                decisions_dir, planner=fake, now_iso_provider=StrictClock(),
                repo_root=repo_root)
            self.assertIsNone(summary["executed_matches_chosen"])
            record = load_records(decisions_dir)[0]
            self.assertNotIn("executed_matches_chosen", record)


def _fake_meta_key():
    """A fake Meta API key for the satisfied-live-gate precondition test.

    Assembled from concatenated parts so no credential shape (AWS/JWT/PEM)
    appears on a single source line. This value is used ONLY in-memory for the
    precondition check and is NEVER written to any persisted file.
    """
    return "fake" + "-meta-" + "owner-key-" + "offline-only"


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
