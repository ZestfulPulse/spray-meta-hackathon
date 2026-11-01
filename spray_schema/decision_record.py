"""Offline, append-only Decision Record mechanism for SPRAY.

A Decision Record captures one judge-and-decide cycle so the owner can SEE
SPRAY reason: the observed state, the self-generated opportunity candidates,
the scoring, the chosen action and why, the rejected options, a prediction
written BEFORE acting, and (after the fact) the actual outcome, the
prediction-vs-actual comparison and the lesson learned. Each record may cite
lessons from earlier finalized records so decisions build on one another.

Design (mirrors spray_schema/offline_audit.py and offline_g4_preflight.py):

* No cloud/model/network calls. This module is pure stdlib and offline.
* Result objects are frozen @dataclass values.
* Validation uses strict ``type(x) is ...`` / isinstance checks and raises
  ValueError with short messages (or returns a DecisionValidation reason).
* JSON is written with sort_keys=True, compact separators and ensure_ascii so
  records are byte-stable; writes are atomic (temp file + os.replace).
* Records are append-only: create refuses to overwrite an existing file, and a
  FINALIZED record may never be edited again.

Two-phase lifecycle keeps the prediction honest:

* ``create_record`` writes a record with ``status='PREDICTED'`` whose
  ``actual_outcome`` / ``prediction_vs_actual`` / ``lesson`` are all null. The
  prediction is therefore committed BEFORE any outcome exists.
* ``finalize_record`` fills ONLY those three fields and flips status to
  ``'FINALIZED'``; it refuses to alter any other field and refuses to finalize
  a record twice.

DECISIONS.md convention: this module appends exactly ONE Korean summary line
per decision at CREATE time and leaves it unchanged at finalize (one line per
decision, written once).

R2-S8 falsifiable-prediction gating (backward compatibility)
------------------------------------------------------------
The S8 fields (``falsifiable``, ``quantitative_claim``, ``numeric_error``,
``followup_decision``, ``findings``) are all OPTIONAL additive fields. Two new
validator rules are GATED so that every pre-S8 record (D-0001..D-0012 and all
existing fixtures/tests) validates exactly as before:

* ``prediction_missing_quantitative_claim`` (create-time) fires ONLY when the
  record carries the explicit marker ``falsifiable == True``. A record without
  the marker is never subject to it, so unmarked records keep validating.
* ``followup_finding_not_addressed`` fires ONLY when the MOST-RECENT prior
  FINALIZED record carries the S8 numeric_error/finding fields AND indicates a
  trigger (``numeric_error.within_range`` is False OR a non-empty ``findings``
  list). Priors without the S8 fields never trigger it, so a chain of pre-S8
  records is unaffected.

R2-S9 executed-matches-chosen gating (backward compatibility)
-------------------------------------------------------------
The S9 field ``executed_matches_chosen`` (bool) is an OPTIONAL additive marker
set at CREATE time by run_one_cycle for NEW cycles that require the executed
analysis to equal the chosen one. It GATES a single create-time rule, mirroring
the S8 ``falsifiable`` gating exactly:

* ``chosen_action_would_fall_back`` (create-time) fires ONLY when the record
  carries the explicit marker ``executed_matches_chosen == True``. When fired,
  the record's ``chosen_action`` is mapped via the executor's
  ``map_action_to_analysis`` (imported lazily inside the validator so this
  module stays import-light and offline) and the record is REJECTED if the
  mapping returns ``fallback == True`` (i.e. the chosen action does not map to a
  supported analysis and the executor would silently run the inventory
  fallback). A record WITHOUT the marker (every pre-existing record
  D-0001..D-0015 and all fixtures/tests, several of which are fallback=true) is
  never subject to the rule and validates exactly as before. The marker is set
  at create and is immutable at finalize (NOT in _FINALIZE_FIELDS).

R3-H4 re-rank gating (backward compatibility)
---------------------------------------------
Two more OPTIONAL additive fields capture a genuine second scoring pass that a
surprising prior outcome can drive. Both are CREATE-time (they describe the
pre-action decision) and are NOT in _FINALIZE_FIELDS, so finalize can never set
or alter them:

* ``announced_next`` (dict) records what the cited prior lesson literally said
  to do next. Shape: ``source_decision_id`` (a 'D-NNNN' id that must exist among
  priors), ``lesson_text`` (a non-empty string that MUST match that prior
  FINALIZED record's ``lesson`` verbatim, reusing the lesson_text_mismatch
  byte-match discipline), and ``announced_action`` (a non-empty string naming
  the next action that prior lesson announced). Shape reason:
  ``invalid_announced_next``; verbatim-mismatch reason:
  ``announced_next_lesson_mismatch``.
* ``rerank`` (dict) records a genuine SECOND scoring pass fed the PRIOR MEASURED
  OUTCOME. Shape: ``prior_outcome_decision_id`` (a 'D-NNNN' id), ``prior_outcome``
  (a dict echoing the measured outcome it was fed: at minimum ``metric_key`` +
  ``measured`` + ``within_range`` and/or ``outcome_verdict`` so the feed is
  checkable from the record alone), a ``candidates`` (or ``passes``) list where
  each entry has ``candidate`` (non-empty str), ``old_rank`` (int>=1, first
  pass) and ``new_rank`` (int>=1, second pass), plus top-level
  ``diverged_from_announced`` (bool) and ``why`` (non-empty str). Shape reason:
  ``invalid_rerank``.

Two GATED rules fire ONLY when a ``rerank`` block is present, so every
pre-existing record D-0001..D-0018 and every fixture (none carry ``rerank``)
validate exactly as before:

* ``rerank_missing_prior_outcome_citation`` - a record carrying a ``rerank``
  block MUST cite via ``prior_outcome_decision_id`` a prior record that exists
  among priors AND is FINALIZED carrying a measured outcome (``numeric_error``
  and/or ``outcome_verdict``). A rerank block whose citation is absent,
  malformed, not found, or points at a non-finalized / non-measured prior is
  REJECTED.
* ``divergence_not_reflected_in_choice`` - when BOTH ``announced_next`` and
  ``rerank`` are present and the rerank claims ``diverged_from_announced: true``,
  the record's ``chosen_action`` must map (via
  action_executor.map_action_to_analysis, imported LAZILY like the S9 rule) to a
  DIFFERENT analysis than ``announced_next.announced_action`` maps to, and
  ``rerank.why`` must be non-empty. This only validates that a CLAIMED
  divergence is real; it never forces divergence.

R3-H5 anchor-free marker (backward compatibility)
-------------------------------------------------
One more OPTIONAL additive field, ``anchor_free`` (bool), records that the
re-scoring pass ran WITHOUT the prior lesson's announced-next directive in the
planner prompt (the announcement anchor was stripped before the lesson entered
the prompt). It is CREATE-time (it describes how the pre-action prompt was
built) and is NOT in _FINALIZE_FIELDS, so finalize can never set or alter it. It
is validated SHAPE-ONLY and GATED on presence: a non-bool value is rejected with
the distinct reason ``invalid_anchor_free``; a record omitting it validates
EXACTLY as before, so D-0001..D-0022 and every fixture are unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
from pathlib import Path

# Reuse the ledger's single source of truth for how a normalized UTC 'Z'
# instant is parsed/ordered. A value that is not a string, does not end in
# 'Z', carries an explicit numeric offset, or does not parse returns None
# (fail closed). The R2-S4 timestamp fields are validated with exactly this.
from .resource_ledger import _parse_utc_iso

# Single source of truth for the record schema. create/finalize/validator all
# agree on exactly this set of keys.
FIELD_NAMES: tuple[str, ...] = (
    "id",
    "observed_state",
    "prior_decision_ids_read",
    "candidates",
    "scoring_criteria",
    "candidate_scores",
    "chosen_action",
    "chosen_rationale",
    "rejected",
    "prediction",
    "status",
    "actual_outcome",
    "prediction_vs_actual",
    "lesson",
    "lessons_applied",
)

# R2-S3 / R2-S4 additive fields. These are OPTIONAL: a record may omit them
# entirely (so the unchanged R2-S2 tests, which supply exactly FIELD_NAMES,
# still validate), but when present each is validated.
#
# * provider / model / estimated_credit_cost_usd (R2-S3) are set at CREATE time
#   and are immutable at finalize (never listed in _FINALIZE_FIELDS).
# * predicted_at (R2-S4) is a normalized UTC ISO 'Z' timestamp set at CREATE
#   time; it is immutable at finalize (never listed in _FINALIZE_FIELDS).
# * outcome_at (R2-S4) is a normalized UTC ISO 'Z' timestamp set ONLY at
#   finalize (listed in _FINALIZE_FIELDS). When BOTH timestamps are present the
#   validator proves the prediction precedes the outcome.
#
# * artifact_path / artifact_sha256 (R2-S6) reference the REAL analysis
#   artifact the action executor produced. They are written ONLY at finalize
#   (the artifact does not exist until the action runs) and are therefore
#   listed in _FINALIZE_FIELDS. When a FINALIZED record carries BOTH, the
#   validator can re-resolve the artifact file and prove it exists and that its
#   sha256 matches (see the resolution-convention note on validate_record).
OPTIONAL_FIELD_NAMES: tuple[str, ...] = (
    "provider",
    "model",
    "estimated_credit_cost_usd",
    "predicted_at",
    "outcome_at",
    "artifact_path",
    "artifact_sha256",
    # R2-S7 additive: the explicit HIT/MISS verdict the decision cycle computed
    # from the executor measurements. Written ONLY at finalize (the outcome does
    # not exist until the action runs), so it is also listed in _FINALIZE_FIELDS.
    # A record omitting it validates exactly as before (backward compatible).
    "outcome_verdict",
    # R2-S8 additive falsifiable-prediction fields (all OPTIONAL; a record that
    # omits every one of them validates EXACTLY as before, so pre-S8 records
    # D-0001..D-0012 and every existing fixture/test are unaffected):
    #
    # * ``falsifiable`` (bool) is the explicit S8 marker set at CREATE time by
    #   run_one_cycle/harness for NEW cycles. It GATES the create-time
    #   quantitative-claim rule: the rule is enforced ONLY on a record carrying
    #   ``falsifiable: true`` (see 'prediction_missing_quantitative_claim'
    #   below), so a record without the marker is never subject to it. The
    #   marker is set at create and is immutable at finalize (NOT in
    #   _FINALIZE_FIELDS).
    # * ``quantitative_claim`` (dict) is the structured, checkable claim the
    #   planner committed BEFORE acting: metric_key + a predicted value or
    #   [low, high] range + a kind label + a confidence in (0, 1]. It is set at
    #   CREATE time and is immutable at finalize (NOT in _FINALIZE_FIELDS) so
    #   the committed claim can never be rewritten to match the outcome.
    # * ``numeric_error`` (dict) is the REAL numeric error computed from the
    #   executor's measured value at finalize (predicted echo, measured int,
    #   absolute_error, relative_error [null when basis 0], within_range bool,
    #   confidence echo). Written ONLY at finalize, so it IS in _FINALIZE_FIELDS.
    # * ``followup_decision`` (dict) records whether the cycle chose or rejected
    #   a follow-up on a prior over-range error / finding, citing the prior id.
    #   It is built and committed at CREATE time (the follow-up-on-finding rule
    #   is checked when the PREDICTED record is written), so it is create-time /
    #   immutable at finalize and is NOT in _FINALIZE_FIELDS.
    # * ``findings`` (list of plain strings) are the bounded, deterministic
    #   findings the executor surfaced from the measured counts at finalize
    #   (e.g. 'undocumented=12'). Written ONLY at finalize, so it IS in
    #   _FINALIZE_FIELDS. A non-empty findings list on the most-recent prior is
    #   one of the two triggers for the follow-up-on-finding rule.
    "falsifiable",
    "quantitative_claim",
    "numeric_error",
    "followup_decision",
    "findings",
    # R2-S9 additive: the explicit marker (bool) that this cycle REQUIRES the
    # executed analysis to equal the chosen one. Set at CREATE time by
    # run_one_cycle for a new S9 cycle; it GATES the create-time
    # 'chosen_action_would_fall_back' rule below (fires ONLY when the marker is
    # True). It is immutable at finalize (deliberately NOT in _FINALIZE_FIELDS).
    # A record omitting it validates exactly as before (backward compatible).
    "executed_matches_chosen",
    # R3-H4 additive re-rank fields (both OPTIONAL; a record omitting both
    # validates EXACTLY as before, so D-0001..D-0018 and every fixture are
    # unaffected). Both are CREATE-time and immutable at finalize (deliberately
    # NOT in _FINALIZE_FIELDS):
    #
    # * ``announced_next`` (dict) echoes what the cited prior lesson literally
    #   said to do next: source_decision_id + the prior lesson_text (verbatim)
    #   + announced_action. See the module docstring for the full shape and the
    #   invalid_announced_next / announced_next_lesson_mismatch reasons.
    # * ``rerank`` (dict) records the genuine SECOND scoring pass fed the prior
    #   measured outcome: prior_outcome_decision_id + prior_outcome summary + a
    #   candidates/passes list with old_rank/new_rank + diverged_from_announced
    #   + why. Presence of this key GATES the rerank_missing_prior_outcome_
    #   citation and divergence_not_reflected_in_choice rules below.
    "announced_next",
    "rerank",
    # R3-H5 additive: the anchor-free experiment marker (bool). It records that
    # the re-scoring pass was run WITHOUT the prior lesson's announced-next
    # directive in the planner prompt (the announcement anchor was stripped),
    # so the record self-describes as an anchor-free cycle. It is CREATE-time
    # and immutable at finalize (deliberately NOT in _FINALIZE_FIELDS), exactly
    # like falsifiable / quantitative_claim / announced_next / rerank. It is
    # validated SHAPE-ONLY when present (a non-bool is rejected with the
    # distinct reason 'invalid_anchor_free'); a record omitting it validates
    # EXACTLY as before, so D-0001..D-0022 and every fixture are unaffected.
    "anchor_free",
)

# The provider recorded on a decision must be one of the known planner
# providers. Mirrors spray_schema.gemini_planner / spray_schema.meta_planner.
ALLOWED_PROVIDERS: frozenset[str] = frozenset({"gemini", "meta"})

# The fields finalize is allowed to change (status plus the three nulls, plus
# the OPTIONAL R2-S4 outcome_at timestamp which is written only at finalize).
# predicted_at is deliberately NOT here: it is set at create and immutable.
_FINALIZE_FIELDS: frozenset[str] = frozenset(
    {
        "status", "actual_outcome", "prediction_vs_actual", "lesson",
        "outcome_at",
        # R2-S6: the artifact reference is produced at action time, so these
        # are written at finalize (mirroring how outcome_at was added).
        "artifact_path", "artifact_sha256",
        # R2-S7: the HIT/MISS verdict is computed from the measured outcome at
        # finalize, so it is finalize-mutable (like outcome_at / artifact_*).
        "outcome_verdict",
        # R2-S8: numeric_error is computed from the executor measurement at
        # finalize and findings are surfaced from the measured counts at
        # finalize, so both are finalize-written. ``falsifiable``,
        # ``quantitative_claim`` AND ``followup_decision`` are deliberately NOT
        # here: the S8 marker, the committed claim and the create-time
        # follow-up decision are all create-time and MUST stay byte-for-byte
        # immutable at finalize (finalize_record has no followup_decision
        # parameter and never writes it).
        "numeric_error",
        "findings",
    }
)

# The two legal HIT/MISS verdict values. Any other value is rejected with a
# distinct reason so a malformed verdict never passes validation.
_OUTCOME_VERDICTS: frozenset[str] = frozenset({"HIT", "MISS"})

# R2-S8: the recognized headline metric keys a quantitative_claim / numeric_error
# may name. Kept in sync with action_executor.RECOGNIZED_METRIC_KEYS (one
# headline per analysis). Duplicated here as a frozenset so this module stays
# import-light and offline (no cross-import of the executor at validation time).
_RECOGNIZED_METRIC_KEYS: frozenset[str] = frozenset({
    "total_files",
    "total_markers",
    "total_lines",
    "total_bytes",
    "duplicate_basename_groups",
    "documented",
    "edges",
})

# A sha256 hex digest is exactly 64 lowercase hex characters.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

STATUS_PREDICTED = "PREDICTED"
STATUS_FINALIZED = "FINALIZED"

_ID_RE = re.compile(r"^D-(\d{4})$")

DECISIONS_MD = "DECISIONS.md"


@dataclass(frozen=True)
class DecisionValidation:
    """Result of validating a single record against its earlier records."""

    valid: bool
    reason: str


def _id_to_seq(record_id: str) -> int | None:
    """Return the integer sequence for a 'D-NNNN' id, or None if malformed."""
    if type(record_id) is not str:
        return None
    match = _ID_RE.match(record_id)
    if match is None:
        return None
    return int(match.group(1))


def _seq_to_id(seq: int) -> str:
    """Format a 1-based sequence number as a zero-padded 'D-NNNN' id."""
    if type(seq) is not int or seq < 1 or seq > 9999:
        raise ValueError("invalid decision sequence")
    return "D-%04d" % seq


def _is_nonempty_str(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _is_short_nonempty_str(value: object, limit: int = 64) -> bool:
    """A non-empty str whose length is bounded (short identifier/label)."""
    return _is_nonempty_str(value) and len(value) <= limit


def _is_finite_non_negative_number(value: object) -> bool:
    """True only for a finite, non-negative, non-bool real number.

    Mirrors the numeric guard style in resource_ledger._check_non_negative_number
    and event_envelope: bools are rejected (``bool`` is a subclass of ``int``),
    NaN / Infinity are rejected, and every exotic failure mode is treated as
    invalid (fail closed).
    """
    if type(value) not in (int, float):
        return False
    try:
        if not math.isfinite(float(value)):
            return False
    except (TypeError, ValueError, ArithmeticError):
        return False
    return value >= 0


def _dumps(record: dict) -> str:
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _file_sha256(path: Path) -> str | None:
    """Return the lowercase hex sha256 of a file's bytes, or None if unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _is_int_not_bool(value: object) -> bool:
    """True only for a real int (bool is rejected: it subclasses int)."""
    return type(value) is int


