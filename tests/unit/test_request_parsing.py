"""Architecture §3.1, AD-8 — candidate_parser (raw dict -> AssessmentRequest) + request_validator."""

from __future__ import annotations

import pytest

from pebra.core import candidate_parser as cp
from pebra.core import request_validator as rv

_RAW = {
    "schema_version": "0.1",
    "task": "Fix failing login validation",
    "repo_id": "repo_local_example",
    "candidate_actions": [
        {
            "id": "a1",
            "label": "Patch validate_login only",
            "action_type": "edit",
            "affected_symbols": ["src/auth.py::validate_login"],
            "expected_files": ["src/auth.py"],
        }
    ],
    "evidence": {"p_success": 0.74},
    "thresholds": {"c3_max_expected_loss_without_human": 0.20},
}


def test_parse_builds_canonical_request() -> None:
    req = cp.parse(_RAW)
    assert req.task == "Fix failing login validation"
    assert req.schema_version == "0.1"
    assert len(req.candidate_actions) == 1
    a = req.candidate_actions[0]
    assert a.id == "a1"
    assert a.affected_symbols == ["src/auth.py::validate_login"]
    assert a.expected_files == ["src/auth.py"]
    assert req.evidence["p_success"] == 0.74
    assert req.thresholds["c3_max_expected_loss_without_human"] == 0.20


def test_validate_accepts_well_formed_request() -> None:
    req = cp.parse(_RAW)
    rv.validate(req)  # no raise


def test_validate_rejects_empty_task() -> None:
    bad = {**_RAW, "task": ""}
    with pytest.raises(rv.RequestValidationError):
        rv.validate(cp.parse(bad))


def test_validate_rejects_no_candidate_actions() -> None:
    bad = {**_RAW, "candidate_actions": []}
    with pytest.raises(rv.RequestValidationError):
        rv.validate(cp.parse(bad))


def test_validate_rejects_duplicate_action_ids() -> None:
    bad = {
        **_RAW,
        "candidate_actions": [
            {"id": "a1", "label": "x", "action_type": "edit"},
            {"id": "a1", "label": "y", "action_type": "edit"},
        ],
    }
    with pytest.raises(rv.RequestValidationError):
        rv.validate(cp.parse(bad))


def test_parse_missing_schema_version_defaults() -> None:
    raw = {k: v for k, v in _RAW.items() if k != "schema_version"}
    req = cp.parse(raw)
    assert req.schema_version  # defaulted, not blank


def test_validate_requires_expected_files_to_match_multifile_patch() -> None:
    patch = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/session.py b/src/session.py\n"
        "--- a/src/session.py\n+++ b/src/session.py\n@@ -1 +1 @@\n-old\n+new\n"
    )
    bad = {
        **_RAW,
        "candidate_actions": [{
            **_RAW["candidate_actions"][0],
            "expected_files": ["src/auth.py"],
            "proposed_patch": patch,
        }],
    }

    with pytest.raises(rv.RequestValidationError, match="exactly match"):
        rv.validate(cp.parse(bad))


def test_validate_accepts_exact_multifile_patch_envelope() -> None:
    patch = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/session.py b/src/session.py\n"
        "--- a/src/session.py\n+++ b/src/session.py\n@@ -1 +1 @@\n-old\n+new\n"
    )
    raw = {
        **_RAW,
        "candidate_actions": [{
            **_RAW["candidate_actions"][0],
            "expected_files": ["./src/session.py", "src/auth.py"],
            "proposed_patch": patch,
        }],
    }

    rv.validate(cp.parse(raw))


def test_validate_accepts_multifile_patch_with_unquoted_space_paths() -> None:
    patch = (
        "diff --git a/docs/readme.md b/docs/readme.md\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/docs/readme.md\n+++ b/docs/readme.md\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/docs/release notes.md b/docs/release notes.md\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/docs/release notes.md\n+++ b/docs/release notes.md\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    raw = {
        **_RAW,
        "candidate_actions": [{
            **_RAW["candidate_actions"][0],
            "expected_files": ["docs/readme.md", "docs/release notes.md"],
            "proposed_patch": patch,
        }],
    }

    rv.validate(cp.parse(raw))


def test_validate_rejects_nonempty_malformed_proposed_patch() -> None:
    raw = {
        **_RAW,
        "candidate_actions": [{
            **_RAW["candidate_actions"][0],
            "expected_files": ["src/auth.py"],
            "proposed_patch": "not a unified diff",
        }],
    }

    with pytest.raises(rv.RequestValidationError, match="well-formed unified diff"):
        rv.validate(cp.parse(raw))


def test_validate_rejects_mixed_unified_and_codex_patch_formats() -> None:
    patch = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1 @@\n-old\n+new\n"
        "*** Begin Patch\n*** Add File: src/extra.py\n+payload\n*** End Patch\n"
    )
    raw = {
        **_RAW,
        "candidate_actions": [{
            **_RAW["candidate_actions"][0],
            "expected_files": ["src/auth.py"],
            "proposed_patch": patch,
        }],
    }

    with pytest.raises(rv.RequestValidationError, match="well-formed unified diff"):
        rv.validate(cp.parse(raw))


def test_validate_rejects_patch_path_escape() -> None:
    patch = (
        "diff --git a/../outside.ts b/../outside.ts\n"
        "--- a/../outside.ts\n+++ b/../outside.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    raw = {
        **_RAW,
        "candidate_actions": [{
            **_RAW["candidate_actions"][0],
            "expected_files": ["../outside.ts"],
            "proposed_patch": patch,
        }],
    }

    with pytest.raises(rv.RequestValidationError, match="inside the repository"):
        rv.validate(cp.parse(raw))
