from __future__ import annotations

import json
import subprocess
from pathlib import Path

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.runners import agent_loop, run_pair, tool_impl
from e2e.experiments.agent_ab.tools import advisory_check_real, repository_context_contract


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.ts").write_text(
        "export function helper() { return 1; }\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "PEBRA test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@users.noreply.github.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    return tmp_path


def test_repository_context_tool_contract_is_provider_neutral_and_fixed_shape() -> None:
    schemas = {
        item["name"]: item
        for item in agent_loop._build_tools_schema(("repository_context",))
    }

    assert repository_context_contract.TOOL_NAME == "repository_context"
    assert schemas["repository_context"]["input_schema"] == (
        repository_context_contract.INPUT_SCHEMA
    )
    blob = json.dumps(schemas["repository_context"]).lower()
    assert "pebra" not in blob
    assert "codegraph" not in blob
    assert "provider" not in blob


def test_repository_context_dispatch_normalizes_backend_output() -> None:
    seen: list[dict] = []
    output = tool_impl.repository_context(
        {"query": "find helper", "files": ["src/a.ts"]},
        lambda payload, **_kwargs: seen.append(payload)
        or {
            "status": "available",
            "context": "helper source",
            "related_files": ["src/a.ts"],
            "related_tests": ["src/a.test.ts"],
            "warnings": [],
            "truncated": False,
            "secret_backend": "must be dropped",
        },
    )

    assert seen == [{"query": "find helper", "files": ["src/a.ts"]}]
    assert tuple(output) == repository_context_contract.OUTPUT_KEYS
    assert "secret_backend" not in output