def _is_confidence(value: object) -> bool:
    """True for a finite float strictly in (0, 1] (the S8 confidence domain)."""
    if type(value) not in (int, float):
        return False
    if type(value) is bool:
        return False
    try:
        if not math.isfinite(float(value)):
            return False
    except (TypeError, ValueError, ArithmeticError):
        return False
    return 0.0 < float(value) <= 1.0


def _validate_quantitative_claim_shape(claim: object) -> str | None:
    """Return a distinct reason if a present ``quantitative_claim`` is malformed.

    A well-formed claim is a dict with a recognized ``metric_key``, a
    ``confidence`` strictly in (0, 1], a short ``kind`` label, and EXACTLY one
    of: a single ``predicted_value`` int, OR a ``predicted_low``/``predicted_high``
    int pair with low <= high. Returns None when the shape is valid.
    """
    if type(claim) is not dict:
        return "invalid_quantitative_claim"
    if claim.get("metric_key") not in _RECOGNIZED_METRIC_KEYS:
        return "invalid_quantitative_claim"
    if not _is_confidence(claim.get("confidence")):
        return "invalid_quantitative_claim"
    if not _is_short_nonempty_str(claim.get("kind"), 32):
        return "invalid_quantitative_claim"
    has_value = "predicted_value" in claim
    has_range = "predicted_low" in claim or "predicted_high" in claim
    if has_value == has_range:
        # Must carry EXACTLY one of a single value or a low/high range.
        return "invalid_quantitative_claim"
    if has_value:
        if not _is_int_not_bool(claim.get("predicted_value")):
            return "invalid_quantitative_claim"
    else:
        low = claim.get("predicted_low")
        high = claim.get("predicted_high")
        if not (_is_int_not_bool(low) and _is_int_not_bool(high)):
            return "invalid_quantitative_claim"
        if low > high:
            return "invalid_quantitative_claim"
    return None


