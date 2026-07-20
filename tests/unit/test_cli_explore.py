from __future__ import annotations

import json

import pytest

from pebra import composition
from pebra.cli import main
from pebra.core.exploration import ExplorationResult
from pebra.core.graph_snapshot import GraphSnapshot


def _result(status: str = "available") -> ExplorationResult:
    snapshot = GraphSnapshot(
        status=status,
        provider="CodeGraph",
        provider_version="1.1.1",
        index_version="24",
        repo_head="commit-b",
        config_digest="config",
        graph_scope_digest="scope",
        sync_performed=True,
        fallback_reason=None,
    )
    return ExplorationResult(
        status=status,
        snapshot=snapshot,
        context="bounded context" if status == "available" else "",
        dependent_files=("src/caller.py",) if status == "available" else (),
        affected_tests=("tests/test_target.py",) if status == "available" else (),
        warnings=("bounded warning",),
        fallback_reason=None if status == "available" else "graph unavailable",
        truncated=True if status == "available" else False,
    )


def test_explore_parser_is_provider_neutral_and_requires_query_or_file() -> None:
    parser = main.build_parser()

    args = parser.parse_args(["explore", "q"])
    assert args.command == "explore"

    with pytest.raises(SystemExit) as exc:
        main.main(["explore"])

    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["explore", "q", "--provider", "codegraph"])
    assert exc.value.code == 2


def test_invalid_only_file_with_blank_query_is_argparse_error_before_composition(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple] = []
    monkeypatch.setattr(
        composition, "explore_repository", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    with pytest.raises(SystemExit) as exc:
        main.main([
            "explore", " ", "--file", "../outside.py", "--repo-root", str(repo),
        ])

    assert exc.value.code == 2
    assert calls == []


def test_composition_prepares_once_clamps_bounds_and_queries_same_snapshot(monkeypatch) -> None:
    calls: list[tuple] = []
    snapshot = _result().snapshot

    class Explorer:
        def prepare(self, repo_root):
            calls.append(("prepare", repo_root))
            return snapshot

        def explore(self, repo_root, query, **kwargs):
            calls.append(("explore", repo_root, query, kwargs))
            return _result()

    monkeypatch.setattr(composition, "CodeGraphExplorer", Explorer)

    result = composition.explore_repository(
        "/repo", "q", files=("src/a.py",), max_files=100, max_bytes=10,
    )

    assert result.status == "available"
    assert calls == [
        ("prepare", "/repo"),
        ("explore", "/repo", "q", {
            "snapshot": snapshot,
            "files": ("src/a.py",),
            "max_files": 32,
            "max_bytes": 1_000,
        }),
    ]


def test_explore_json_serializes_provider_neutral_dataclasses(monkeypatch, capsys) -> None:
    monkeypatch.setattr(composition, "explore_repository", lambda *a, **k: _result())

    assert main.main([
        "explore", "repository resolution", "--file", "src/target.py", "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "available"
    assert payload["snapshot"]["graph_scope_digest"] == "scope"
    assert payload["context"] == "bounded context"
    assert payload["dependent_files"] == ["src/caller.py"]
    assert payload["affected_tests"] == ["tests/test_target.py"]
    assert "codegraph" not in payload


def test_explore_human_output_includes_snapshot_context_impact_and_warnings(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(composition, "explore_repository", lambda *a, **k: _result())

    assert main.main(["explore", "q", "--file", "src/target.py"]) == 0

    output = capsys.readouterr().out
    assert "status: available" in output
    assert "snapshot HEAD: commit-b" in output
    assert "graph scope: scope" in output
    assert "bounded context" in output
    assert "src/caller.py" in output
    assert "tests/test_target.py" in output
    assert "bounded warning" in output
    assert "truncated: yes" in output


def test_handled_unavailable_result_returns_zero_and_reports_fallback(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        composition, "explore_repository", lambda *a, **k: _result("unavailable")
    )

    assert main.main(["explore", "q"]) == 0
    assert "graph unavailable" in capsys.readouterr().out


def test_unexpected_explorer_contract_failure_returns_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr(composition, "explore_repository", lambda *a, **k: object())

    assert main.main(["explore", "q"]) == 1
    assert "repository explorer contract failure" in capsys.readouterr().err