def test_ordinary_context_is_bounded_cached_and_receipted(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    telemetry = run_pair.ArmTelemetry()
    calls = 0
    original = run_pair._ordinary_repository_context

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(run_pair, "_ordinary_repository_context", counted)
    backend = run_pair._repository_context_backend(models.ARM_SHAM, repo, telemetry)

    first = backend({"query": "helper", "files": ["src/a.ts"]})
    second = backend({"query": " helper ", "files": ["src\\a.ts"]})

    assert calls == 1
    assert first == second
    assert tuple(first) == repository_context_contract.OUTPUT_KEYS
    assert "helper" in first["context"]
    assert [item["cache_hit"] for item in telemetry.repository_context_receipts] == [
        False,
        True,
    ]
    assert {item["source"] for item in telemetry.repository_context_receipts} == {
        "ordinary"
    }


def test_graph_context_uses_public_explore_and_keeps_receipt_host_only(
    tmp_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    calls: list[dict] = []

    def explore(query, *, files, repo_root, max_files, max_bytes, timeout):
        calls.append(
            {
                "query": query,
                "files": files,
                "repo_root": repo_root,
                "max_files": max_files,
                "max_bytes": max_bytes,
                "timeout": timeout,
            }
        )
        return {
            "status": "available",
            "snapshot": {
                "status": "available",
                "repo_head": head,
                "graph_scope_digest": "a" * 64,
            },
            "context": "CodeGraph found PEBRA helper source",
            "dependent_files": ["src/b.ts"],
            "affected_tests": ["src/a.test.ts"],
            "warnings": [],
            "fallback_reason": None,
            "truncated": False,
        }

    monkeypatch.setattr(run_pair.cli_harness, "explore", explore)
    telemetry = run_pair.ArmTelemetry()
    backend = run_pair._repository_context_backend(
        models.ARM_GRAPH_CONTEXT, repo, telemetry
    )

    output = backend(
        {"query": "find helper", "files": ["src/a.ts"]}, timeout_seconds=12.0
    )

    assert len(calls) == 1
    assert calls[0]["query"] == "find helper"
    assert tuple(output) == repository_context_contract.OUTPUT_KEYS
    serialized = json.dumps(output).lower()
    assert "codegraph" not in serialized
    assert "pebra" not in serialized
    assert "graph_scope" not in serialized
    receipt = telemetry.repository_context_receipts[-1]
    assert receipt["source"] == "graph"
    assert receipt["repo_head"] == head
    assert receipt["graph_scope_digest"] == "a" * 64


def test_context_cache_invalidates_when_repository_head_changes(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    telemetry = run_pair.ArmTelemetry()
    calls = 0
    original = run_pair._ordinary_repository_context

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(run_pair, "_ordinary_repository_context", counted)
    backend = run_pair._repository_context_backend(models.ARM_SHAM, repo, telemetry)
    backend({"query": "helper", "files": []})
    (repo / "src/b.ts").write_text("export const b = 2;\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "next"], cwd=repo, check=True)
    backend({"query": "helper", "files": []})

    assert calls == 2
    assert [item["cache_hit"] for item in telemetry.repository_context_receipts] == [
        False,
        False,
    ]


def test_context_cache_invalidates_when_uncommitted_worktree_changes(
    tmp_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    telemetry = run_pair.ArmTelemetry()
    calls = 0
    original = run_pair._ordinary_repository_context

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(run_pair, "_ordinary_repository_context", counted)
    backend = run_pair._repository_context_backend(models.ARM_SHAM, repo, telemetry)
    backend({"query": "helper", "files": ["src/a.ts"]})
    (repo / "src/a.ts").write_text(
        "export function helper() { return 2; }\n", encoding="utf-8"
    )
    backend({"query": "helper", "files": ["src/a.ts"]})

    assert calls == 2
    assert [item["cache_hit"] for item in telemetry.repository_context_receipts] == [
        False,
        False,
    ]


def _receipt(*, source: str, head: str, digest: str | None) -> dict:
    return {
        "source": source,
        "repo_head": head,
        "graph_scope_digest": digest,
        "query": "helper",
        "requested_files": ["src/a.ts"],
        "returned_files": ["src/a.ts"],
        "truncated": False,
        "duration_seconds": 0.1,
        "cache_hit": False,
        "status": "available",
    }


def test_advisory_is_not_run_before_required_understand_receipt(tmp_path, monkeypatch) -> None:
    telemetry = run_pair.ArmTelemetry()
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("assessment must wait for Understand")
        ),
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
        required_context_source="ordinary",
    )

    output = backend(
        {
            "target_file": "src/a.ts",
            "change_summary": "change helper",
            "proposed_patch": "diff --git a/src/a.ts b/src/a.ts",
        }
    )

    assert output["recommended_decision"] is None
    assert "repository context" in output["advisory"].lower()


def test_graph_understand_receipt_must_match_assessment_scope(tmp_path, monkeypatch) -> None:
    telemetry = run_pair.ArmTelemetry()
    telemetry.repository_context_receipts.append(
        _receipt(source="graph", head="b" * 40, digest="a" * 64)
    )
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *_args, **_kwargs: advisory_check_real.AdvisoryOutput(
            {
                "recommended_decision": "proceed",
                "risk_level": "low",
                "advisory": "No significant concerns were detected.",
                "detail": {},
            },
            assessment_id="asm_1",
            raw_payload={
                "graph_provenance": {
                    "repo_head": "b" * 40,
                    "graph_scope_digest": "c" * 64,
                }
            },
        ),
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_CONTEXT,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
        required_context_source="graph",
    )

    output = backend(
        {
            "target_file": "src/a.ts",
            "change_summary": "change helper",
            "proposed_patch": "diff --git a/src/a.ts b/src/a.ts",
        }
    )

    assert output["recommended_decision"] is None
    assert telemetry.last_assessment_id is None
    assert telemetry.real_advisory_failures[-1]["category"] == (
        "understand_assessment_scope_mismatch"
    )


def test_matching_graph_understand_receipt_allows_assessment(tmp_path, monkeypatch) -> None:
    telemetry = run_pair.ArmTelemetry()
    telemetry.repository_context_receipts.append(
        _receipt(source="graph", head="b" * 40, digest="a" * 64)
    )
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *_args, **_kwargs: advisory_check_real.AdvisoryOutput(
            {
                "recommended_decision": "proceed",
                "risk_level": "low",
                "advisory": "No significant concerns were detected.",
                "detail": {},
            },
            assessment_id="asm_1",
            raw_payload={
                "graph_provenance": {
                    "repo_head": "b" * 40,
                    "graph_scope_digest": "a" * 64,
                }
            },
        ),
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_CONTEXT,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
        required_context_source="graph",
    )

    output = backend(
        {
            "target_file": "src/a.ts",
            "change_summary": "change helper",
            "proposed_patch": "diff --git a/src/a.ts b/src/a.ts",
        }
    )

    assert output["recommended_decision"] == "proceed"
    assert telemetry.last_assessment_id == "asm_1"
