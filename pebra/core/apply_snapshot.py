"""apply_snapshot (M5b) — pure learned-override reapplication.

Takes a (read-port-decoded) SnapshotBundle of learned facts and the raw AssessmentInput, and returns
an adjusted copy where matching facts have OVERRIDDEN the predicted risk probabilities (p_success,
p_event.<event>) PRE-scoring. Pure stdlib + core only — no DB, no I/O, no learning writes.

v1 semantics (ratified, deliberately simple): most-specific scope wins (k=1), REPLACEMENT — i.e. a
"learned override", NOT a blended calibrated prior. The references (Calibrate-Then-Act, MICE) treat a
calibrated value as a monotone remap of / blend with the model's own belief; logit-space
confidence-weighted blending is the v2 roadmap. v1 overrides outright and is honestly named so.

Safety rails:
- only ``risk_binary`` targets (p_success / p_event.*) are applied; benefit targets are skipped (v1).
- defense-in-depth: facts requiring human ratification, or with no calibration sample, are not applied
  (the read-port is the primary gate: status='active', ratified, min sample size).
- adjusted probabilities clamped to [0.01, 0.99] (logit safety; intentionally stricter than the
  capture clamp of [0,1] in prediction_capture).
- the original input is never mutated; adjusted event dicts are deep-copied.
- snapshot is None / no facts / no match -> the input is returned unchanged (byte-equivalent).
"""

from __future__ import annotations

import dataclasses
import fnmatch
import math
from dataclasses import dataclass, field
from typing import Any

from pebra.core.models import AssessmentInput
from pebra.core.prediction_capture import RISK_BINARY

_CLAMP_LO = 0.01
_CLAMP_HI = 0.99


@dataclass(frozen=True)
class SnapshotFact:
    """One learned fact decoded from learned_risk_facts by the (M5c) read-port. ``value`` is the
    learned override for ``target_name`` within ``scope``. ``fact_json`` carries composite scope
    predicate data (e.g. domain+change_kind). The read-port supplies ``fact_id`` as ``lrf_{id}`` and
    is the primary applicability gate; the fields here also enable a deterministic tiebreak."""

    fact_id: str
    target_type: str
    target_name: str
    scope_kind: str
    scope_value: str
    specificity_rank: int
    value: float
    sample_size: int = 0
    calibration_method: str = ""
    created_at: str = ""
    weight: float = 1.0
    requires_human_ratification: bool = False
    scope_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotBundle:
    snapshot_id: str
    facts: tuple[SnapshotFact, ...] = ()


def _clamp(value: float) -> float:
    return max(_CLAMP_LO, min(_CLAMP_HI, value))


def _applicable(fact: SnapshotFact) -> bool:
    """Defense-in-depth gate (read-port is primary): a fact may override a live risk probability only
    if it is risk-binary, ratified, carries a calibration sample AND method, and its value is a finite
    number. A non-finite or method-less value is treated as malformed and skipped — never clamped into
    a strong override."""
    return (
        fact.target_type == RISK_BINARY
        and not fact.requires_human_ratification
        and fact.sample_size > 0
        and bool(fact.calibration_method)
        and math.isfinite(fact.value)
    )


def _matches(fact: SnapshotFact, inp: AssessmentInput, features: dict[str, Any] | None) -> bool:
    sk = fact.scope_kind
    # input-derived scopes (no structural features required)
    if sk == "global":
        return True
    if sk == "action_type":
        return inp.action.action_type == fact.scope_value
    if sk == "path_glob":
        # match ALL files the action touches (not just the representative one) + the structural file
        paths = list(inp.action.expected_files)
        if features:
            file_path = (features.get("symbol") or {}).get("file_path")
            if file_path:
                paths.append(file_path)
        return any(fnmatch.fnmatch(p, fact.scope_value) for p in paths)
    # the remaining scopes need the structural feature payload
    if features is None:
        return False
    sym = features.get("symbol", {})
    domains = (features.get("domain", {}) or {}).get("matched_domains") or []
    if sk == "symbol":
        return sym.get("symbol_id") == fact.scope_value
    if sk == "public_api":
        return bool(sym.get("is_public_api"))
    if sk == "public_api_domain":
        return bool(sym.get("is_public_api")) and fact.scope_json.get("domain") in domains
    if sk == "domain":
        return fact.scope_value in domains
    if sk == "domain_change_kind":
        return (
            fact.scope_json.get("domain") in domains
            and fact.scope_json.get("change_kind") == sym.get("change_kind")
        )
    return False


def _winner(
    facts: tuple[SnapshotFact, ...], target_name: str, inp: AssessmentInput,
    features: dict[str, Any] | None,
) -> SnapshotFact | None:
    """Most-specific-wins (k=1) with a deterministic tiebreak on equal specificity:
    specificity_rank -> sample_size -> created_at (newest) -> fact_id."""
    candidates = [
        f for f in facts
        if f.target_name == target_name and _applicable(f) and _matches(f, inp, features)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda f: (f.specificity_rank, f.sample_size, f.created_at, f.fact_id),
    )


def _provenance(target: str, prior: float, new: float, fact: SnapshotFact) -> dict[str, Any]:
    return {
        "target": target,
        "winning_fact_id": fact.fact_id,
        "scope_kind": fact.scope_kind,
        "scope_rank": fact.specificity_rank,
        "prior_predicted_p": prior,
        "new_value": new,
        "sample_size": fact.sample_size,
        "calibration_method": fact.calibration_method,
    }


def apply_snapshot(
    inp: AssessmentInput, snapshot: SnapshotBundle | None = None
) -> AssessmentInput:
    """Return ``inp`` unchanged when no active snapshot/fact applies; otherwise an adjusted copy with
    overridden risk probabilities and ``applied_snapshot_provenance`` set."""
    if snapshot is None or not snapshot.facts:
        return inp

    features = inp.structural_features
    applied: list[dict[str, Any]] = []

    new_p_success = inp.p_success
    ps_winner = _winner(snapshot.facts, "p_success", inp, features)
    if ps_winner is not None:
        new_p_success = _clamp(ps_winner.value)
        applied.append(_provenance("p_success", inp.p_success, new_p_success, ps_winner))

    new_events: list[dict[str, Any]] | None = None
    for idx, event in enumerate(inp.events):
        winner = _winner(snapshot.facts, f"p_event.{event['event']}", inp, features)
        if winner is None:
            continue
        if new_events is None:
            # shallow copy is sufficient: v1 event dicts hold only scalars (event/p_event/
            # elicited_disutility). Revisit if the event schema ever gains nested mutables.
            new_events = [dict(e) for e in inp.events]  # never mutate the original
        prior = new_events[idx]["p_event"]
        new_value = _clamp(winner.value)
        new_events[idx]["p_event"] = new_value
        applied.append(_provenance(f"p_event.{event['event']}", prior, new_value, winner))

    if not applied:
        return inp  # no matching facts -> byte-equivalent

    return dataclasses.replace(
        inp,
        p_success=new_p_success,
        events=new_events if new_events is not None else inp.events,
        applied_snapshot_provenance={"snapshot_id": snapshot.snapshot_id, "applied_facts": applied},
    )
