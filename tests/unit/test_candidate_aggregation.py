from pebra.core.candidate_aggregation import aggregate_candidate
from pebra.core.models import CandidateAction, FanInEvidence, OwnerRiskEvidence


def _action(*files: str) -> CandidateAction:
    return CandidateAction(id="a", label="edit", action_type="edit", expected_files=list(files))


def _owner(
    node_id: str,
    file_path: str,
    *,
    exposure: float,
    impacted: tuple[str, ...],
    language: str = "typescript",
    public: bool = False,
) -> OwnerRiskEvidence:
    return OwnerRiskEvidence(
        node_id=node_id,
        file_path=file_path,
        language=language,
        qualified_name=node_id,
        impact_percentile=exposure,
        transitive_impact_percentile=exposure,
        impacted_node_ids=impacted,
        is_public_contract=public,
    )


def test_single_owner_preserves_worst_owner_floor_without_breadth_bonus() -> None:
    owner = _owner("a", "src/a.ts", exposure=0.8, impacted=("x", "y"), public=True)
    result = aggregate_candidate(
        _action("src/a.ts"),
        FanInEvidence(
            owner_risk=(owner,),
            modify_transitive_impact_count=2,
            modify_transitive_impact_percentile=0.8,
        ),
    )

    assert result.max_owner_exposure == 0.8
    assert result.cumulative_exposure == 0.8
    assert result.breadth_bonus == 0.0
    assert result.public_contract_count == 1


def test_disjoint_owner_impact_accumulates_without_exceeding_one() -> None:
    owners = (
        _owner("a", "src/a.ts", exposure=0.7, impacted=("x", "y")),
        _owner("b", "src/b.ts", exposure=0.6, impacted=("z", "q")),
    )
    result = aggregate_candidate(
        _action("src/a.ts", "src/b.ts"),
        FanInEvidence(
            owner_risk=owners,
            modify_transitive_impact_count=4,
            modify_transitive_impact_percentile=0.9,
        ),
    )

    assert result.cumulative_exposure > result.max_owner_exposure
    assert result.cumulative_exposure <= 1.0
    assert result.impacted_node_count == 4
    assert result.breadth_bonus > 0.0


def test_shared_impact_is_deduplicated_and_does_not_inflate_exposure() -> None:
    owners = (
        _owner("a", "src/a.ts", exposure=0.7, impacted=("x", "y")),
        _owner("b", "src/b.ts", exposure=0.6, impacted=("x", "y")),
    )
    result = aggregate_candidate(
        _action("src/a.ts", "src/b.ts"),
        FanInEvidence(
            owner_risk=owners,
            modify_transitive_impact_count=2,
            modify_transitive_impact_percentile=0.7,
        ),
    )

    assert result.impacted_node_count == 2
    assert result.cumulative_exposure == result.max_owner_exposure


def test_mixed_languages_preserve_resolved_evidence_and_report_missing_files() -> None:
    owners = (
        _owner("a", "src/a.ts", exposure=0.6, impacted=("x",), language="typescript"),
        _owner("b", "src/b.py", exposure=0.5, impacted=("y",), language="python"),
    )
    result = aggregate_candidate(
        _action("src/a.ts", "src/b.py", "src/new.go"), FanInEvidence(owner_risk=owners)
    )

    assert result.languages == ("python", "typescript")
    assert result.resolved_file_count == 2
    assert result.unresolved_file_count == 1
    assert result.resolution_coverage == 2 / 3


def test_file_and_owner_order_do_not_change_aggregate() -> None:
    a = _owner("a", "src/a.ts", exposure=0.7, impacted=("x", "y"))
    b = _owner("b", "src/b.py", exposure=0.6, impacted=("z",), language="python")

    left = aggregate_candidate(
        _action("src/a.ts", "src/b.py"), FanInEvidence(owner_risk=(a, b))
    )
    right = aggregate_candidate(
        _action("src/b.py", "src/a.ts"), FanInEvidence(owner_risk=(b, a))
    )

    assert left == right


def test_resolved_owner_files_form_envelope_when_expected_files_are_omitted() -> None:
    owner = _owner("a", "src/a.ts", exposure=0.5, impacted=("x",))

    result = aggregate_candidate(_action(), FanInEvidence(owner_risk=(owner,)))

    assert result.file_count == 1
    assert result.resolved_file_count == 1
    assert result.resolution_coverage == 1.0


def test_adding_overlapping_higher_exposure_owner_cannot_lower_candidate_aggregate() -> None:
    first = _owner("a", "src/a.ts", exposure=0.6, impacted=("x",))
    second = _owner("b", "src/b.ts", exposure=0.6, impacted=("y",))
    overlapping = _owner("c", "src/c.ts", exposure=0.7, impacted=("x", "y"))
    baseline = aggregate_candidate(
        _action("src/a.ts", "src/b.ts"),
        FanInEvidence(
            owner_risk=(first, second),
            modify_transitive_impact_count=2,
            modify_transitive_impact_percentile=0.84,
        ),
    )
    extended = aggregate_candidate(
        _action("src/a.ts", "src/b.ts", "src/c.ts"),
        FanInEvidence(
            owner_risk=(first, second, overlapping),
            modify_transitive_impact_count=2,
            modify_transitive_impact_percentile=0.84,
        ),
    )

    assert extended.cumulative_exposure >= baseline.cumulative_exposure
    assert extended.breadth_bonus >= baseline.breadth_bonus


def test_patch_files_form_envelope_when_expected_files_are_omitted_and_one_is_unresolved() -> None:
    owner = _owner("a", "src/a.ts", exposure=0.5, impacted=("x",))
    action = _action()
    action.proposed_patch = (
        "diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n"
        "@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/src/b.py b/src/b.py\n--- a/src/b.py\n+++ b/src/b.py\n"
        "@@ -1 +1 @@\n-a\n+b\n"
    )

    result = aggregate_candidate(action, FanInEvidence(owner_risk=(owner,)))

    assert result.file_count == 2
    assert result.unresolved_file_count == 1
    assert result.resolution_coverage == 0.5


def test_plain_unified_patch_files_form_candidate_envelope() -> None:
    owner = _owner("a", "src/a.ts", exposure=0.5, impacted=("x",))
    action = _action()
    action.proposed_patch = (
        "--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1 +1 @@\n-a\n+b\n"
        "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-a\n+b\n"
    )

    result = aggregate_candidate(action, FanInEvidence(owner_risk=(owner,)))

    assert result.file_count == 2
    assert result.unresolved_file_count == 1