def _validate_numeric_error_shape(err: object) -> str | None:
    """Return a distinct reason if a present ``numeric_error`` is malformed.

    A well-formed numeric_error is a dict with a recognized ``metric_key``, a
    ``measured`` int, an ``absolute_error`` finite non-negative number, a
    ``relative_error`` that is either null or a finite non-negative number, a
    ``within_range`` bool, a ``confidence`` in (0, 1], and a ``predicted`` echo
    (an int OR a two-int [low, high] list). Returns None when valid.
    """
    if type(err) is not dict:
        return "invalid_numeric_error"
    if err.get("metric_key") not in _RECOGNIZED_METRIC_KEYS:
        return "invalid_numeric_error"
    if not _is_int_not_bool(err.get("measured")):
        return "invalid_numeric_error"
    if not _is_finite_non_negative_number(err.get("absolute_error")):
        return "invalid_numeric_error"
    rel = err.get("relative_error")
    if rel is not None and not _is_finite_non_negative_number(rel):
        return "invalid_numeric_error"
    if type(err.get("within_range")) is not bool:
        return "invalid_numeric_error"
    if not _is_confidence(err.get("confidence")):
        return "invalid_numeric_error"
    predicted = err.get("predicted")
    if _is_int_not_bool(predicted):
        pass
    elif type(predicted) is list and len(predicted) == 2 \
            and all(_is_int_not_bool(p) for p in predicted):
        pass
    else:
        return "invalid_numeric_error"
    return None


def _validate_followup_decision_shape(followup: object) -> str | None:
    """Return a distinct reason if a present ``followup_decision`` is malformed.

    A well-formed followup_decision is a dict citing the prior decision id
    (``prior_decision_id`` as a 'D-NNNN' string), referencing the prior outcome
    (a non-empty ``prior_outcome`` string), a ``chose`` bool, and a non-empty
    ``why``. Returns None when valid.
    """
    if type(followup) is not dict:
        return "invalid_followup_decision"
    if _id_to_seq(followup.get("prior_decision_id")) is None:
        return "invalid_followup_decision"
    if not _is_nonempty_str(followup.get("prior_outcome")):
        return "invalid_followup_decision"
    if type(followup.get("chose")) is not bool:
        return "invalid_followup_decision"
    if not _is_nonempty_str(followup.get("why")):
        return "invalid_followup_decision"
    return None


def _validate_announced_next_shape(announced: object) -> str | None:
    """Return a distinct reason if a present ``announced_next`` is malformed.

    A well-formed announced_next is a dict with ``source_decision_id`` (a
    'D-NNNN' string), ``lesson_text`` (a non-empty string), and
    ``announced_action`` (a non-empty string). The verbatim lesson match and
    the existence of the cited prior are enforced in validate_record (where the
    priors are available). Returns None when the shape is valid.
    """
    if type(announced) is not dict:
        return "invalid_announced_next"
    if _id_to_seq(announced.get("source_decision_id")) is None:
        return "invalid_announced_next"
    if not _is_nonempty_str(announced.get("lesson_text")):
        return "invalid_announced_next"
    if not _is_nonempty_str(announced.get("announced_action")):
        return "invalid_announced_next"
    return None


def _validate_anchor_free_shape(anchor_free: object) -> str | None:
    """Return a distinct reason if a present ``anchor_free`` is not a bool.

    The R3-H5 anchor-free marker is a plain bool. It is validated SHAPE-ONLY
    and GATED on presence in validate_record, so a record omitting it validates
    exactly as before. Returns None when valid.
    """
    if type(anchor_free) is not bool:
        return "invalid_anchor_free"
    return None


