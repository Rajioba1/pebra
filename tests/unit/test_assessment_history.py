"""Pure tests for trustworthy assessment-history identity projection."""

from __future__ import annotations

from pebra.core.assessment_history import project_assessment_identity
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM


_A = "a" * 64
_B = "b" * 64


def _content(**request_overrides):
    request = {"task": "Fix login", "action_id": "edit-auth"}
    request.update(request_overrides)
    return {"request": request}


def _with_binding(content, files, *, algorithm=CANDIDATE_BINDING_ALGORITHM, **extra):
    content["model_guidance_packet"] = {
        "binding": {"candidate": {"algorithm": algorithm, "files": files, **extra}}
    }
    return content


def test_exact_candidate_binding_wins_for_display_target() -> None:
    content = _with_binding(
        _content(revision_envelope={"expected_files": ["src/declared.py"]}),
        {"src/bound.py": _A},
    )

    identity = project_assessment_identity(content)

    assert identity.task == "Fix login"
    assert identity.action_id == "edit-auth"
    assert identity.declared_files == ("src/declared.py",)
    assert identity.bound_files == ("src/bound.py",)
    assert identity.target_files == ("src/bound.py",)
    assert identity.target_provenance == "candidate_bound"
    assert identity.candidate_fingerprint is not None


def test_revision_envelope_is_authoritative_declared_scope() -> None:
    content = _content(revision_envelope={"expected_files": ["src/declared.py"]})
    content["model_guidance_packet"] = {
        "binding": {"safe_scope": {"files": ["src/legacy.py"]}}
    }

    identity = project_assessment_identity(content)

    assert identity.declared_files == ("src/declared.py",)
    assert identity.target_files == ("src/declared.py",)
    assert identity.target_provenance == "declared"


def test_legacy_guidance_scope_is_labelled_inferred() -> None:
    content = _content()
    content["model_guidance_packet"] = {
        "binding": {"safe_scope": {"files": ["src/bad\ud800.py", "src/legacy.py"]}}
    }

    identity = project_assessment_identity(content)

    assert identity.declared_files == ()
    assert identity.bound_files == ()
    assert identity.target_files == ("src/legacy.py",)
    assert identity.target_provenance == "legacy_guidance"


def test_graph_resolved_paths_are_last_legacy_fallback() -> None:
    content = _content()
    content["scores"] = {
        "symbol_scope_evidence": {
            "symbol_fanin": {
                "resolved_file_paths": ["src/bad\ud800.py", "src/graph.py"]
            }
        }
    }

    identity = project_assessment_identity(content)

    assert identity.target_files == ("src/graph.py",)
    assert identity.target_provenance == "legacy_graph"


def test_symbol_ids_are_not_misrepresented_as_file_paths() -> None:
    content = _content()
    content["model_guidance_packet"] = {
        "binding": {
            "safe_scope": {
                "files": ["src/auth.py::validate_login"],
                "symbols": [
                    "src/other.py::symbol",
                    {"symbol_id": "src/third.py::symbol", "file_path": "src/real.py"},
                ],
            }
        }
    }

    identity = project_assessment_identity(content)

    assert identity.target_files == ("src/real.py",)
    assert identity.target_provenance == "legacy_guidance"


def test_invalid_binding_algorithm_has_no_fingerprint() -> None:
    content = _with_binding(_content(), {"src/a.py": _A}, algorithm="unknown")

    identity = project_assessment_identity(content)

    assert identity.bound_files == ()
    assert identity.candidate_fingerprint is None


def test_invalid_file_digest_has_no_fingerprint() -> None:
    content = _with_binding(_content(), {"src/a.py": "A" * 64})

    identity = project_assessment_identity(content)

    assert identity.bound_files == ()
    assert identity.candidate_fingerprint is None


def test_invalid_utf8_labels_and_declared_paths_degrade_safely() -> None:
    content = {
        "request": {
            "task": "bad\ud800task",
            "action_id": "bad\udfffaction",
            "revision_envelope": {
                "expected_files": ["src/bad\ud800.py", "src/good.py"]
            },
        }
    }

    identity = project_assessment_identity(content)

    assert identity.task is None
    assert identity.action_id is None
    assert identity.declared_files == ("src/good.py",)
    assert identity.target_files == ("src/good.py",)
    assert identity.target_provenance == "declared"


