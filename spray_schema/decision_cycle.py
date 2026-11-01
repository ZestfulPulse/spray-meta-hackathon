"""Offline, live-gated decision-cycle runner for SPRAY (pure stdlib).

This module executes ONE self-direction decision cycle and writes a Decision
Record chain so that each decision becomes the basis of the next. It COMPOSES
existing R2-S2/R2-S3 primitives and performs NO network or model I/O itself:

* ``spray_schema.decision_record`` - the two-phase append-only store
  (``create_record`` writes the PREDICTED record BEFORE acting; ``finalize_record``
  fills the outcome AFTER acting). R2-S4 timestamps ``predicted_at`` /
  ``outcome_at`` are passed through its additive optional fields.
* ``spray_schema.provider_planner.Planner`` - the provider-agnostic planner the
  runner asks for a self-generated plan. In tests a FAKE planner conforms by
  exposing ``provider`` / ``model_id`` / ``choose(...)``; no real backend is
  ever constructed here.
* ``spray_schema.model_observation`` - S6 redaction applied to any
  observation-derived content before it is persisted or passed onward.
* ``spray_schema.credit_budget`` + ``spray_schema.meta_planner`` - the live
  gate. Live execution is refused unless ``META_API_KEY`` is present, the model
  id is NOT the placeholder, and ``authorize_meta_spend`` permits; otherwise a
  single ``LIVE-GATED`` line is printed and the runner exits without any call.

Ordering guarantee (scope item 2)
----------------------------------
``run_one_cycle`` calls ``create_record`` FIRST (this writes
``decisions/D-NNNN.json`` to disk with status PREDICTED and null outcome), then
asserts the record file exists on disk, THEN runs the action (produces the
artifact under ``decisions/artifacts/D-NNNN/``), THEN calls ``finalize_record``.
The prediction is therefore committed to disk before anything is acted on.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from . import action_executor
from . import credit_budget
from . import decision_record
from . import meta_planner
from .model_observation import redact, sanitize_observation

# Planner providers whose provenance we are allowed to record. Mirrors
# decision_record.ALLOWED_PROVIDERS; a plan from any other provider is refused.
_ALLOWED_PROVIDERS = decision_record.ALLOWED_PROVIDERS

# The literal token every live-gate refusal line begins with. Callers/tests
# assert on this prefix; the message never contains any secret.
LIVE_GATED_TOKEN = "LIVE-GATED"

# Where per-decision artifacts live (relative to the decisions dir).
_ARTIFACTS_DIRNAME = "artifacts"

# First-cycle citation note recorded in observed_state when no prior lessons
# exist yet, so the "none available" intent is visible in the record.
_NO_PRIOR_LESSONS_NOTE = "none available"


@dataclass(frozen=True)
class LiveGateStatus:
    """Result of the live-gate precondition check (no secrets retained)."""

    permitted: bool
    reason: str


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def live_gate_status(
    env,
    model_id,
    *,
    budget_inputs=None,
    now_iso_provider=None,
) -> LiveGateStatus:
    """Decide whether a LIVE planner call may proceed. Fail closed.

    Live execution is permitted ONLY when ALL hold:

    1. ``META_API_KEY`` is present and non-empty in ``env``.
    2. The resolved ``model_id`` is NOT ``meta_planner.META_MODEL_ID`` (the
       ``'muse-spark-PLACEHOLDER'`` sentinel) and is a non-empty string.
    3. ``credit_budget.authorize_meta_spend`` permits the worst-case spend
       described by ``budget_inputs`` at the sampled ``now_iso``.

    The returned reason explains the FIRST unmet precondition and never
    contains a secret or any untrusted value.
    """
    key = env.get(meta_planner._API_KEY_ENV) if hasattr(env, "get") else None
    if not _is_nonempty_str(key):
        return LiveGateStatus(
            False, "META_API_KEY is absent; cannot run a live cycle")
    if not _is_nonempty_str(model_id):
        return LiveGateStatus(False, "model id is not configured")
    if model_id == meta_planner.META_MODEL_ID:
        return LiveGateStatus(
            False,
            "model id is the unconfirmed placeholder "
            "('%s'); owner must confirm the real id" % meta_planner.META_MODEL_ID)

    inputs = dict(budget_inputs or {})
    if now_iso_provider is not None and "now_iso" not in inputs:
        inputs["now_iso"] = now_iso_provider()
    permitted, reason = credit_budget.authorize_meta_spend(
        worst_case_cost_usd=inputs.get("worst_case_cost_usd"),
        lifetime_credit_spent_usd=inputs.get("lifetime_credit_spent_usd"),
        now_iso=inputs.get("now_iso"),
        round_spent_usd=inputs.get("round_spent_usd", 0),
    )
    if not permitted:
        # authorize_meta_spend reasons are non-sensitive, structured strings.
        return LiveGateStatus(False, "credit budget refused: " + reason)
    return LiveGateStatus(True, "all live-gate preconditions satisfied")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _require_plan_shape(plan: dict) -> None:
    """Validate the self-direction plan the planner returned (fail closed)."""
    _require(type(plan) is dict, "planner plan must be a dict")
    candidates = plan.get("candidates")
    _require(
        type(candidates) is list and len(candidates) >= 3,
        "planner plan must self-generate at least 3 candidates",
    )
    for key in (
        "domain", "chosen_action", "chosen_rationale", "prediction",
        "actual_outcome", "prediction_vs_actual", "lesson",
    ):
        _require(key in plan, "planner plan missing required field: " + key)
    _require(_is_nonempty_str(plan["domain"]), "planner plan domain is empty")
    _require(_is_nonempty_str(plan["lesson"]), "planner plan lesson is empty")
    for key in ("scoring_criteria", "candidate_scores", "rejected"):
        _require(type(plan.get(key)) is list, "planner plan " + key + " must be a list")


def _redact_text(value: object, limit: int = 1000) -> str:
    """S6 redaction for a single untrusted text value (never a secret path)."""
    return redact(value if isinstance(value, str) else "", limit)


def _sanitize_observed_state(raw_observed_state) -> dict:
    """Apply S6 redaction to the observation-derived state before persisting.

    ``browser_observation`` (if present) is passed through
    ``sanitize_observation``; a free-text ``note`` is redacted. Anything the
    planner might derive from untrusted page content is scrubbed here so no
    secret or raw DOM reaches the persisted record.
    """
    state: dict = {}
    if type(raw_observed_state) is dict:
        obs = raw_observed_state.get("browser_observation")
        if type(obs) is dict:
            state["browser_observation"] = sanitize_observation(obs)
        note = raw_observed_state.get("note")
        if note is not None:
            state["note"] = _redact_text(note, 1000)
        domain = raw_observed_state.get("domain")
        if domain is not None:
            state["domain"] = _redact_text(domain, 200)
    return state


def _augment_history_with_outcomes(prior_lessons, prior_records) -> list:
    """Attach each prior's HIT/MISS verdict + short reason to its lesson entry.

    ``prior_lessons`` is the lesson-only structured list from
    ``load_prior_lessons`` ({decision_id, lesson}, oldest first). We look up the
    matching finalized record for each and, when it carries an
    ``outcome_verdict`` and a ``prediction_vs_actual`` string, add a redacted
    ``outcome_verdict`` and a short ``reason`` so the planner sees the OUTCOME.
    Entries whose record has no verdict keep exactly their lesson-only shape, so
    this is fully backward compatible. The lesson text is left byte-identical so
    the runner can still cite the most-recent lesson verbatim.
    """
    by_id = {
        r.get("id"): r for r in prior_records
        if type(r) is dict and _is_nonempty_str(r.get("id"))
    }
    augmented = []
    for entry in prior_lessons:
        item = {"decision_id": entry["decision_id"], "lesson": entry["lesson"]}
        record = by_id.get(entry["decision_id"])
        if type(record) is dict:
            verdict = record.get("outcome_verdict")
            if verdict in ("HIT", "MISS"):
                item["outcome_verdict"] = verdict
            pva = record.get("prediction_vs_actual")
            if _is_nonempty_str(pva):
                item["reason"] = _redact_text(pva, 300)
            # R2-S8 additive: carry a SHORT numeric-error summary and the
            # measured findings so the planner sees the prior numeric error and
            # artifact findings, not just lesson text. Both are optional: a
            # prior without them simply lacks these keys (backward compatible).
            numeric_error = record.get("numeric_error")
            if type(numeric_error) is dict:
                item["numeric_error"] = _numeric_error_summary(numeric_error)
            findings = record.get("findings")
            if type(findings) is list and findings:
                item["findings"] = [
                    _redact_text(f, 120) for f in findings
                    if _is_nonempty_str(f)
                ][:8]
        augmented.append(item)
    return augmented


def _numeric_error_summary(numeric_error: dict) -> dict:
    """Build a compact, safe numeric-error summary for planner history.

    Carries only the measured scalars (predicted echo, measured, within_range)
    so the planner can see how far a prior prediction was from the measured
    truth. No free text and no secret is present in a numeric_error dict.
    """
    return {
        "metric_key": numeric_error.get("metric_key"),
        "predicted": numeric_error.get("predicted"),
        "measured": numeric_error.get("measured"),
        "within_range": numeric_error.get("within_range"),
    }


def _quantitative_claim_from_plan(plan: dict) -> dict:
    """Extract and validate the planner's structured quantitative claim.

    The planner is required (for an S8 falsifiable cycle) to supply a
    ``quantitative_claim`` dict carrying a recognized ``metric_key``, a numeric
    predicted ``predicted_value`` OR a ``predicted_low``/``predicted_high``
    range, a ``kind`` label, and a ``confidence`` in (0, 1]. We validate it
    fail-closed and raise a clear ValueError when it is missing or malformed, so
    the cycle never fabricates a claim on the planner's behalf.
    """
    claim = plan.get("quantitative_claim")
    if type(claim) is not dict:
        raise ValueError(
            "planner plan missing a structured quantitative_claim for an "
            "S8 falsifiable cycle")
    metric_key = claim.get("metric_key")
    if metric_key not in action_executor.RECOGNIZED_METRIC_KEYS:
        raise ValueError(
            "quantitative_claim.metric_key is not a recognized metric")
    confidence = claim.get("confidence")
    if type(confidence) not in (int, float) or type(confidence) is bool \
            or not 0.0 < float(confidence) <= 1.0:
        raise ValueError("quantitative_claim.confidence must be in (0, 1]")
    kind = claim.get("kind")
    if not (isinstance(kind, str) and kind.strip()):
        raise ValueError("quantitative_claim.kind must be a non-empty label")
    has_value = "predicted_value" in claim
    has_range = "predicted_low" in claim or "predicted_high" in claim
    if has_value == has_range:
        raise ValueError(
            "quantitative_claim must carry exactly one of predicted_value or "
            "a predicted_low/predicted_high range")
    built = {
        "metric_key": metric_key,
        "kind": kind,
        "confidence": float(confidence),
    }
    if has_value:
        value = claim.get("predicted_value")
        if type(value) is not int:
            raise ValueError("quantitative_claim.predicted_value must be an int")
        built["predicted_value"] = value
    else:
        low = claim.get("predicted_low")
        high = claim.get("predicted_high")
        if type(low) is not int or type(high) is not int:
            raise ValueError(
                "quantitative_claim predicted_low/high must be ints")
        if low > high:
            low, high = high, low
        built["predicted_low"] = low
        built["predicted_high"] = high
    return built


def _compute_numeric_error(claim: dict, measured: dict) -> dict:
    """Compute the REAL numeric error from the executor's measured value.

    Reads the true measured integer for ``claim['metric_key']`` out of the
    executor's measured counts via ``action_executor.metric_value`` (never a
    model-asserted number), then builds the structured numeric_error:
    ``predicted`` (echo of the value or [low, high] range), ``measured``,
    ``absolute_error`` (distance to the predicted point or nearest range edge),
    ``relative_error`` (absolute_error / measured, or null when measured is 0),
    ``within_range`` (bool), and ``confidence`` (echo).
    """
    metric_key = claim["metric_key"]
    measured_value = action_executor.metric_value(
        measured.get("counts"), metric_key)
    if measured_value is None:
        raise ValueError(
            "executor produced no measured value for metric '%s'" % metric_key)

    if "predicted_value" in claim:
        predicted_echo = claim["predicted_value"]
        absolute_error = abs(measured_value - claim["predicted_value"])
        within_range = measured_value == claim["predicted_value"]
    else:
        low = claim["predicted_low"]
        high = claim["predicted_high"]
        predicted_echo = [low, high]
        within_range = low <= measured_value <= high
        if within_range:
            absolute_error = 0
        elif measured_value < low:
            absolute_error = low - measured_value
        else:
            absolute_error = measured_value - high

    # relative_error is absolute_error / measured and is intentionally UNBOUNDED:
    # a badly over- or under-range prediction can yield a ratio > 1 (e.g. the
    # live D-0013 recorded ~0.835; a single-value test shows 4.0). The validator
    # only requires it to be finite and non-negative, never <= 1.
    relative_error = (absolute_error / measured_value) if measured_value else None
    return {
        "metric_key": metric_key,
        "predicted": predicted_echo,
        "measured": measured_value,
        "absolute_error": absolute_error,
        "relative_error": relative_error,
        "within_range": bool(within_range),
        "confidence": claim["confidence"],
    }


def _numeric_error_phrase(numeric_error: dict) -> str:
    """A short, honest prediction_vs_actual phrase stating the REAL error.

    States predicted vs measured and whether the measured value fell within the
    predicted claim. Never says 'no numeric claim' because an S8 cycle always
    carries a checkable quantitative_claim.
    """
    predicted = numeric_error["predicted"]
    if isinstance(predicted, list):
        predicted_str = "range [%d, %d]" % (predicted[0], predicted[1])
    else:
        predicted_str = "value %d" % predicted
    verdict = "within" if numeric_error["within_range"] else "outside"
    return (
        "quantitative claim on %s: predicted %s, measured %d (%s; "
        "absolute_error=%d)" % (
            numeric_error["metric_key"], predicted_str,
            numeric_error["measured"], verdict,
            numeric_error["absolute_error"]))


def _followup_trigger(prior_records) -> tuple | None:
    """Return (prior_id, outcome_desc) if the most-recent prior FINALIZED record
    carries an S8 trigger (over-range numeric_error OR non-empty findings).

    Mirrors decision_record._followup_trigger_from_prior: a prior without the
    S8 fields never triggers. The returned ``outcome_desc`` is a short,
    non-secret string the injected candidate / followup_decision can reference.
    """
    finalized = [
        r for r in prior_records
        if type(r) is dict and r.get("status") == decision_record.STATUS_FINALIZED
        and _is_nonempty_str(r.get("id"))
    ]
    if not finalized:
        return None
    latest = finalized[-1]
    numeric_error = latest.get("numeric_error")
    if type(numeric_error) is dict and numeric_error.get("within_range") is False:
        return latest["id"], (
            "prior numeric error on %s exceeded its predicted range"
            % numeric_error.get("metric_key", "a metric"))
    findings = latest.get("findings")
    if type(findings) is list and findings:
        return latest["id"], (
            "prior artifact surfaced findings: "
            + _redact_text(", ".join(str(f) for f in findings[:4]), 200))
    return None


def _latest_finalized_measured_prior(prior_records):
    """Return the most-recent FINALIZED prior that carries a measured outcome.

    A measured outcome is a numeric_error dict and/or an outcome_verdict of
    'HIT'/'MISS'. Returns None when no such prior exists (the first cycles in a
    fresh chain), in which case the cycle behaves as a plain S9 cycle with no
    announced_next / rerank block.
    """
    finalized = [
        r for r in prior_records
        if type(r) is dict
        and r.get("status") == decision_record.STATUS_FINALIZED
        and _is_nonempty_str(r.get("id"))
    ]
    for record in reversed(finalized):
        if type(record.get("numeric_error")) is dict \
                or record.get("outcome_verdict") in ("HIT", "MISS"):
            return record
    return None


# A prior lesson text typically ends with a next-step directive phrased like
# "...; next proceed to import graph dependency analysis only after this
# succeeds...". We extract the clause starting at "next proceed to" (or
# "next:") up to the next sentence/clause break so announced_next.announced_action
# names what the prior lesson literally announced.
_ANNOUNCED_NEXT_RE = re.compile(
    r"next\s+(?:proceed\s+to\s+|step\s*:?\s*|:\s*)(.+?)(?:[;.]|$)",
    re.IGNORECASE,
)


def _announced_action_from_lesson(lesson_text):
    """Extract the 'next action' phrase a prior lesson announced, or None.

    Reads the clause after a "next proceed to ..." / "next: ..." directive. When
    no such directive is present the whole (bounded) lesson is returned as the
    announced action so announced_action is always a non-empty phrase for a
    non-empty lesson; returns None only for an empty/non-string lesson.
    """
    if not _is_nonempty_str(lesson_text):
        return None
    match = _ANNOUNCED_NEXT_RE.search(lesson_text)
    if match is not None and match.group(1).strip():
        return match.group(1).strip()
    return lesson_text.strip()


def _prior_outcome_summary(prior_record):
    """Build the checkable prior-outcome echo the rerank block carries.

    Carries only measured scalars (metric_key, measured, within_range) from the
    prior's numeric_error plus the HIT/MISS outcome_verdict, so the feed into
    the second pass is verifiable from the record alone. No free text / secret.
    """
    summary = {}
    numeric_error = prior_record.get("numeric_error")
    if type(numeric_error) is dict:
        summary["metric_key"] = numeric_error.get("metric_key")
        summary["measured"] = numeric_error.get("measured")
        summary["within_range"] = numeric_error.get("within_range")
    verdict = prior_record.get("outcome_verdict")
    if verdict in ("HIT", "MISS"):
        summary["outcome_verdict"] = verdict
    return summary


def _candidate_name(entry):
    """Return the free-text name/description for a plan candidate entry."""
    if type(entry) is not dict:
        return None
    for key in ("name", "description", "candidate"):
        value = entry.get(key)
        if _is_nonempty_str(value):
            return value
    return None


def _rerank_candidates_on_prior_outcome(plan, prior_summary):
    """Produce the SECOND scoring pass, demonstrably fed the prior outcome.

    The FIRST pass is the planner's own candidate ORDER (candidates as the
    planner emitted them: old_rank = position). The SECOND pass re-scores
    DETERMINISTICALLY from the concrete prior measured outcome:

    * When the prior's numeric_error.within_range is False (the prior prediction
      MISSED its range) OR the prior outcome_verdict is 'MISS', the second pass
      PROMOTES the candidate that most directly re-examines the prior metric (a
      recount / follow-up on the surprising prior metric) to new_rank 1 and
      demotes the rest, so a surprising prior outcome visibly changes the
      ranking.
    * When the prior was within_range (a calm prior), the second pass keeps the
      planner's order (new_rank == old_rank).

    Because the re-ranking reads prior_summary['within_range'] /
    ['outcome_verdict'] and ['metric_key'], flipping the fake prior outcome in a
    test provably changes new_rank / the promoted candidate: the prior MEASURED
    outcome, not model prose, drives the result.
    """
    candidates = plan.get("candidates")
    if type(candidates) is not list or not candidates:
        return None
    names = [_candidate_name(c) for c in candidates]
    names = [n for n in names if n is not None]
    if not names:
        return None

    within_range = prior_summary.get("within_range")
    verdict = prior_summary.get("outcome_verdict")
    surprised = (within_range is False) or (verdict == "MISS")
    metric_key = prior_summary.get("metric_key")

    # First-pass order is the planner's own candidate order.
    first_pass_order = list(names)

    if surprised and metric_key is not None:
        # Promote the candidate whose text most directly re-examines the prior
        # metric (the follow-up / recount candidate) to the top. The match is a
        # case-insensitive scan for the metric's quantity nouns or the words
        # 'recount'/'follow'/'calibrat'/'recalibrat' which name a re-examination.
        nouns = _COUNT_NOUNS.get(metric_key, ())
        reexam_words = tuple(nouns) + (
            "recount", "follow", "calibrat", "recalibrat", "re-examine",
            "reexamine", metric_key)

        def _reexam_score(name):
            lowered = name.lower()
            return sum(1 for w in reexam_words if w and w in lowered)

        scored = sorted(
            range(len(first_pass_order)),
            key=lambda i: (-_reexam_score(first_pass_order[i]), i),
        )
        second_pass_order = [first_pass_order[i] for i in scored]
    else:
        # Calm prior: the second pass keeps the first-pass order.
        second_pass_order = list(first_pass_order)

    old_rank_of = {name: idx + 1 for idx, name in enumerate(first_pass_order)}
    new_rank_of = {name: idx + 1 for idx, name in enumerate(second_pass_order)}
    entries = [
        {
            "candidate": _redact_text(name, 300),
            "old_rank": old_rank_of[name],
            "new_rank": new_rank_of[name],
        }
        for name in first_pass_order
    ]
    return entries, second_pass_order[0], surprised


def run_one_cycle(
    decisions_dir,
    *,
    planner,
    now_iso_provider,
    artifacts_root=None,
    repo_root=None,
    observation=None,
    target_text="SPRAY self-directed opportunity selection",
    estimated_credit_cost_usd=0.0,
    falsifiable=False,
    require_executed_matches_chosen=False,
    rerank_on_prior_outcome=False,
    anchor_free=False,
) -> dict:
    """Execute ONE offline decision cycle and return a summary dict.

    Steps (ordering is structurally enforced):

    1. Read all prior ``decisions/D-NNNN.json`` records and prior lessons.
    2. Ask ``planner.choose(...)`` for a self-generated plan (domain + >=3
       candidates + scoring + choice + prediction + outcome + lesson). The
       planner is the ONLY outbound call; offline tests inject a fake.
    3. Build the citation set: cite every prior decision id read, and cite the
       most recent finalized prior lesson verbatim (first cycle: none).
    4. ``create_record`` writes the PREDICTED record to disk (predicted_at set).
    5. Assert the D-NNNN.json file exists on disk, THEN produce the artifact
       under ``decisions/artifacts/D-NNNN/`` (the action).
    6. ``finalize_record`` fills the outcome/comparison/lesson (outcome_at set).

    ``now_iso_provider`` returns the predicted_at / outcome_at timestamps
    (normalized UTC 'Z' strings); outcome_at is sampled AFTER the artifact is
    written so it always follows predicted_at.

    R2-S9 ``require_executed_matches_chosen`` (keyword-only, default False):
    when True the runner PRE-CHECKS the plan's ``chosen_action`` with the
    executor's ``map_action_to_analysis`` and raises a clear ValueError BEFORE
    writing anything if that choice would fall back to the inventory default, so
    a fallback choice is refused at create and the cycle never silently runs
    inventory. The committed record also carries ``executed_matches_chosen=True``
    so the matching create-time validator rule applies. When False (the default)
    the cycle behaves exactly as before and no such key is written.

    R3-H5 ``anchor_free`` (keyword-only, default False): when True AND the
    re-scoring pass is active (``rerank_on_prior_outcome`` set AND a finalized
    measured prior exists), the planner prompt is built WITHOUT the prior
    lesson's announced-next directive (``anchor_free=True`` is threaded into
    ``planner.choose``) and the committed record carries ``anchor_free=True``.
    ``announced_next`` and the rerank block (diverged_from_announced + why) are
    STILL computed server-side from the RAW prior lesson exactly as today. When
    the flag is False (or no measured prior exists) the prompt, behavior, and
    record shape are byte-identical to a pre-H5 cycle.
    """
    decisions_path = Path(decisions_dir)

    # (1) Read priors. load_records is offline (filesystem only).
    prior_records = decision_record.load_records(decisions_path)
    prior_lessons = decision_record.load_prior_lessons(decisions_path)

    # (2) Ask the planner for its self-generated plan. This is the single
    # permitted outbound call (a fake in tests). History carries the structured
    # prior lessons PLUS each prior's HIT/MISS outcome_verdict and a short
    # reason (sourced from prediction_vs_actual) so the planner can see prior
    # OUTCOMES, not just lesson text, and react to a MISS. The augmentation is
    # additive: an entry simply lacks the verdict/reason keys when a prior does
    # not carry them (backward compatible with lesson-only history).
    history = _augment_history_with_outcomes(prior_lessons, prior_records)
    predicted_at = now_iso_provider()

    # R3-H5 anchor-free re-scoring. The anchor-free prompt change applies ONLY
    # when this cycle both opts into anchor-free mode AND is actually running
    # the re-scoring pass (rerank_on_prior_outcome set AND a finalized measured
    # prior exists). In that case the planner prompt is built WITHOUT the prior
    # lesson's announced-next directive, so the planner never sees the announced
    # next action; announced_next and rerank.diverged_from_announced + why are
    # STILL computed server-side below exactly as today. When the flag is off
    # (or no measured prior exists) the prompt is byte-identical to before.
    anchor_free_active = bool(
        anchor_free
        and rerank_on_prior_outcome
        and _latest_finalized_measured_prior(prior_records) is not None)

    choose_kwargs = {
        "observation": observation if type(observation) is dict else {},
        "target_text": target_text,
        "history": tuple(history),
    }
    # Only pass the anchor_free kwarg when the anchor-free prompt change is
    # actually active, so a planner whose choose() signature predates R3-H5
    # (every pre-H5 fake and the live planner's prior callers) is unaffected
    # when anchor_free is off.
    if anchor_free_active:
        choose_kwargs["anchor_free"] = True
    plan = planner.choose(**choose_kwargs)
    _require_plan_shape(plan)

    provider = getattr(planner, "provider", None)
    model = getattr(planner, "model_id", None)
    _require(provider in _ALLOWED_PROVIDERS,
             "planner provider must be one of %s" % sorted(_ALLOWED_PROVIDERS))
    _require(_is_nonempty_str(model), "planner model_id must be a non-empty string")

    # (3) Citations. Cite every prior decision id read (all of them), and cite
    # the most recent finalized prior lesson verbatim. First cycle: cite none.
    prior_ids = [r["id"] for r in prior_records if _is_nonempty_str(r.get("id"))]
    lessons_applied: list[dict] = []
    if prior_lessons:
        latest = prior_lessons[-1]
        # Copy the lesson text byte-for-byte to avoid lesson_text_mismatch.
        lessons_applied = [{
            "decision_id": latest["decision_id"],
            "lesson": latest["lesson"],
        }]

    # Build observed_state with S6 redaction. Record the self-chosen domain and
    # an explicit "none available" note on the first cycle so intent is visible.
    observed_state = _sanitize_observed_state({
        "domain": plan["domain"],
        "note": plan.get("observed_note"),
        "browser_observation": observation,
    })
    observed_state["self_chosen_domain"] = _redact_text(plan["domain"], 200)
    if not prior_lessons:
        observed_state["prior_lessons"] = _NO_PRIOR_LESSONS_NOTE

    chosen_rationale = _redact_text(plan["chosen_rationale"], 1000)
    if not prior_lessons:
        chosen_rationale = (
            chosen_rationale + " (prior lessons: " + _NO_PRIOR_LESSONS_NOTE + ")")

    # R2-S8: for a falsifiable cycle, extract + validate the planner's
    # structured quantitative claim (fail closed if missing/malformed) so it is
    # committed with the PREDICTED record and can be checked against the real
    # measured value after acting. Non-falsifiable cycles keep the prior shape.
    quantitative_claim = None
    if falsifiable:
        quantitative_claim = _quantitative_claim_from_plan(plan)

    # R2-S8 follow-up-on-finding: when the most-recent prior FINALIZED record
    # triggered one (over-range numeric error OR a non-empty findings list), the
    # validator REQUIRES a conforming followup_decision citing that prior. Build
    # it at CREATE time (it is create-time/immutable) from the plan's chose/why
    # (falling back to a safe default), and inject a follow-up candidate so the
    # planner/record can choose or reject it.
    followup_decision = None
    trigger = _followup_trigger(prior_records)
    if trigger is not None:
        prior_id, outcome_desc = trigger
        plan_followup = plan.get("followup_decision")
        chose = True
        why = ("following up on the prior finding: " + outcome_desc)
        if type(plan_followup) is dict:
            if type(plan_followup.get("chose")) is bool:
                chose = plan_followup["chose"]
            if _is_nonempty_str(plan_followup.get("why")):
                why = _redact_text(plan_followup["why"], 500)
        followup_decision = {
            "prior_decision_id": prior_id,
            "prior_outcome": _redact_text(outcome_desc, 300),
            "chose": chose,
            "why": why,
        }

    # R2-S9 executed-matches-chosen: when this cycle REQUIRES the executed
    # analysis to equal the chosen one, PRE-CHECK the plan's chosen_action with
    # the SAME executor mapper the executor will use. If it would fall back to
    # the inventory default (the chosen action does not map to a supported
    # analysis), raise a clear ValueError BEFORE writing anything, so the cycle
    # refuses a fallback choice at create and never silently runs inventory.
    # The committed record then carries executed_matches_chosen=True, making it
    # subject to the matching create-time validator rule (defense in depth).
    executed_matches_chosen = None
    if require_executed_matches_chosen:
        _chosen = action_executor._chosen_text_from_plan(plan)
        _analysis, _would_fall_back = action_executor.map_action_to_analysis(
            _chosen)
        if _would_fall_back:
            raise ValueError(
                "chosen_action would fall back to the inventory default; the "
                "cycle refuses a fallback choice when require_executed_matches_"
                "chosen is set (chosen action does not map to a supported "
                "analysis)")
        executed_matches_chosen = True

    # R3-H4 announced_next + second scoring pass (rerank). When
    # rerank_on_prior_outcome is set AND there is a most-recent FINALIZED prior
    # carrying a MEASURED outcome, build:
    #   * announced_next: what that prior lesson literally said to do next
    #     (source_decision_id + the prior lesson verbatim + the extracted
    #     'next proceed to X' phrase as announced_action);
    #   * rerank: a genuine SECOND scoring pass that DEMONSTRABLY reads the
    #     prior MEASURED outcome (numeric_error.within_range / outcome_verdict)
    #     and recomputes new_rank from it, citing prior_outcome_decision_id.
    # The divergence flag is set ONLY when the re-ranked top candidate maps to a
    # DIFFERENT analysis than the announced action AND the cycle's chosen_action
    # also maps there (so a CLAIMED divergence is real). Divergence is never
    # forced: a calm prior keeps the order and diverged_from_announced is False.
    # When there is no finalized measured prior, both are left None and the
    # cycle behaves exactly as an S9 cycle.
    announced_next = None
    rerank = None
    if rerank_on_prior_outcome:
        measured_prior = _latest_finalized_measured_prior(prior_records)
        if measured_prior is not None:
            announced_action = _announced_action_from_lesson(
                measured_prior.get("lesson"))
            if _is_nonempty_str(announced_action):
                announced_next = {
                    "source_decision_id": measured_prior["id"],
                    # Copy the lesson byte-for-byte to satisfy the verbatim
                    # announced_next_lesson_mismatch rule.
                    "lesson_text": measured_prior["lesson"],
                    "announced_action": _redact_text(announced_action, 500),
                }
            prior_summary = _prior_outcome_summary(measured_prior)
            reranked = _rerank_candidates_on_prior_outcome(plan, prior_summary)
            if reranked is not None and announced_next is not None:
                entries, top_candidate, surprised = reranked
                chosen_text = action_executor._chosen_text_from_plan(plan)
                chosen_analysis, _c_fb = \
                    action_executor.map_action_to_analysis(chosen_text)
                announced_analysis, _a_fb = \
                    action_executor.map_action_to_analysis(
                        announced_next["announced_action"])
                top_analysis, _t_fb = \
                    action_executor.map_action_to_analysis(top_candidate)
                # A real divergence: the surprising prior re-ranked the top
                # candidate to a different analysis than announced, AND the
                # cycle actually chose that different analysis.
                diverged = bool(
                    surprised
                    and top_analysis != announced_analysis
                    and chosen_analysis != announced_analysis)
                if diverged:
                    why = (
                        "prior measured outcome on %s (within_range=%s) re-ranked"
                        " the candidates so the chosen action diverges from the "
                        "announced next step" % (
                            prior_summary.get("metric_key"),
                            prior_summary.get("within_range")))
                elif chosen_analysis != announced_analysis:
                    why = (
                        "prior measured outcome fed the second scoring pass; "
                        "the chosen analysis differs from the announced next "
                        "step, but the strict divergence flag stays false "
                        "because no surprising-prior re-rank promoted a "
                        "different-analysis candidate to the top")
                else:
                    why = (
                        "prior measured outcome fed the second scoring pass; "
                        "chosen action stays consistent with the announced next"
                        " step")
                rerank = {
                    "prior_outcome_decision_id": measured_prior["id"],
                    "prior_outcome": prior_summary,
                    "candidates": entries,
                    "diverged_from_announced": diverged,
                    "why": _redact_text(why, 500),
                }

    # (4) Write the PREDICTED record BEFORE acting. create_record validates
    # before writing and stamps predicted_at via the additive field.
    record = decision_record.create_record(
        decisions_path,
        observed_state=observed_state,
        prior_decision_ids_read=prior_ids,
        candidates=plan["candidates"],
        scoring_criteria=plan["scoring_criteria"],
        candidate_scores=plan["candidate_scores"],
        chosen_action=_redact_text(plan["chosen_action"], 500),
        chosen_rationale=chosen_rationale,
        rejected=plan["rejected"],
        prediction=_redact_text(plan["prediction"], 1000),
        lessons_applied=lessons_applied,
        provider=provider,
        model=model,
        estimated_credit_cost_usd=estimated_credit_cost_usd,
        predicted_at=predicted_at,
        falsifiable=True if falsifiable else None,
        quantitative_claim=quantitative_claim,
        followup_decision=followup_decision,
        executed_matches_chosen=executed_matches_chosen,
        announced_next=announced_next,
        rerank=rerank,
        # R3-H5: self-describe the record as an anchor-free experiment ONLY when
        # the anchor-free prompt change was actually applied this cycle. When
        # inactive, no anchor_free key is written and the record shape is
        # byte-identical to a pre-H5 cycle.
        anchor_free=True if anchor_free_active else None,
    )
    record_id = record["id"]

    # (5) Ordering guard: the prediction file MUST be on disk before we act.
    record_path = decisions_path / (record_id + ".json")
    _require(record_path.is_file(),
             "prediction record must be on disk before acting: " + record_id)

    # (5b) ACT: run the chosen offline analysis for real and write its artifact
    # under decisions/artifacts/D-NNNN/. The executor COMPUTES the measured
    # outcome (real path, byte size, sha256, counts, wall-clock duration); the
    # model's asserted outcome text is NOT used for the recorded outcome.
    measured = action_executor.execute_chosen_action(
        decisions_path, record_id, plan,
        repo_root=repo_root, artifacts_root=artifacts_root)

    # The executor returns the artifact path relative to the decisions dir; the
    # absolute path is needed for the on-disk ordering assertion and finalize's
    # artifact re-verification resolves the relative path from the decisions dir.
    artifact_rel = measured["artifact_path"]
    artifact_abs = (decisions_path / artifact_rel)

    # (6) Finalize only AFTER the artifact exists. outcome_at is sampled now so
    # it always follows predicted_at.
    _require(artifact_abs.is_file(),
             "artifact must exist before finalize: " + record_id)
    outcome_at = now_iso_provider()

    # Build the recorded actual_outcome ONLY from the executor's measurements.
    # plan[actual_outcome] / plan[prediction_vs_actual] are IGNORED.
    actual_outcome = _measured_actual_outcome(measured)
    # The model's INTENDED analysis is derived from its chosen_action via the
    # SAME mapper the executor used, so the cycle knows what the model intended
    # independent of what actually ran.
    intended_analysis, _intended_fallback = action_executor.map_action_to_analysis(
        action_executor._chosen_text_from_plan(plan))
    outcome_verdict = _outcome_verdict(measured, intended_analysis)
    prediction_vs_actual = _mechanical_prediction_vs_actual(
        plan.get("prediction"), measured, intended_analysis)

    # R2-S8: for a falsifiable cycle carrying a quantitative_claim, compute the
    # REAL numeric error from the executor's MEASURED value for the claim's
    # metric_key (never a model-asserted number) and surface the executor's
    # deterministic findings. The prediction_vs_actual string must state the
    # real error (predicted vs measured, within/outside) and MUST NOT say 'no
    # numeric claim' for such a cycle. A non-falsifiable cycle keeps exactly the
    # prior behavior.
    numeric_error = None
    findings = None
    if quantitative_claim is not None:
        numeric_error = _compute_numeric_error(quantitative_claim, measured)
        findings = action_executor.findings_from_counts(
            measured["analysis"], measured.get("counts"))
        prediction_vs_actual = _redact_text(
            _numeric_error_phrase(numeric_error), 1000)

    finalized = decision_record.finalize_record(
        decisions_path,
        record_id,
        actual_outcome=actual_outcome,
        prediction_vs_actual=prediction_vs_actual,
        lesson=_redact_text(plan["lesson"], 1000),
        outcome_at=outcome_at,
        artifact_path=artifact_rel,
        artifact_sha256=measured["artifact_sha256"],
        outcome_verdict=outcome_verdict,
        numeric_error=numeric_error,
        findings=findings,
    )

    return {
        "id": record_id,
        "status": finalized["status"],
        "provider": provider,
        "model": model,
        "estimated_credit_cost_usd": estimated_credit_cost_usd,
        "domain": observed_state["self_chosen_domain"],
        "predicted_at": predicted_at,
        "outcome_at": outcome_at,
        "artifact_path": str(artifact_abs),
        "lessons_applied": lessons_applied,
        # R2-S6 measured fields (additive; existing keys above are preserved).
        "analysis": measured["analysis"],
        "fallback": measured["fallback"],
        "artifact_sha256": measured["artifact_sha256"],
        "artifact_bytes": measured["artifact_bytes"],
        "counts": measured["counts"],
        "duration_seconds": measured["duration_seconds"],
        "prediction_vs_actual": prediction_vs_actual,
        # R2-S7 honest HIT/MISS verdict (additive).
        "outcome_verdict": outcome_verdict,
        # R2-S8 falsifiable-prediction fields (additive; None for a
        # non-falsifiable cycle so the prior summary shape is preserved).
        "falsifiable": bool(falsifiable),
        "quantitative_claim": quantitative_claim,
        "numeric_error": numeric_error,
        "findings": findings,
        "followup_decision": followup_decision,
        # R2-S9 marker (additive; None for a cycle that did not require the
        # executed analysis to equal the chosen one, preserving the prior shape).
        "executed_matches_chosen": executed_matches_chosen,
        # R3-H4 re-rank fields (additive; None when this cycle did not run a
        # second scoring pass fed a prior measured outcome).
        "announced_next": announced_next,
        "rerank": rerank,
        # R3-H5 anchor-free marker (additive; True only when the anchor-free
        # prompt change was applied this cycle, else False so the prior summary
        # shape is preserved).
        "anchor_free": bool(anchor_free_active),
    }


# The headline-count keys an analysis may report, in priority order, together
# with the quantity NOUNS a prediction must name for a numeric bound to count
# as a checkable claim about THAT quantity. We only grade a bound that is tied
# to the noun matching the analysis's headline count; incidental integers
# ("0 network calls", "under 60s") never form a range.
_COUNT_NOUNS: dict[str, tuple[str, ...]] = {
    "total_files": ("file", "files"),
    "total_markers": ("marker", "markers", "todo", "fixme", "hack", "xxx"),
    "total_lines": ("line", "lines"),
    "total_bytes": ("byte", "bytes"),
    # R2-S7 new analyses' headline counts and the quantity nouns a prediction
    # must name for a numeric bound to be graded against that headline.
    "duplicate_basename_groups": ("duplicate", "duplicates", "group", "groups"),
    "documented": ("documented", "docstring", "docstrings", "object", "objects"),
    "edges": ("edge", "edges", "dependency", "dependencies", "import", "imports"),
}

# Explicit, clearly-labeled bound phrasings. Each maps a matched phrase to a
# comparison so a single labeled claim ("under 50 markers", "at least 3 files",
# "fewer than 100 lines") yields a real within/outside verdict. We deliberately
# do NOT harvest bare integers: a bound only counts when it is a recognized
# bound word adjacent to the analysis's own quantity noun.
#   kind: "upper" -> count must be <= bound; "lower" -> count must be >= bound;
#         "upper_strict"/"lower_strict" -> strict (<, >).
_BOUND_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"under\s+(\d+)", "upper_strict"),
    (r"fewer\s+than\s+(\d+)", "upper_strict"),
    (r"less\s+than\s+(\d+)", "upper_strict"),
    (r"no\s+more\s+than\s+(\d+)", "upper"),
    (r"at\s+most\s+(\d+)", "upper"),
    (r"up\s+to\s+(\d+)", "upper"),
    (r"more\s+than\s+(\d+)", "lower_strict"),
    (r"over\s+(\d+)", "lower_strict"),
    (r"greater\s+than\s+(\d+)", "lower_strict"),
    (r"at\s+least\s+(\d+)", "lower"),
    (r"no\s+fewer\s+than\s+(\d+)", "lower"),
    (r"(\d+)\s+or\s+more", "lower"),
    (r"(\d+)\s+or\s+fewer", "upper"),
)


def _measured_actual_outcome(measured: dict) -> dict:
    """Build the recorded actual_outcome from the executor's measurements only.

    Every value here is a LOCAL measurement (no model-asserted outcome text and
    no secret). The only model-derived string, ``mapped_from``, has already been
    S6-redacted by the executor; we redact it again defensively.
    """
    return {
        "measured": True,
        "analysis": measured["analysis"],
        "mapped_from": _redact_text(measured.get("mapped_from", ""), 500),
        "fallback": bool(measured["fallback"]),
        "artifact_path": measured["artifact_path"],
        "artifact_sha256": measured["artifact_sha256"],
        "artifact_bytes": measured["artifact_bytes"],
        "counts": measured["counts"],
        "duration_seconds": measured["duration_seconds"],
    }


def _primary_count(measured: dict):
    """Return ``(key, value)`` for the analysis's headline count, or None.

    ``key`` identifies the measured quantity (e.g. ``total_lines``) so the
    prediction parser can require an EXPLICIT bound tied to that same quantity.
    """
    counts = measured.get("counts")
    if type(counts) is not dict:
        return None
    for key in ("total_files", "total_markers", "total_lines", "total_bytes",
                "duplicate_basename_groups", "documented", "edges"):
        value = counts.get(key)
        if type(value) is int:
            return key, value
    return None


def _explicit_bound_for_count(prediction: str, count_key: str):
    """Find an EXPLICIT, labeled numeric bound tied to the measured quantity.

    Returns ``(kind, bound_int, phrase)`` for the FIRST recognized bound word
    (``under`` / ``fewer than`` / ``at least`` / ``N or more`` / ...) whose
    match sits next to one of the quantity NOUNS for ``count_key`` (so "under
    50 markers" grades the marker count, but "under 60s" or "0 network calls"
    do not). Returns None when there is no such labeled claim.

    The adjacency test requires the quantity noun to appear within a short
    window (<= 24 chars) on either side of the matched bound phrase, so a bound
    must actually describe that quantity rather than merely co-occur in prose.
    """
    nouns = _COUNT_NOUNS.get(count_key, ())
    if not nouns:
        return None
    lowered = prediction.lower()
    for pattern, kind in _BOUND_PATTERNS:
        for match in re.finditer(pattern, lowered):
            bound = int(match.group(1))
            window_start = max(0, match.start() - 24)
            window_end = min(len(lowered), match.end() + 24)
            window = lowered[window_start:window_end]
            if any(re.search(r"\b" + re.escape(noun) + r"\b", window)
                   for noun in nouns):
                return kind, bound, match.group(0)
    return None


def _explicit_range_for_count(prediction: str, count_key: str):
    """Find an EXPLICIT ``between N and M`` range tied to the measured quantity.

    Returns ``(low, high, phrase)`` for the first "between N and M" claim that
    sits next to one of the quantity NOUNS for ``count_key``, else None. Like
    ``_explicit_bound_for_count`` this requires the quantity noun nearby so an
    incidental "between" phrase about something else is not graded.
    """
    nouns = _COUNT_NOUNS.get(count_key, ())
    if not nouns:
        return None
    lowered = prediction.lower()
    for match in re.finditer(r"between\s+(\d+)\s+and\s+(\d+)", lowered):
        low, high = int(match.group(1)), int(match.group(2))
        if low > high:
            low, high = high, low
        window_start = max(0, match.start() - 24)
        window_end = min(len(lowered), match.end() + 24)
        window = lowered[window_start:window_end]
        if any(re.search(r"\b" + re.escape(noun) + r"\b", window)
               for noun in nouns):
            return low, high, match.group(0)
    return None


def _is_miss(measured: dict, intended_analysis) -> bool:
    """True iff this run is an honest MISS (fallback OR executed != intended).

    A MISS means the executor could NOT faithfully run what the model chose:
    either it fell back to the safe default (``measured['fallback']`` is True),
    or the analysis it actually ran differs from the analysis the model's
    chosen_action intended. Both conditions mean the recorded outcome did NOT
    answer the chosen action and must never be reported as 'as predicted'.
    """
    if bool(measured.get("fallback")):
        return True
    executed = measured.get("analysis")
    if intended_analysis is not None and executed != intended_analysis:
        return True
    return False


def _outcome_verdict(measured: dict, intended_analysis) -> str:
    """The explicit HIT/MISS verdict: MISS iff ``_is_miss`` else HIT."""
    return "MISS" if _is_miss(measured, intended_analysis) else "HIT"


def _miss_reason(measured: dict, intended_analysis) -> str:
    """A short, honest MISS reason string (no secrets); starts after 'MISS: '.

    States WHY the run missed: a fallback to inventory, or an executed analysis
    that diverges from the intended one. The returned fragment is appended to
    the 'MISS' prefix by ``_mechanical_prediction_vs_actual``.
    """
    executed = measured.get("analysis", "?")
    if bool(measured.get("fallback")):
        return ("executor fell back to %s; chosen action intended %s"
                % (executed, intended_analysis or "an unmapped analysis"))
    return ("executed %s but chosen action intended %s"
            % (executed, intended_analysis or "an unmapped analysis"))


def _mechanical_prediction_vs_actual(
        prediction, measured: dict, intended_analysis=None) -> str:
    """Compare the committed prediction to the measured result MECHANICALLY.

    R2-S7 honest MISS: when the run is a MISS (``_is_miss`` - the executor fell
    back, or ran a different analysis than the chosen action intended) the
    returned string STARTS with 'MISS' and states the reason; it is NEVER
    reported as 'artifact produced as predicted'. This is the visible miss that
    can drive the next choice.

    For a faithful run (not a MISS): we do NOT copy the model's self-graded
    ``prediction_vs_actual`` text, and we do NOT fabricate a range by harvesting
    incidental integers. A within/outside verdict is emitted ONLY when the
    prediction carries an EXPLICIT, clearly labeled numeric bound ("under N",
    "at least N", "fewer than N", ...) tied to the SAME quantity the analysis
    measured (its headline count). When no such checkable claim exists we say so
    plainly: a faithful run with no falsifiable numeric claim is recorded only
    on the basis of the artifact being produced for the intended analysis, and
    the string honestly states there was no numeric claim to verify. The
    returned text is S6-redacted.
    """
    if _is_miss(measured, intended_analysis):
        return _redact_text(
            "MISS: " + _miss_reason(measured, intended_analysis), 1000)
    produced = "artifact produced as predicted"
    primary = _primary_count(measured)
    if primary is not None and isinstance(prediction, str):
        count_key, count = primary
        rng = _explicit_range_for_count(prediction, count_key)
        if rng is not None:
            low, high, phrase = rng
            ok = low <= count <= high
            verdict = "within" if ok else "outside"
            analysis = measured.get("analysis", "?")
            detail = (
                "measured %s count %d is %s the predicted range '%s'"
                % (analysis, count, verdict, phrase)
            )
            return _redact_text(produced + "; " + detail, 1000)
        bound = _explicit_bound_for_count(prediction, count_key)
        if bound is not None:
            kind, value, phrase = bound
            if kind == "upper":
                ok = count <= value
            elif kind == "upper_strict":
                ok = count < value
            elif kind == "lower":
                ok = count >= value
            else:  # lower_strict
                ok = count > value
            verdict = "within" if ok else "outside"
            analysis = measured.get("analysis", "?")
            detail = (
                "measured %s count %d is %s the predicted bound '%s'"
                % (analysis, count, verdict, phrase)
            )
            return _redact_text(produced + "; " + detail, 1000)
    return _redact_text(
        produced + "; no numeric claim in the prediction to verify", 1000)