def _is_rank_int(value: object) -> bool:
    """True only for a real int >= 1 (bool rejected: it subclasses int)."""
    return type(value) is int and value >= 1


def _validate_rerank_shape(rerank: object) -> str | None:
    """Return a distinct reason if a present ``rerank`` block is malformed.

    A well-formed rerank is a dict carrying:

    * ``prior_outcome_decision_id`` - a 'D-NNNN' string (the citation whose
      existence/finalized-measured status is checked in validate_record);
    * ``prior_outcome`` - a dict echoing the MEASURED outcome the second pass
      was fed, carrying at minimum a recognized ``metric_key`` + an int
      ``measured`` + a bool ``within_range`` AND/OR an ``outcome_verdict`` of
      'HIT'/'MISS', so the feed is checkable from the record alone;
    * a ``candidates`` OR ``passes`` list (non-empty) where each entry has a
      non-empty ``candidate`` str, an int>=1 ``old_rank`` (first pass) and an
      int>=1 ``new_rank`` (second pass);
    * a bool ``diverged_from_announced`` and a non-empty ``why``.

    Returns None when the shape is valid.
    """
    if type(rerank) is not dict:
        return "invalid_rerank"
    if _id_to_seq(rerank.get("prior_outcome_decision_id")) is None:
        return "invalid_rerank"

    prior_outcome = rerank.get("prior_outcome")
    if type(prior_outcome) is not dict:
        return "invalid_rerank"
    # The echoed prior outcome must carry at least one checkable measured
    # signal: a recognized metric_key + int measured + bool within_range, OR a
    # valid HIT/MISS verdict. A summary that carries neither is not a usable
    # echo of a measured outcome.
    has_numeric_echo = (
        prior_outcome.get("metric_key") in _RECOGNIZED_METRIC_KEYS
        and _is_int_not_bool(prior_outcome.get("measured"))
        and type(prior_outcome.get("within_range")) is bool
    )
    has_verdict_echo = prior_outcome.get("outcome_verdict") in _OUTCOME_VERDICTS
    if not (has_numeric_echo or has_verdict_echo):
        return "invalid_rerank"

    entries = rerank.get("candidates")
    if entries is None:
        entries = rerank.get("passes")
    if type(entries) is not list or not entries:
        return "invalid_rerank"
    for entry in entries:
        if type(entry) is not dict:
            return "invalid_rerank"
        if not _is_nonempty_str(entry.get("candidate")):
            return "invalid_rerank"
        if not _is_rank_int(entry.get("old_rank")):
            return "invalid_rerank"
        if not _is_rank_int(entry.get("new_rank")):
            return "invalid_rerank"

    if type(rerank.get("diverged_from_announced")) is not bool:
        return "invalid_rerank"
    if not _is_nonempty_str(rerank.get("why")):
        return "invalid_rerank"
    return None


def _prior_carries_measured_outcome(prior: dict) -> bool:
    """True iff a FINALIZED prior carries a measured outcome.

    A measured outcome is a numeric_error dict and/or an outcome_verdict of
    'HIT'/'MISS'. Used by the rerank_missing_prior_outcome_citation rule to
    prove the cited prior was actually a finalized, measured record.
    """
    if type(prior) is not dict or prior.get("status") != STATUS_FINALIZED:
        return False
    if type(prior.get("numeric_error")) is dict:
        return True
    if prior.get("outcome_verdict") in _OUTCOME_VERDICTS:
        return True
    return False


def _followup_trigger_from_prior(prior: dict) -> str | None:
    """Return a short trigger description if a prior FINALIZED record carries an
    S8 trigger (over-range numeric_error OR a non-empty findings list), else None.

    A trigger exists ONLY when the record actually carries the S8 fields and one
    of them indicates a condition to follow up on: ``numeric_error.within_range``
    is False (the error exceeded the stated range) OR a non-empty ``findings``
    list. Pre-S8 records (no numeric_error, no findings) never trigger, so
    D-0001..D-0012 are entirely unaffected by the follow-up-on-finding rule.
    """
    if type(prior) is not dict or prior.get("status") != STATUS_FINALIZED:
        return None
    numeric_error = prior.get("numeric_error")
    if type(numeric_error) is dict and numeric_error.get("within_range") is False:
        return "over_range_numeric_error"
    findings = prior.get("findings")
    if type(findings) is list and len(findings) > 0:
        return "artifact_finding"
    return None