def test_candidate_binding_with_invalid_utf8_path_is_rejected_entirely() -> None:
    content = _with_binding(
        _content(revision_envelope={"expected_files": ["src/declared.py"]}),
        {"src/good.py": _A, "src/bad\ud800.py": _B},
    )

    identity = project_assessment_identity(content)

    assert identity.bound_files == ()
    assert identity.candidate_fingerprint is None
    assert identity.target_files == ("src/declared.py",)


def test_candidate_binding_with_invalid_utf8_metadata_is_rejected_entirely() -> None:
    content = _with_binding(
        _content(revision_envelope={"expected_files": ["src/declared.py"]}),
        {"src/good.py": _A},
        metadata={"note": "bad\ud800metadata"},
    )

    identity = project_assessment_identity(content)

    assert identity.bound_files == ()
    assert identity.candidate_fingerprint is None
    assert identity.target_files == ("src/declared.py",)


def test_symbol_only_candidate_binding_has_no_fingerprint() -> None:
    content = _with_binding(_content(), {"src/auth.py::validate_login": _A})

    identity = project_assessment_identity(content)

    assert identity.bound_files == ()
    assert identity.candidate_fingerprint is None


def test_mixed_file_and_symbol_candidate_binding_is_rejected_entirely() -> None:
    content = _with_binding(
        _content(revision_envelope={"expected_files": ["src/declared.py"]}),
        {"src/auth.py": _A, "src/auth.py::validate_login": _B},
    )

    identity = project_assessment_identity(content)

    assert identity.bound_files == ()
    assert identity.candidate_fingerprint is None
    assert identity.target_files == ("src/declared.py",)
    assert identity.target_provenance == "declared"


def test_fingerprint_is_stable_across_dictionary_order() -> None:
    first = _with_binding(
        _content(), {"src/a.py": _A, "src/b.py": _B}, metadata={"z": 2, "a": 1}
    )
    second = _with_binding(
        _content(), {"src/b.py": _B, "src/a.py": _A}, metadata={"a": 1, "z": 2}
    )

    assert (
        project_assessment_identity(first).candidate_fingerprint
        == project_assessment_identity(second).candidate_fingerprint
    )


def test_fingerprint_changes_when_file_content_digest_changes() -> None:
    first = _with_binding(_content(), {"src/a.py": _A})
    second = _with_binding(_content(), {"src/a.py": _B})
    first_fingerprint = project_assessment_identity(first).candidate_fingerprint

    assert first_fingerprint == "7777bb937abfaea98a378435a40153678a147dc553b25a846411d1e49ca733f4"
    assert first_fingerprint != project_assessment_identity(second).candidate_fingerprint


def test_paths_are_forward_slash_normalized_and_deduplicated() -> None:
    content = _content(
        revision_envelope={
            "expected_files": ["src\\auth.py", "src/auth.py", "src\\session.py"]
        }
    )

    identity = project_assessment_identity(content)

    assert identity.declared_files == ("src/auth.py", "src/session.py")
    assert identity.target_files == identity.declared_files


def test_missing_scope_is_unavailable_not_empty_declared_scope() -> None:
    identity = project_assessment_identity(_content())

    assert identity.declared_files == ()
    assert identity.bound_files == ()
    assert identity.target_files == ()
    assert identity.target_provenance == "unavailable"
    assert identity.candidate_fingerprint is None


def test_present_but_empty_declared_scope_does_not_fall_back_to_legacy() -> None:
    content = _content(revision_envelope={"expected_files": []})
    content["model_guidance_packet"] = {
        "binding": {"safe_scope": {"files": ["src/legacy.py"]}}
    }

    identity = project_assessment_identity(content)

    assert identity.target_files == ()
    assert identity.target_provenance == "unavailable"


def test_assessed_at_accepts_only_utc_iso_8601() -> None:
    identity = project_assessment_identity(
        {"request": {}, "assessed_at": "2026-07-20T12:34:56.123456+00:00"}
    )

    assert identity.assessed_at == "2026-07-20T12:34:56.123456+00:00"


def test_assessed_at_malformed_legacy_values_degrade_to_unavailable() -> None:
    invalid_values = (
        None,
        "",
        "not-a-timestamp",
        "2026-07-20T12:34:56",
        "2026-07-20T13:34:56+01:00",
        123,
        "bad\ud800timestamp",
    )

    for value in invalid_values:
        identity = project_assessment_identity({"request": {}, "assessed_at": value})
        assert identity.assessed_at is None
