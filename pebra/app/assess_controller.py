"""assess_controller (Architecture §11, plan §5) — the live assess use case.

The only orchestrator (plan §8): it validates the request, gathers evidence via ports, builds the
AssessmentInput IR, runs the pure engine (builder -> decision -> explanation -> guidance), and
persists through the store port. It imports only ``core/`` + ``ports/`` — never adapters.

Active learned snapshots may be loaded read-only and applied before scoring. Learning measurement and
promotion are not on this path; everything the engine needs arrives inside AssessmentInput.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from pebra.core import (
    assessment_builder,
    change_classifier,
    decision_engine,
    destructive_op_model,
    explanation_generator,
    exposure_model,
    modify_risk_model,
    model_guidance,
    prediction_capture,
    request_validator,
)
from pebra.core.apply_snapshot import apply_snapshot
from pebra.core.explanation_generator import Explanation
from pebra.core.graph_trust import is_trusted_fanin
from pebra.core.language_capability import LanguageCapability, classify_tier
from pebra.core.models import (
    AssessmentInput,
    AssessmentRequest,
    AssessmentResult,
    CandidateAction,
    CandidateVerificationEvidence,
    FileFanInRollup,
)
from pebra.ports.blast_radius_port import BlastRadiusProvider
from pebra.ports.fanin_port import FanInProvider
from pebra.ports.file_fanin_port import FileFanInProvider
from pebra.ports.evidence_port import EvidenceProvider
from pebra.ports.language_capability_port import LanguageCapabilityProvider
from pebra.ports.materialized_diff_port import MaterializedGraphDiffProvider
from pebra.ports.repository_registry_port import RepositoryRegistryPort
from pebra.ports.sanction_port import SanctionPort
from pebra.ports.snapshot_read_port import SnapshotReadPort
from pebra.ports.store_port import StorePort
from pebra.ports.structural_feature_port import StructuralFeatureProvider
from pebra.ports.symbol_diff_port import SymbolDiffProvider


@dataclass
class ScoredAction:
    action: CandidateAction
    result: AssessmentResult
    explanation: Explanation
    # Milestone 4a: the prediction manifest captured at scoring time (WHAT PEBRA predicted), persisted
    # atomically with the assessment. Shadow-only measurement — it never changes this decision.
    predictions: list[dict[str, Any]] = field(default_factory=list)
    thresholds: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssessmentOutcome:
    recommended_result: AssessmentResult
    recommended_explanation: Explanation
    assessment_id: str
    repo_id: str
    repo_root: str
    scored_actions: list[ScoredAction] = field(default_factory=list)


def _capability_for_fanin(
    fanin_ev: Any,
    provider: LanguageCapabilityProvider | None,
    repo_root: str,
) -> LanguageCapability:
    if provider is None or fanin_ev is None:
        return LanguageCapability()
    languages = tuple(getattr(fanin_ev, "resolved_languages", ()) or ())
    if not languages and getattr(fanin_ev, "resolved_language", None):
        languages = (fanin_ev.resolved_language,)
    if len(languages) != 1:
        return LanguageCapability(
            language="mixed" if languages else "unknown",
            probe_status="unmeasured",
            fallback_reason=(
                "multiple resolved languages" if languages else "no resolved language"
            ),
        )
    return provider.capability_for(languages[0], repo_root)


def _codegraph_structural_tier_allowed(
    fanin_ev: Any,
    provider: LanguageCapabilityProvider | None,
    cap: LanguageCapability,
) -> bool:
    if provider is None:
        # Tests and legacy embeddings that do not wire capability probing keep the old behavior.
        return True
    languages = tuple(getattr(fanin_ev, "resolved_languages", ()) or ())
    if len(languages) > 1:
        return False
    return classify_tier(cap) in {"full", "partial"}


def _merge_event_max(existing: dict[str, Any], injected: dict[str, Any]) -> dict[str, Any]:
    """Merge same-name event evidence conservatively: stronger probability/disutility wins."""
    merged = dict(existing)
    merged["p_event"] = max(existing.get("p_event", 0.0), injected.get("p_event", 0.0))
    merged["elicited_disutility"] = max(
        existing.get("elicited_disutility", 0.0), injected.get("elicited_disutility", 0.0)
    )
    for key, value in injected.items():
        if key not in merged:
            merged[key] = value
    return merged


def _build_input(
    request: AssessmentRequest,
    action: CandidateAction,
    repo_id: str,
    repo_root: str,
    thresholds: dict[str, float],
    *,
    evidence_provider: EvidenceProvider,
    symbol_diff_provider: SymbolDiffProvider,
    blast_provider: BlastRadiusProvider,
    sanction_port: SanctionPort,
    fanin_provider: FanInProvider | None = None,
    file_fanin_provider: FileFanInProvider | None = None,
    language_capability_provider: LanguageCapabilityProvider | None = None,
    materialized_diff_provider: MaterializedGraphDiffProvider | None = None,
    semantic_diff_enabled: bool = False,
    trusted_candidate_verification: CandidateVerificationEvidence | None = None,
) -> AssessmentInput:
    evidence = evidence_provider.gather_evidence(request, action, repo_root)
    symbol_diff = symbol_diff_provider.symbol_diff(action, repo_root)
    blast = blast_provider.blast(action, repo_root)
    sanction = sanction_port.active_sanction(repo_id, action)
    effective_thresholds = {**evidence.thresholds, **thresholds}

    # 3c — graph incompleteness caps evidence_quality: a blast estimate built over unresolved/dynamic/
    # wildcard imports (or missing expected files) is less trustworthy. The bounded penalty lowers
    # edit_confidence through the existing geometric mean (and can trip gate 8); a fully resolved
    # graph (score 0.0) leaves evidence_quality untouched, preserving the worked example.
    edit_confidence_factors = dict(evidence.edit_confidence_factors)
    if blast.graph_uncertainty_score > 0.0:
        supplied_eq = edit_confidence_factors.get("evidence_quality", 1.0)
        edit_confidence_factors["evidence_quality"] = max(
            0.0, supplied_eq - blast.graph_uncertainty_score
        )

    # M5c.5 — language-agnostic per-symbol fan-in. A TRUSTED result (location/name_fallback over a
    # FRESH graph) patches the real fan-in into the symbol evidence and OR-ins the fan-in-based
    # consequential flag (so Gate 2 escalates a high-fan-in consequential change). An UNTRUSTED result
    # (unresolved/stale/mismatch/ambiguous) is the ABSENCE of fan-in evidence — it is NOT nudged into
    # evidence_quality here. Codegraph validity is an INFRASTRUCTURE precondition, not a property of the
    # edit, so the decision layer handles it via Gate 13 (decision_engine._fanin_validity): when the
    # graph engine is required, an untrusted result downgrades a would-be proceed to inspect_first with
    # an actionable reason; when optional (default), it is identity. The raw evidence is attached either
    # way for Gate 13 + provenance.
    fanin_ev = None
    language_capability = LanguageCapability()
    if fanin_provider is not None:
        fanin_ev = fanin_provider.fanin(action, repo_root)
        language_capability = _capability_for_fanin(
            fanin_ev, language_capability_provider, repo_root
        )
        trusted = is_trusted_fanin(fanin_ev)
        if trusted:
            fan_in_threshold = effective_thresholds.get(
                "consequential_symbol_fan_in_percentile", 0.90
            )
            # Multi-language coarse diff tier: when there is NO AST-level symbol diff (non-Python, or
            # any language whose diff wasn't parsed) but the graph resolved the changed owner(s),
            # classify from graph structure instead of leaving max_change_kind at UNKNOWN. Gated on
            # `not parsed_patch_available`, so a real AST diff (Python) is untouched — byte-identical.
            if (
                action.proposed_patch
                and not symbol_diff.parsed_patch_available
                and fanin_ev.resolution_method == "location"
                and fanin_ev.node_ids_resolved
                and _codegraph_structural_tier_allowed(
                    fanin_ev, language_capability_provider, language_capability
                )
            ):
                # Semantic tier (dark, opt-in): for a measured-`full` language, materialize the
                # candidate and produce a real before/after signature/return/visibility diff that
                # ENRICHES the coarse floor. Falls back to the coarse tier when disabled / not `full` /
                # unavailable. Default-off threshold -> Python and every existing caller are untouched.
                materialized = None
                if (
                    materialized_diff_provider is not None
                    and semantic_diff_enabled
                    and effective_thresholds.get("codegraph_semantic_diff_enabled")
                    and classify_tier(language_capability) == "full"
                ):
                    materialized = materialized_diff_provider.diff_for_patch(
                        repo_root=repo_root, patch=action.proposed_patch
                    )
                # Honest tier label: `rows_from_materialized_graph_diff` DEGRADES to the pure coarse
                # floor when the diff is unavailable or the join is ambiguous (multi-owner). Only label
                # "codegraph_semantic" when it actually ENRICHED (rows differ from the coarse floor) —
                # otherwise no signature-level check happened and the honest tier is coarse-structural.
                coarse = change_classifier.rows_from_fanin(fanin_ev)
                enriched = (
                    change_classifier.rows_from_materialized_graph_diff(materialized, fanin_ev)
                    if materialized is not None and materialized.available
                    else coarse
                )
                if enriched != coarse:
                    rows, tier, tier_reason = enriched, "codegraph_semantic", (
                        "graph-semantic before/after classification "
                        "(materialized signature/return/visibility diff)"
                    )
                else:
                    rows, tier, tier_reason = coarse, "codegraph_structural", (
                        "graph-structural coarse classification "
                        "(no AST-level symbol diff for this language)"
                    )
                if rows:
                    summary = change_classifier.classify_diff(rows, effective_thresholds)
                    symbol_diff = replace(
                        symbol_diff,
                        changed_symbols=summary.changed_symbols or symbol_diff.changed_symbols,
                        max_change_kind=summary.max_change_kind,
                        visibility=summary.visibility,
                        consequential_symbol_changed=(
                            symbol_diff.consequential_symbol_changed
                            or summary.consequential_symbol_changed
                        ),
                        consequence_reason=list(dict.fromkeys(
                            symbol_diff.consequence_reason + summary.consequence_reason
                        )),
                        fallback_reason=tier_reason,
                        structure_tier=tier,
                    )
            patched = replace(
                symbol_diff,
                symbol_fan_in_percentile=fanin_ev.symbol_fan_in_percentile,
            )
            symbol_diff = replace(
                patched,
                consequential_symbol_changed=(
                    symbol_diff.consequential_symbol_changed
                    or change_classifier.is_high_fanin_consequential(patched, fan_in_threshold)
                ),
            )

    # Tier-3 benefit-exposure derivation: when RCA measured a real maintainability delta but nobody set
    # future_change_exposure (its unset default 0.0), derive the weight from the trusted graph fan-in so
    # the benefit is credited by DEFAULT — proportional to the code's future-change reach. An EXPLICIT
    # caller exposure (incl. an explicit 0.0) always wins. BENEFIT-only: only benefit_delta_evidence is
    # replaced; risk/events/gates are untouched. Absent/untrusted graph -> derive_exposure returns 0.0.
    bd = evidence.benefit_delta_evidence
    if (
        bd.auto_exposure_allowed
        and not bd.future_change_exposure_explicit
        and bd.future_change_exposure == 0.0
        and bd.source_type != "projected"
        and bd.deltas
    ):
        derived = exposure_model.derive_exposure(
            fanin_ev, cap=effective_thresholds.get("future_change_exposure_cap", 1.0)
        )
        if derived > 0.0:
            evidence = replace(
                evidence, benefit_delta_evidence=replace(bd, future_change_exposure=derived)
            )

    # Multi-language: attach the MEASURED capability for the resolved edit's language. Only probed
    # when fan-in resolved a language (avoids a needless DB open for unresolved edits). Advisory only
    # in this phase — nothing in the engine scores off it; it rides the input for honest surfacing.
    # Destructive-op event injection (assess-path risk model). Only DELETE injects (symbol loss →
    # call-graph roll-up + no-graph baseline floor). RENAME/MOVE are recorded on the symbol_diff axis
    # but NOT scored here (path migration is an import-graph question, modeled in a later slice); CREATE
    # is inert. events stays the SAME object for non-DELETE so ordinary patches are byte-identical.
    events_list = evidence.events
    file_fanin_rollup: FileFanInRollup | None = None
    if symbol_diff.file_operation_kind == "DELETE":
        rollups = (
            [file_fanin_provider.file_fanin_rollup(fp, repo_root)
             for fp in symbol_diff.file_operation_paths]
            if file_fanin_provider is not None else []
        )
        file_fanin_rollup = (
            max(rollups, key=lambda r: (r.file_symbol_fanin_rollup_percentile, r.distinct_caller_count))
            if rollups else FileFanInRollup()
        )
        # public_api_break only when the symbol is actually exported. NOT consequential_symbol_changed —
        # that flag means HIGH INTERNAL fan-in (many internal callers), not public API surface; using it
        # would inject a spurious public_api_break (and inflate expected_loss) for internal deletions.
        is_pub = symbol_diff.visibility in {"public_api", "exported"}
        injected = destructive_op_model.events_for_destructive_op(
            op_kind="DELETE", rollup=file_fanin_rollup, arch=evidence.architecture_evidence,
            is_public_api=is_pub, is_migration=action.is_migration,
            is_schema_change=action.is_schema_change,
        )
        if injected:
            events_list = list(evidence.events)
            for ev in injected:
                existing_idx = next(
                    (i for i, e in enumerate(events_list) if e.get("event") == ev["event"]), None
                )
                if existing_idx is None:
                    events_list.append(ev)
                else:
                    events_list[existing_idx] = _merge_event_max(events_list[existing_idx], ev)

    injected = modify_risk_model.events_for_modify_risk(
        symbol_diff=symbol_diff,
        fanin=fanin_ev,
        arch=evidence.architecture_evidence,
        criticality_stage=evidence.criticality_stage,
        is_migration=action.is_migration,
        is_schema_change=action.is_schema_change,
    )
    if injected:
        events_list = list(events_list)
        for ev in injected:
            existing_idx = next(
                (i for i, e in enumerate(events_list) if e.get("event") == ev["event"]), None
            )
            if existing_idx is None:
                events_list.append(ev)
            else:
                events_list[existing_idx] = _merge_event_max(events_list[existing_idx], ev)

    return AssessmentInput(
        request=request,
        action=action,
        events=events_list,
        p_success=evidence.p_success,
        immediate_benefit=evidence.immediate_benefit,
        review_cost=evidence.review_cost,
        criticality_stage=evidence.criticality_stage,
        criticality_value=evidence.criticality_value,
        edit_confidence_factors=edit_confidence_factors,
        thresholds=effective_thresholds,
        policy_violations=list(evidence.policy_violations),
        repo_id=repo_id,
        repo_root=repo_root,
        p_success_variance=evidence.p_success_variance,
        review_cost_variance=evidence.review_cost_variance,
        variance_breakdown=evidence.variance_breakdown,
        benefit_delta_evidence=evidence.benefit_delta_evidence,
        candidate_verification=trusted_candidate_verification or evidence.candidate_verification,
        symbol_diff_evidence=symbol_diff,
        fanin_evidence=fanin_ev,
        language_capability=language_capability,
        file_fanin_rollup=file_fanin_rollup,
        blast_evidence=blast,
        architecture_evidence=evidence.architecture_evidence,
        # active_snapshot left at its default None here; M5c assigns it after apply_snapshot.
        sanction=sanction,
    )


def _score_action(
    request: AssessmentRequest,
    action: CandidateAction,
    repo_id: str,
    repo_root: str,
    thresholds: dict[str, float],
    **ports: Any,
) -> ScoredAction:
    inp = _build_input(
        request, action, repo_id, repo_root, thresholds,
        evidence_provider=ports["evidence_provider"],
        symbol_diff_provider=ports["symbol_diff_provider"],
        blast_provider=ports["blast_provider"],
        sanction_port=ports["sanction_port"],
        fanin_provider=ports.get("fanin_provider"),
        file_fanin_provider=ports.get("file_fanin_provider"),
        language_capability_provider=ports.get("language_capability_provider"),
        materialized_diff_provider=ports.get("materialized_diff_provider"),
        semantic_diff_enabled=bool(ports.get("semantic_diff_enabled", False)),
        trusted_candidate_verification=ports.get("trusted_candidate_verification"),
    )
    # Phase-4 reframe: capture structural features pre-scoring and attach to the IR for CAPTURE only.
    # assessment_builder/decision_engine ignore inp.structural_features (no score/gate change — Hard
    # Rule). M5 apply_snapshot will later consume it pre-scoring. None provider -> empty features.
    sfp = ports.get("structural_feature_provider")
    if sfp is not None:
        inp.structural_features = sfp.build_features(inp)
    # M5c: apply the active learned snapshot PRE-scoring. The bundle is loaded once per assess()
    # (not once per action) and passed in here. apply_snapshot is pure; the assess path performs NO
    # learning write. No active facts -> identity (golden unchanged).
    # The prediction manifest below records the USED (possibly overridden) values; the raw priors are
    # preserved in inp.applied_snapshot_provenance.
    bundle = ports.get("active_snapshot_bundle")
    inp = apply_snapshot(inp, bundle)
    inp.active_snapshot = bundle
    assessment = assessment_builder.build_assessment(inp)
    policy_violations = inp.policy_violations
    result = decision_engine.decide(assessment, policy_violations=policy_violations)
    if inp.applied_snapshot_provenance is not None:
        result.provenance["applied_snapshot_provenance"] = inp.applied_snapshot_provenance
    result.assessed_commit = ports.get("assessed_commit")
    result.provenance["repo_state"] = {
        "repo_head_sha": result.assessed_commit,
        "worktree_dirty": ports.get("worktree_dirty"),
        "assessed_repo_root": repo_root,
    }
    graph_provenance = _graph_provenance(inp)
    if graph_provenance:
        result.provenance["graph_provenance"] = graph_provenance
    explanation = explanation_generator.render(result, inp.thresholds)
    packet = model_guidance.render(result, action, explanation)
    result.model_guidance_packet = packet
    # Milestone 4a: capture the prediction manifest from the in-flight evidence (p_success and the
    # projected deltas are dropped from result.scores, so this is the only faithful record). Pure;
    # read-only; does not feed back into the decision (Hard Rule).
    manifest = prediction_capture.build_prediction_manifest(
        p_success=inp.p_success,
        events=inp.events,
        immediate_benefit=inp.immediate_benefit,
        projected_deltas=inp.benefit_delta_evidence.deltas,
        projected_benefit=result.scores["benefit"],
        action_id=action.id,
        features=inp.structural_features,
        applied_snapshot_provenance=inp.applied_snapshot_provenance,
    )
    return ScoredAction(
        action=action,
        result=result,
        explanation=explanation,
        predictions=[asdict(t) for t in manifest],
        thresholds=dict(inp.thresholds),
    )


def _graph_provenance(inp: AssessmentInput) -> dict[str, Any]:
    fanin = inp.fanin_evidence
    if fanin is None:
        return {}
    prov: dict[str, Any] = {
        "engine": "CodeGraph",
        "provider_version": fanin.provider_version,
        "index_version": fanin.index_version,
    }
    # Honest per-language reach: measured tier + coverage for the resolved edit's language, and the
    # tier that produced THIS diff. Lets a reader see WHY a non-Python diff is coarse — so
    # "unavailable"/"partial" can't be mistaken for "verified full support".
    cap = inp.language_capability
    if cap.probe_status != "unmeasured":
        prov["language_capability"] = {
            "language": cap.language,
            "probe_status": cap.probe_status,
            "tier": classify_tier(cap),
            "node_count": cap.node_count,
            "signature_coverage_ratio": round(cap.signature_coverage_ratio, 3),
            "visibility_coverage_ratio": round(cap.visibility_coverage_ratio, 3),
            "edge_kinds": sorted(cap.edge_kinds),
        }
    prov["structure_tier"] = inp.symbol_diff_evidence.structure_tier
    return prov


def _thresholds_with_revise_attempt(
    thresholds: dict[str, Any],
    *,
    store: StorePort,
    repo_id: str,
    assessed_commit: str | None,
    action: CandidateAction,
) -> dict[str, Any]:
    caller_attempt = 0
    caller_supplied = "revise_safer_attempt" in thresholds
    if caller_supplied:
        try:
            caller_attempt = int(thresholds["revise_safer_attempt"])
        except (TypeError, ValueError):
            caller_attempt = 0
    counter = getattr(store, "revise_safer_attempt_count", None)
    if counter is None and caller_attempt <= 0:
        return thresholds
    store_attempt = 0
    try:
        if counter is not None:
            store_attempt = counter(repo_id, assessed_commit, list(action.expected_files or []))
    except Exception:  # noqa: BLE001 - attempt tracking must not make assess unavailable
        store_attempt = 0
    attempt = max(caller_attempt, int(store_attempt or 0))
    if caller_supplied and attempt == caller_attempt:
        return thresholds
    if attempt <= 0:
        return thresholds
    return {**thresholds, "revise_safer_attempt": attempt}


def _recommended(scored: list[ScoredAction]) -> ScoredAction:
    """Pick the recommended action: best RAU among non-rejected, else the first."""
    from pebra.core.constants import Decision

    proceedable = [s for s in scored if s.result.recommended_decision is not Decision.REJECT]
    pool = proceedable or scored
    return max(pool, key=lambda s: s.result.scores["rau"])


def _candidate_verification_from_raw(raw: dict[str, Any]) -> CandidateVerificationEvidence:
    return CandidateVerificationEvidence(
        status=str(raw.get("status", "not_applicable")),
        checks=dict(raw.get("checks", {})),
        required_checks=[str(check) for check in raw.get("required_checks", []) if isinstance(check, str)],
        domain=raw.get("domain"),
        reason=raw.get("reason"),
        verified_patch_hash=(
            str(raw["verified_patch_hash"])
            if isinstance(raw.get("verified_patch_hash"), str)
            else None
        ),
    )


def _trusted_verification_for_action(
    raw: dict[str, Any] | None, action: CandidateAction
) -> CandidateVerificationEvidence | None:
    """Host-only candidate verification injected outside request.evidence.

    Accepted shapes:
    - a single verification dict with a ``status`` field, for one-action surfaces;
    - ``{action_id: verification_dict}`` for multi-action surfaces.
    """
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("status"), str):
        return _candidate_verification_from_raw(raw)
    action_raw = raw.get(action.id)
    if isinstance(action_raw, dict):
        return _candidate_verification_from_raw(action_raw)
    return None


def assess(
    request: AssessmentRequest,
    *,
    thresholds: dict[str, float],
    start_path: str,
    evidence_provider: EvidenceProvider,
    symbol_diff_provider: SymbolDiffProvider,
    blast_provider: BlastRadiusProvider,
    sanction_port: SanctionPort,
    repository_registry: RepositoryRegistryPort,
    store: StorePort,
    assessed_commit: str | None = None,
    worktree_dirty: bool | None = None,
    structural_feature_provider: StructuralFeatureProvider | None = None,
    snapshot_read_port: SnapshotReadPort | None = None,
    fanin_provider: FanInProvider | None = None,
    file_fanin_provider: FileFanInProvider | None = None,
    language_capability_provider: LanguageCapabilityProvider | None = None,
    materialized_diff_provider: MaterializedGraphDiffProvider | None = None,
    semantic_diff_enabled: bool = False,
    trusted_candidate_verification: dict[str, Any] | None = None,
) -> AssessmentOutcome:
    request_validator.validate(request)
    repo = repository_registry.resolve(start_path)
    active_snapshot_bundle = (
        snapshot_read_port.load_active_snapshot(repo.repo_id) if snapshot_read_port is not None else None
    )

    scored: list[ScoredAction] = []
    for action in request.candidate_actions:
        action_thresholds = _thresholds_with_revise_attempt(
            thresholds,
            store=store,
            repo_id=repo.repo_id,
            assessed_commit=assessed_commit,
            action=action,
        )
        scored.append(
            _score_action(
                request, action, repo.repo_id, repo.repo_root, action_thresholds,
                evidence_provider=evidence_provider,
                symbol_diff_provider=symbol_diff_provider,
                blast_provider=blast_provider,
                sanction_port=sanction_port,
                assessed_commit=assessed_commit,
                worktree_dirty=worktree_dirty,
                structural_feature_provider=structural_feature_provider,
                active_snapshot_bundle=active_snapshot_bundle,
                fanin_provider=fanin_provider,
                file_fanin_provider=file_fanin_provider,
                language_capability_provider=language_capability_provider,
                materialized_diff_provider=materialized_diff_provider,
                semantic_diff_enabled=semantic_diff_enabled,
                trusted_candidate_verification=_trusted_verification_for_action(
                    trusted_candidate_verification, action
                ),
            )
        )

    recommended = _recommended(scored)
    assessment_id = store.persist_assessment(
        recommended.result,
        # persist the thresholds used so the post-edit verify path can reproduce the SAME consequential
        # fan-in threshold (otherwise verify silently falls back to the 0.90 default — assess/verify drift).
        {"task": request.task, "action_id": recommended.action.id,
         "thresholds": dict(recommended.thresholds)},
        predictions=recommended.predictions,
    )
    return AssessmentOutcome(
        recommended_result=recommended.result,
        recommended_explanation=recommended.explanation,
        assessment_id=assessment_id,
        repo_id=repo.repo_id,
        repo_root=repo.repo_root,
        scored_actions=scored,
    )