def validate_record(
    record: dict, *, prior_records: tuple[dict, ...] | list[dict],
    artifacts_resolution_root: str | Path | None = None,
) -> DecisionValidation:
    """Validate a PREDICTED/FINALIZED record against its earlier records.

    ``prior_records`` are the records with a lower sequence number than this
    one (earlier decisions). Each rejection has a distinct reason string.

    R2-S6 artifact rule (ADDITIVE, backward-compatible)
    ---------------------------------------------------
    A FINALIZED record MAY carry two optional fields describing the real
    analysis artifact the action executor produced: ``artifact_path`` (stored
    relative to the decisions dir, e.g. ``artifacts/D-0004/inventory.json``) and
    ``artifact_sha256`` (the lowercase hex sha256 of the artifact bytes).

    Resolution convention: when ``artifacts_resolution_root`` is given, the
    ``artifact_path`` is resolved RELATIVE TO IT (the root is the decisions dir
    the record lives in). Only then is the on-disk artifact checked:

    * ``artifact_missing``        - the resolved file does not exist.
    * ``artifact_sha256_mismatch`` - the file exists but its recomputed sha256
      does not equal the recorded ``artifact_sha256``.

    This rule is STRICTLY additive and backward-compatible:

    * A record WITHOUT both artifact fields validates exactly as before (every
      pre-existing R2-S2 / R2-S4 fixture is unaffected).
    * A PREDICTED record is never subject to the rule.
    * When ``artifacts_resolution_root`` is None (the safe default used by every
      existing caller, including ``create_record`` and ``finalize_record`` and
      every ``validate_record(record, prior_records=...)`` test call) the
      artifact file is NOT resolved and the existence/sha256 check is SKIPPED,
      so existing callers and fixtures keep passing unchanged. The shape of the
      fields (string path, 64-hex sha256) is still validated whenever present.
    """
    if type(record) is not dict:
        return DecisionValidation(False, "record_not_object")
    # Field-set check, relaxed additively for the OPTIONAL R2-S3 fields: every
    # required FIELD_NAMES key must be present, and no key outside
    # FIELD_NAMES + OPTIONAL_FIELD_NAMES is allowed. A record that supplies
    # exactly FIELD_NAMES (the R2-S2 shape) still validates unchanged.
    keys = set(record)
    if not set(FIELD_NAMES).issubset(keys):
        return DecisionValidation(False, "field_set_mismatch")
    if not keys.issubset(set(FIELD_NAMES) | set(OPTIONAL_FIELD_NAMES)):
        return DecisionValidation(False, "field_set_mismatch")

    # Validate the optional provenance fields only when present.
    if "provider" in record:
        provider = record["provider"]
        if not _is_short_nonempty_str(provider, 32) \
                or provider not in ALLOWED_PROVIDERS:
            return DecisionValidation(False, "invalid_provider")
    if "model" in record and not _is_short_nonempty_str(record["model"], 128):
        return DecisionValidation(False, "invalid_model")
    if "estimated_credit_cost_usd" in record \
            and not _is_finite_non_negative_number(
                record["estimated_credit_cost_usd"]):
        return DecisionValidation(False, "invalid_estimated_credit_cost_usd")

    # R2-S6 additive artifact-reference fields. Validate shape ONLY when
    # present (a record without them is unaffected). The existence/sha256 file
    # check happens later (FINALIZED records only, when a resolution root is
    # supplied).
    if "artifact_path" in record \
            and not _is_nonempty_str(record["artifact_path"]):
        return DecisionValidation(False, "invalid_artifact_path")
    if "artifact_sha256" in record:
        digest = record["artifact_sha256"]
        if type(digest) is not str or _SHA256_RE.match(digest) is None:
            return DecisionValidation(False, "invalid_artifact_sha256")

    # R2-S7 additive outcome_verdict shape. Validate ONLY when present (a record
    # omitting it is unaffected). Must be exactly 'HIT' or 'MISS'.
    if "outcome_verdict" in record \
            and record["outcome_verdict"] not in _OUTCOME_VERDICTS:
        return DecisionValidation(False, "invalid_outcome_verdict")

    # R2-S8 additive falsifiable-prediction fields. Each is validated ONLY when
    # present and only for SHAPE here, with a DISTINCT reason string; a record
    # that omits every one of them validates exactly as before (backward
    # compatible with every pre-S8 record/fixture/test).
    if "falsifiable" in record and type(record["falsifiable"]) is not bool:
        return DecisionValidation(False, "invalid_falsifiable")
    # R2-S9 additive marker shape: validate ONLY when present (a record omitting
    # it is unaffected). Must be a real bool (distinct reason).
    if "executed_matches_chosen" in record \
            and type(record["executed_matches_chosen"]) is not bool:
        return DecisionValidation(False, "invalid_executed_matches_chosen")
    if "quantitative_claim" in record:
        reason = _validate_quantitative_claim_shape(record["quantitative_claim"])
        if reason is not None:
            return DecisionValidation(False, reason)
    if "numeric_error" in record:
        reason = _validate_numeric_error_shape(record["numeric_error"])
        if reason is not None:
            return DecisionValidation(False, reason)
    if "followup_decision" in record:
        reason = _validate_followup_decision_shape(record["followup_decision"])
        if reason is not None:
            return DecisionValidation(False, reason)
    if "findings" in record:
        findings = record["findings"]
        if type(findings) is not list \
                or not all(_is_nonempty_str(f) for f in findings):
            return DecisionValidation(False, "invalid_findings")

    # R3-H4 additive re-rank fields. Validate SHAPE ONLY when present, each with
    # a DISTINCT reason; a record omitting both validates exactly as before.
    if "announced_next" in record:
        reason = _validate_announced_next_shape(record["announced_next"])
        if reason is not None:
            return DecisionValidation(False, reason)
    if "rerank" in record:
        reason = _validate_rerank_shape(record["rerank"])
        if reason is not None:
            return DecisionValidation(False, reason)

    # R3-H5 additive anchor-free marker. Validate SHAPE ONLY when present (a
    # non-bool is rejected with the distinct reason 'invalid_anchor_free'); a
    # record omitting it validates exactly as before.
    if "anchor_free" in record:
        reason = _validate_anchor_free_shape(record["anchor_free"])
        if reason is not None:
            return DecisionValidation(False, reason)

    # R2-S4 additive timestamps. Each is OPTIONAL; when present it MUST be a
    # normalized UTC ISO 'Z' instant (parsed via resource_ledger._parse_utc_iso,
    # fail closed on any non-normalized value). When BOTH are present the
    # prediction must precede the outcome (equal instants are allowed; a
    # strictly earlier outcome is rejected).
    predicted_instant = None
    outcome_instant = None
    if "predicted_at" in record:
        predicted_instant = _parse_utc_iso(record["predicted_at"])
        if predicted_instant is None:
            return DecisionValidation(False, "invalid_predicted_at")
    if "outcome_at" in record:
        outcome_instant = _parse_utc_iso(record["outcome_at"])
        if outcome_instant is None:
            return DecisionValidation(False, "invalid_outcome_at")
    if predicted_instant is not None and outcome_instant is not None \
            and outcome_instant < predicted_instant:
        return DecisionValidation(False, "outcome_precedes_prediction")

    seq = _id_to_seq(record["id"])
    if seq is None:
        return DecisionValidation(False, "invalid_id_format")

    if record["status"] not in (STATUS_PREDICTED, STATUS_FINALIZED):
        return DecisionValidation(False, "invalid_status")

    # R2-S6 artifact existence + integrity (ADDITIVE). Only a FINALIZED record
    # that carries BOTH artifact fields is subject to the on-disk check, and
    # only when a resolution root is supplied (the safe default None skips it,
    # keeping every existing caller/fixture unchanged).
    if record["status"] == STATUS_FINALIZED \
            and "artifact_path" in record and "artifact_sha256" in record \
            and artifacts_resolution_root is not None:
        resolved = Path(artifacts_resolution_root) / record["artifact_path"]
        if not resolved.is_file():
            return DecisionValidation(False, "artifact_missing")
        actual_digest = _file_sha256(resolved)
        if actual_digest is None:
            return DecisionValidation(False, "artifact_missing")
        if actual_digest != record["artifact_sha256"]:
            return DecisionValidation(False, "artifact_sha256_mismatch")

    # Build the ordered map of earlier records by sequence for citation checks.
    prior_by_seq: dict[int, dict] = {}
    for prior in prior_records:
        prior_seq = _id_to_seq(prior.get("id")) if type(prior) is dict else None
        if prior_seq is None:
            return DecisionValidation(False, "invalid_prior_id")
        prior_by_seq[prior_seq] = prior

    # (e) non-sequential id: must be exactly one greater than the highest
    # earlier record (D-0001 when there are no priors).
    expected_seq = (max(prior_by_seq) + 1) if prior_by_seq else 1
    if seq != expected_seq:
        return DecisionValidation(False, "non_sequential_id")

    # (a) fewer than 3 candidates.
    candidates = record["candidates"]
    if type(candidates) is not list or len(candidates) < 3:
        return DecisionValidation(False, "fewer_than_three_candidates")
    for candidate in candidates:
        if type(candidate) is not dict or not (
            _is_nonempty_str(candidate.get("name"))
            or _is_nonempty_str(candidate.get("description"))
        ):
            return DecisionValidation(False, "candidate_missing_name")

    # (b) missing/empty scoring_criteria OR missing candidate_scores.
    scoring_criteria = record["scoring_criteria"]
    if type(scoring_criteria) is not list or not scoring_criteria:
        return DecisionValidation(False, "missing_scoring_criteria")
    candidate_scores = record["candidate_scores"]
    if type(candidate_scores) is not list or not candidate_scores:
        return DecisionValidation(False, "missing_candidate_scores")

    # (c) missing/empty prediction.
    if not _is_nonempty_str(record["prediction"]):
        return DecisionValidation(False, "missing_prediction")

    # chosen action / rationale must be present (the decision itself).
    if not _is_nonempty_str(record["chosen_action"]):
        return DecisionValidation(False, "missing_chosen_action")
    if not _is_nonempty_str(record["chosen_rationale"]):
        return DecisionValidation(False, "missing_chosen_rationale")

    # rejected must be a list of {candidate, why}.
    rejected = record["rejected"]
    if type(rejected) is not list:
        return DecisionValidation(False, "invalid_rejected")
    for entry in rejected:
        if type(entry) is not dict or not _is_nonempty_str(entry.get("why")) \
                or "candidate" not in entry:
            return DecisionValidation(False, "invalid_rejected")

    # (d1) cited prior_decision_ids_read must exist among earlier records.
    prior_read = record["prior_decision_ids_read"]
    if type(prior_read) is not list:
        return DecisionValidation(False, "invalid_prior_decision_ids_read")
    for cited in prior_read:
        cited_seq = _id_to_seq(cited)
        if cited_seq is None or cited_seq not in prior_by_seq:
            return DecisionValidation(False, "prior_decision_id_not_found")

    # lessons_applied must be a list of {decision_id, lesson}.
    lessons_applied = record["lessons_applied"]
    if type(lessons_applied) is not list:
        return DecisionValidation(False, "invalid_lessons_applied")
    for entry in lessons_applied:
        if type(entry) is not dict or set(entry) != {"decision_id", "lesson"}:
            return DecisionValidation(False, "invalid_lessons_applied")
        cited_seq = _id_to_seq(entry["decision_id"])
        # (d2) cited decision_id must exist among earlier records.
        if cited_seq is None or cited_seq not in prior_by_seq:
            return DecisionValidation(False, "lesson_decision_id_not_found")
        if not _is_nonempty_str(entry["lesson"]):
            return DecisionValidation(False, "invalid_lessons_applied")
        # (f) cited lesson text must match the earlier record's recorded lesson.
        earlier = prior_by_seq[cited_seq]
        if earlier.get("status") != STATUS_FINALIZED \
                or earlier.get("lesson") != entry["lesson"]:
            return DecisionValidation(False, "lesson_text_mismatch")

    # Scope item 3: a record with available prior lessons but no citation is
    # rejected. D-0001 (no priors) is exempt; any record with >=1 finalized
    # prior lesson must cite at least one.
    available = _prior_lessons(prior_records)
    if available and not lessons_applied:
        return DecisionValidation(False, "available_prior_lessons_not_cited")

    # R2-S7 additive 'miss_not_cited' rule (backward-compatible). When the
    # MOST-RECENT prior FINALIZED record carries outcome_verdict == 'MISS', the
    # current record MUST cite that missed record's id in lessons_applied (the
    # lesson_text_mismatch check above already enforces that the cited lesson
    # text matches that record's lesson verbatim). This makes an outcome able to
    # contradict the plan: a cycle may not silently move past a recorded MISS.
    # The rule triggers ONLY when a prior actually carries a MISS verdict, so
    # every pre-existing record (none carry outcome_verdict) is unaffected.
    if prior_by_seq:
        latest_prior = prior_by_seq[max(prior_by_seq)]
        if latest_prior.get("status") == STATUS_FINALIZED \
                and latest_prior.get("outcome_verdict") == "MISS":
            missed_id = latest_prior.get("id")
            cited_ids = {
                entry.get("decision_id") for entry in lessons_applied
                if type(entry) is dict
            }
            if missed_id not in cited_ids:
                return DecisionValidation(False, "miss_not_cited")

    # R2-S8 CREATE-TIME quantitative-claim rule (GATED for backward compat).
    # A NEW falsifiable record (one explicitly marked ``falsifiable: true``)
    # MUST carry a well-formed quantitative_claim (a recognized metric_key, a
    # numeric predicted value/range, and a confidence). Without a checkable
    # number a prediction cannot be falsified, so it is rejected here with the
    # DISTINCT reason 'prediction_missing_quantitative_claim'.
    #
    # Gating choice: the rule fires ONLY when the record carries the explicit
    # S8 marker ``falsifiable == True``. Every record WITHOUT the marker
    # (D-0001..D-0012 and all existing fixtures/tests, none of which set it) is
    # NOT subject to the rule and validates exactly as before. The
    # quantitative_claim SHAPE is already validated above whenever present, so
    # here we only need to require its PRESENCE for a marked record.
    if record.get("falsifiable") is True \
            and "quantitative_claim" not in record:
        return DecisionValidation(
            False, "prediction_missing_quantitative_claim")

    # R2-S9 CREATE-TIME executed-matches-chosen rule (GATED for backward compat).
    # A NEW S9 record (one explicitly marked ``executed_matches_chosen: true``)
    # REQUIRES its chosen_action to map to a SUPPORTED executor analysis. If the
    # chosen_action would fall back to the inventory default (the executor could
    # not faithfully run what was chosen), the record is rejected here with the
    # DISTINCT reason 'chosen_action_would_fall_back' so the cycle never
    # silently runs inventory in place of the chosen analysis.
    #
    # Gating choice: the rule fires ONLY when the record carries the explicit
    # marker ``executed_matches_chosen == True``. Every record WITHOUT the
    # marker (D-0001..D-0015 and all existing fixtures/tests, several of which
    # are fallback=true) is NOT subject to the rule and validates exactly as
    # before. The executor's mapper is imported LAZILY here so this module stays
    # import-light/offline and there is a single source of truth for the mapping
    # (no top-level cross-import cycle with action_executor).
    if record.get("executed_matches_chosen") is True:
        from . import action_executor
        _analysis, fallback = action_executor.map_action_to_analysis(
            record.get("chosen_action"))
        if fallback:
            return DecisionValidation(False, "chosen_action_would_fall_back")

    # R3-H4 announced_next verbatim-lesson rule (GATED on presence). When the
    # record carries an announced_next block, its cited source_decision_id must
    # exist among the priors and be a FINALIZED record whose ``lesson`` matches
    # ``announced_next.lesson_text`` byte-for-byte (the same discipline as
    # lesson_text_mismatch). A record WITHOUT announced_next is never subject to
    # this rule, so D-0001..D-0018 and all fixtures are unaffected.
    if "announced_next" in record:
        announced = record["announced_next"]
        src_seq = _id_to_seq(announced.get("source_decision_id"))
        src_prior = prior_by_seq.get(src_seq) if src_seq is not None else None
        if type(src_prior) is not dict \
                or src_prior.get("status") != STATUS_FINALIZED \
                or src_prior.get("lesson") != announced.get("lesson_text"):
            return DecisionValidation(False, "announced_next_lesson_mismatch")

    # R3-H4 REQUIRED-CITATION rule (GATED on presence of ``rerank``). A record
    # carrying a rerank block MUST cite, via prior_outcome_decision_id, a prior
    # that EXISTS among the priors AND is FINALIZED carrying a measured outcome
    # (numeric_error and/or outcome_verdict). The second pass is defined as
    # being fed that prior measured outcome, so a rerank block that cannot point
    # at a real measured prior is REJECTED. A record WITHOUT a rerank key never
    # triggers the rule, so every pre-existing record/fixture is unaffected.
    if "rerank" in record:
        rerank = record["rerank"]
        cited_seq = _id_to_seq(rerank.get("prior_outcome_decision_id")) \
            if type(rerank) is dict else None
        cited_prior = prior_by_seq.get(cited_seq) if cited_seq is not None else None
        if cited_prior is None or not _prior_carries_measured_outcome(cited_prior):
            return DecisionValidation(
                False, "rerank_missing_prior_outcome_citation")

    # R3-H4 divergence-honesty rule (GATED on BOTH announced_next and rerank
    # present with a divergence claim). When the rerank claims
    # diverged_from_announced is True, the record's chosen_action must map to a
    # DIFFERENT analysis than announced_next.announced_action maps to, and
    # rerank.why must be non-empty. This only validates that a CLAIMED
    # divergence is REAL; it never forces divergence. The executor mapper is
    # imported LAZILY (mirroring the S9 rule) so this module stays import-light.
    if "announced_next" in record and "rerank" in record \
            and type(record["rerank"]) is dict \
            and record["rerank"].get("diverged_from_announced") is True:
        from . import action_executor
        announced_action = record["announced_next"].get("announced_action")
        announced_analysis, _a_fb = action_executor.map_action_to_analysis(
            announced_action)
        chosen_analysis, _c_fb = action_executor.map_action_to_analysis(
            record.get("chosen_action"))
        if chosen_analysis == announced_analysis \
                or not _is_nonempty_str(record["rerank"].get("why")):
            return DecisionValidation(
                False, "divergence_not_reflected_in_choice")

    # R2-S8 FOLLOW-UP-ON-FINDING rule (ADDITIVE, backward-compatible). When the
    # MOST-RECENT prior FINALIZED record carries the S8 numeric_error/finding
    # fields AND indicates a trigger (numeric_error.within_range is False, i.e.
    # the error exceeded its stated range, OR a non-empty findings list), the
    # current record MUST carry a conforming followup_decision that cites the
    # prior id, references the prior outcome, and states chose=true/false with a
    # non-empty why. If the trigger exists and no conforming followup_decision
    # is present, reject with the DISTINCT reason 'followup_finding_not_addressed'.
    #
    # Backward compatibility: a prior WITHOUT the S8 fields never triggers (see
    # _followup_trigger_from_prior), so D-0001..D-0012 are unaffected. The
    # followup_decision SHAPE is already validated above when present; here we
    # additionally require that, when a trigger exists, the followup actually
    # cites the triggering prior's id.
    if prior_by_seq:
        latest_prior = prior_by_seq[max(prior_by_seq)]
        trigger = _followup_trigger_from_prior(latest_prior)
        if trigger is not None:
            followup = record.get("followup_decision")
            if type(followup) is not dict \
                    or followup.get("prior_decision_id") != latest_prior.get("id"):
                return DecisionValidation(
                    False, "followup_finding_not_addressed")

    return DecisionValidation(True, "valid")


def validate_finalized_edit(old_record: dict, new_record: dict) -> DecisionValidation:
    """Reject any edit to an already-FINALIZED record.

    If ``old_record`` is FINALIZED, ``new_record`` must equal it exactly. This
    backs the 'edits to a finalized record' rejection rule. (The first
    PREDICTED->FINALIZED transition is handled by finalize_record directly.)
    """
    if type(old_record) is not dict or type(new_record) is not dict:
        return DecisionValidation(False, "record_not_object")
    if old_record.get("status") == STATUS_FINALIZED and new_record != old_record:
        return DecisionValidation(False, "edit_to_finalized_record")
    return DecisionValidation(True, "valid")


# --------------------------------------------------------------------------- #
# Prior record loading / lessons
# --------------------------------------------------------------------------- #
def _records_dir(decisions_dir: str | Path) -> Path:
    return Path(decisions_dir)


def load_records(decisions_dir: str | Path) -> list[dict]:
    """Load all D-NNNN.json records sorted ascending by sequence number."""
    directory = _records_dir(decisions_dir)
    records: list[tuple[int, dict]] = []
    if not directory.is_dir():
        return []
    for path in directory.iterdir():
        if not path.is_file() or not path.name.endswith(".json"):
            continue
        seq = _id_to_seq(path.stem)
        if seq is None:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if type(data) is not dict:
            raise ValueError("corrupt decision record: " + path.name)
        records.append((seq, data))
    records.sort(key=lambda item: item[0])
    return [data for _, data in records]


def _prior_lessons(prior_records: tuple[dict, ...] | list[dict]) -> list[dict]:
    """Structured {decision_id, lesson} for finalized priors with a lesson."""
    lessons: list[dict] = []
    ordered = sorted(
        (r for r in prior_records if type(r) is dict and _id_to_seq(r.get("id"))),
        key=lambda r: _id_to_seq(r["id"]),
    )
    for record in ordered:
        if record.get("status") == STATUS_FINALIZED \
                and _is_nonempty_str(record.get("lesson")):
            lessons.append(
                {"decision_id": record["id"], "lesson": record["lesson"]}
            )
    return lessons


def load_prior_lessons(decisions_dir: str | Path) -> list[dict]:
    """Next-action selection helper for the planner prompt.

    Returns the structured list of {decision_id, lesson} gathered from all
    FINALIZED records that have a non-null lesson, oldest first. This performs
    NO model or network call; the caller feeds the list into the planner.
    """
    return _prior_lessons(load_records(decisions_dir))


def available_prior_lessons_not_cited(
    record: dict, prior_records: tuple[dict, ...] | list[dict],
) -> bool:
    """True if earlier finalized lessons exist but the record cites none."""
    if type(record) is not dict:
        return False
    available = _prior_lessons(prior_records)
    cited = record.get("lessons_applied")
    return bool(available) and not (type(cited) is list and cited)


# --------------------------------------------------------------------------- #
# Korean one-line summary for DECISIONS.md
# --------------------------------------------------------------------------- #
def _summary_line(record: dict) -> str:
    """Build the single Korean summary line for a record (no secrets)."""
    chosen = str(record.get("chosen_action", "")).replace("\n", " ").strip()
    prediction = str(record.get("prediction", "")).replace("\n", " ").strip()
    return "- %s: 선택=%s | 예측=%s" % (record["id"], chosen, prediction)


def _append_summary_line(decisions_dir: str | Path, record: dict) -> None:
    """Append one Korean summary line to DECISIONS.md (created if needed)."""
    md_path = _records_dir(decisions_dir) / DECISIONS_MD
    header = (
        "# SPRAY 의사결정 기록 (DECISIONS)\n\n"
        "의사결정 하나당 한국어 요약 한 줄. 상세 내용은 각 `D-NNNN.json` 참조.\n\n"
    )
    if md_path.is_file():
        existing = md_path.read_text(encoding="utf-8")
    else:
        existing = header
    if not existing.endswith("\n"):
        existing += "\n"
    existing += _summary_line(record) + "\n"
    _atomic_write_text(md_path, existing)


# --------------------------------------------------------------------------- #
# Atomic writes
# --------------------------------------------------------------------------- #
def _atomic_write_text(path: Path, text: str) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _write_record(decisions_dir: str | Path, record: dict) -> Path:
    path = _records_dir(decisions_dir) / (record["id"] + ".json")
    _atomic_write_text(path, _dumps(record) + "\n")
    return path


# --------------------------------------------------------------------------- #
# Create / finalize
# --------------------------------------------------------------------------- #
def create_record(
    decisions_dir: str | Path,
    *,
    id: str | None = None,
    observed_state,
    prior_decision_ids_read,
    candidates,
    scoring_criteria,
    candidate_scores,
    chosen_action,
    chosen_rationale,
    rejected,
    prediction,
    lessons_applied,
    provider=None,
    model=None,
    estimated_credit_cost_usd=None,
    predicted_at=None,
    falsifiable=None,
    quantitative_claim=None,
    followup_decision=None,
    executed_matches_chosen=None,
    announced_next=None,
    rerank=None,
    anchor_free=None,
) -> dict:
    """Write a new PREDICTED record (prediction committed before any outcome).

    The next sequential id is computed from the existing D-NNNN.json files when
    ``id`` is None. The record is validated BEFORE it is written, so an invalid
    create is refused and nothing is persisted. Refuses to overwrite an
    existing record (append-only). Appends one Korean summary line to
    DECISIONS.md.

    The OPTIONAL ``provider`` / ``model`` / ``estimated_credit_cost_usd``
    (R2-S3) and ``predicted_at`` (R2-S4) arguments are keyword-only with None
    defaults: when omitted the record keeps the exact prior byte-stable shape.
    ``predicted_at`` is committed here and is immutable at finalize.

    The OPTIONAL R2-S8 ``falsifiable`` marker, ``quantitative_claim`` (the
    structured checkable claim), and ``followup_decision`` (the chose/reject of
    a prior finding) are likewise keyword-only with None defaults and are
    create-time / immutable at finalize. A ``falsifiable=True`` record without a
    ``quantitative_claim`` is refused at create (validated before writing).

    The OPTIONAL R2-S9 ``executed_matches_chosen`` marker is likewise
    keyword-only with a None default and is create-time / immutable at finalize.
    An ``executed_matches_chosen=True`` record whose ``chosen_action`` would
    fall back to the inventory default is refused at create (validated before
    writing) with the reason 'chosen_action_would_fall_back'.

    The OPTIONAL R3-H4 ``announced_next`` (what the cited prior lesson said to
    do next) and ``rerank`` (the second scoring pass fed the prior measured
    outcome) are likewise keyword-only with None defaults and are create-time /
    immutable at finalize. When supplied they are validated before writing: an
    ``announced_next`` whose lesson_text does not match the cited prior verbatim
    is refused, and a ``rerank`` that does not cite a finalized measured prior
    is refused with 'rerank_missing_prior_outcome_citation'.

    The OPTIONAL R3-H5 ``anchor_free`` marker (bool) records that the re-scoring
    pass ran WITHOUT the prior lesson's announced-next directive in the planner
    prompt. It is likewise keyword-only with a None default and is create-time /
    immutable at finalize. A non-bool ``anchor_free`` is refused at create with
    the reason 'invalid_anchor_free'.
    """
    prior_records = load_records(decisions_dir)

    if id is None:
        next_seq = (max((_id_to_seq(r["id"]) for r in prior_records), default=0)) + 1
        record_id = _seq_to_id(next_seq)
    else:
        if _id_to_seq(id) is None:
            raise ValueError("invalid decision id")
        record_id = id

    record = {
        "id": record_id,
        "observed_state": observed_state,
        "prior_decision_ids_read": list(prior_decision_ids_read),
        "candidates": list(candidates),
        "scoring_criteria": list(scoring_criteria),
        "candidate_scores": list(candidate_scores),
        "chosen_action": chosen_action,
        "chosen_rationale": chosen_rationale,
        "rejected": list(rejected),
        "prediction": prediction,
        "status": STATUS_PREDICTED,
        "actual_outcome": None,
        "prediction_vs_actual": None,
        "lesson": None,
        "lessons_applied": list(lessons_applied),
    }

    # R2-S3 additive provenance fields: include ONLY when supplied so a record
    # created without them keeps the exact R2-S2 shape (byte-stable JSON).
    if provider is not None:
        record["provider"] = provider
    if model is not None:
        record["model"] = model
    if estimated_credit_cost_usd is not None:
        record["estimated_credit_cost_usd"] = estimated_credit_cost_usd
    # R2-S4 additive: the prediction timestamp is committed at create time and
    # is immutable at finalize. Include ONLY when supplied so a record created
    # without it keeps the exact prior shape.
    if predicted_at is not None:
        record["predicted_at"] = predicted_at
    # R2-S8 additive: the falsifiable marker, the committed quantitative_claim,
    # and (when a prior trigger required addressing) the followup_decision are
    # all create-time and immutable at finalize. Include ONLY when supplied so a
    # record created without them keeps the exact prior byte-stable shape.
    if falsifiable is not None:
        record["falsifiable"] = falsifiable
    if quantitative_claim is not None:
        record["quantitative_claim"] = quantitative_claim
    if followup_decision is not None:
        record["followup_decision"] = followup_decision
    # R2-S9 additive: the executed-matches-chosen marker is create-time and
    # immutable at finalize. Include ONLY when supplied so a record created
    # without it keeps the exact prior byte-stable shape.
    if executed_matches_chosen is not None:
        record["executed_matches_chosen"] = executed_matches_chosen
    # R3-H4 additive: announced_next (what the cited prior lesson said to do
    # next) and rerank (the second scoring pass fed the prior measured outcome)
    # are both create-time and immutable at finalize. Include ONLY when supplied
    # so a record created without them keeps the exact prior byte-stable shape.
    if announced_next is not None:
        record["announced_next"] = announced_next
    if rerank is not None:
        record["rerank"] = rerank
    # R3-H5 additive: the anchor-free experiment marker is create-time and
    # immutable at finalize (NOT in _FINALIZE_FIELDS). Include ONLY when
    # supplied so a record created without it keeps the exact prior byte-stable
    # shape.
    if anchor_free is not None:
        record["anchor_free"] = anchor_free

    result = validate_record(record, prior_records=prior_records)
    if not result.valid:
        raise ValueError("invalid decision record: " + result.reason)

    path = _records_dir(decisions_dir) / (record_id + ".json")
    if path.exists():
        raise ValueError("decision record already exists: " + record_id)

    _write_record(decisions_dir, record)
    _append_summary_line(decisions_dir, record)
    return record


def finalize_record(
    decisions_dir: str | Path,
    record_id: str,
    *,
    actual_outcome,
    prediction_vs_actual,
    lesson,
    outcome_at=None,
    artifact_path=None,
    artifact_sha256=None,
    outcome_verdict=None,
    numeric_error=None,
    findings=None,
) -> dict:
    """Fill ONLY outcome/comparison/lesson and flip status to FINALIZED.

    Refuses to finalize an already-FINALIZED record and refuses to alter any
    field other than actual_outcome / prediction_vs_actual / lesson / status
    (plus the OPTIONAL R2-S4 ``outcome_at`` timestamp and the R2-S6
    ``artifact_path`` / ``artifact_sha256`` references when supplied). The
    output is reconstructed from the loaded record with only those keys
    changed, and every other key is asserted byte-for-byte unchanged, so
    ``predicted_at`` and the R2-S3 provenance fields stay immutable.

    ``outcome_at`` / ``artifact_path`` / ``artifact_sha256`` /
    ``outcome_verdict`` are keyword-only with None defaults: a finalize that
    omits them behaves exactly as before (no such key is added), keeping the
    unedited R2-S2 test passing unchanged.
    The validator re-verifies the artifact only when given a resolution root,
    so finalize itself re-resolves the artifact from the decisions dir to prove
    the just-recorded path+sha256 are internally consistent.
    """
    path = _records_dir(decisions_dir) / (record_id + ".json")
    if not path.is_file():
        raise ValueError("decision record not found: " + record_id)
    old_record = json.loads(path.read_text(encoding="utf-8"))
    if type(old_record) is not dict:
        raise ValueError("corrupt decision record: " + record_id)

    if old_record.get("status") == STATUS_FINALIZED:
        raise ValueError("cannot finalize an already finalized record")
    if old_record.get("status") != STATUS_PREDICTED:
        raise ValueError("can only finalize a PREDICTED record")

    if not _is_nonempty_str(lesson):
        raise ValueError("finalize requires a non-empty lesson")

    # Reconstruct: copy the loaded record, change only the allowed keys.
    new_record = dict(old_record)
    new_record["actual_outcome"] = actual_outcome
    new_record["prediction_vs_actual"] = prediction_vs_actual
    new_record["lesson"] = lesson
    new_record["status"] = STATUS_FINALIZED
    # R2-S4 additive: stamp the outcome time ONLY when supplied. A finalize
    # that omits it adds no key, preserving the prior byte-stable shape.
    if outcome_at is not None:
        new_record["outcome_at"] = outcome_at
    # R2-S6 additive: record the real artifact reference ONLY when both are
    # supplied together. Partial references are refused so a record never
    # carries a path without its integrity digest (or vice versa).
    if (artifact_path is None) != (artifact_sha256 is None):
        raise ValueError(
            "artifact_path and artifact_sha256 must be supplied together")
    if artifact_path is not None:
        new_record["artifact_path"] = artifact_path
        new_record["artifact_sha256"] = artifact_sha256
    # R2-S7 additive: record the HIT/MISS verdict ONLY when supplied. A finalize
    # that omits it adds no key, preserving the prior byte-stable shape.
    if outcome_verdict is not None:
        new_record["outcome_verdict"] = outcome_verdict
    # R2-S8 additive: the computed numeric_error and the executor findings are
    # written ONLY at finalize. Include ONLY when supplied so a finalize that
    # omits them adds no key, preserving the prior byte-stable shape.
    if numeric_error is not None:
        new_record["numeric_error"] = numeric_error
    if findings is not None:
        new_record["findings"] = findings

    # Assert every non-allowed field is byte-for-byte unchanged. This covers
    # the required FIELD_NAMES and the OPTIONAL R2-S3 provenance fields, which
    # are set at create time and must never change at finalize.
    for key in (*FIELD_NAMES, *OPTIONAL_FIELD_NAMES):
        if key in _FINALIZE_FIELDS:
            continue
        if _dumps({key: new_record.get(key)}) != _dumps({key: old_record.get(key)}):
            raise ValueError("finalize may not alter field: " + key)

    # Re-validate against earlier records. When the record carries an artifact
    # reference, resolve it from the decisions dir so the just-recorded
    # path+sha256 are proven to match a real file on disk before persisting.
    prior_records = [
        r for r in load_records(decisions_dir)
        if _id_to_seq(r["id"]) < _id_to_seq(record_id)
    ]
    resolution_root = decisions_dir if "artifact_path" in new_record else None
    result = validate_record(
        new_record, prior_records=prior_records,
        artifacts_resolution_root=resolution_root)
    if not result.valid:
        raise ValueError("invalid finalized record: " + result.reason)

    _write_record(decisions_dir, new_record)
    return new_record
