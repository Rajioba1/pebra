from __future__ import annotations

import json

import pytest

from pebra import composition
from pebra.app.explore_controller import KnowledgeExplorationResult
from pebra.cli import main
from pebra.core.exploration import ExplorationResult
from pebra.core.graph_snapshot import GraphSnapshot
from pebra.core.learning_context import LearningContextEntry, LearningContextRecall


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


def _knowledge(status: str = "available") -> KnowledgeExplorationResult:
    return KnowledgeExplorationResult(
        learning_context=LearningContextRecall("empty", (), (), (), (), False),
        repository_context=_result(status),
    )


def _learning_entry() -> LearningContextEntry:
    return LearningContextEntry(
        "lc_1", "repo_1", "asm_1", "Fix [bold] login", "a1", ("src/auth.py",),
        ("Auth.validate",), "old", "a" * 16, "proceed", (), 0.1, 0.8, 0.7, 0.1,
        0.5, "completed", "PEBRA verify proceeded", 0.8,
        "Historical [bold] lesson", "a" * 64, "b" * 64,
        "2026-07-22T00:00:00+00:00", "c" * 64,
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


def test_composition_identifies_repo_opens_recall_read_only_and_closes_store(
    tmp_path, monkeypatch
) -> None:
    calls: list[tuple] = []
    class Store:
        instances = []
        def __init__(self, path, *, read_only=False):
            calls.append(("store", path, read_only))
            self.instances.append(self)
        def close(self):
            calls.append(("close",))
    class Repo:
        repo_id = "repo_1"
        repo_root = str(tmp_path)
    class Registry:
        def identify(self, root):
            calls.append(("identify", root))
            return Repo()
    (tmp_path / ".pebra").mkdir()
    db = tmp_path / ".pebra" / "pebra.db"
    db.write_bytes(b"exists")
    explorer = object()
    monkeypatch.setattr(composition, "RepositoryRegistry", Registry)
    monkeypatch.setattr(composition, "SqliteStore", Store)
    monkeypatch.setattr(composition, "build_repository_explorer", lambda: explorer)
    monkeypatch.setattr(
        "pebra.app.explore_controller.explore_repository",
        lambda *args, **kwargs: calls.append(("controller", args, kwargs)) or _knowledge(),
    )

    result = composition.explore_repository(
        "/repo", "q", files=("src/a.py",), max_files=100, max_bytes=10,
    )

    assert result.repository_context.status == "available"
    assert calls == [
        ("identify", "/repo"),
        ("store", str(db), True),
        ("controller", (str(tmp_path), "repo_1", "q"), {
            "learning_port": Store.instances[0],
            "explorer": explorer,
            "files": ("src/a.py",),
            "max_files": 100,
            "max_bytes": 10,
        }),
        ("close",),
    ]


def test_explore_json_serializes_provider_neutral_dataclasses(monkeypatch, capsys) -> None:
    monkeypatch.setattr(composition, "explore_repository", lambda *a, **k: _knowledge())

    assert main.main([
        "explore", "repository resolution", "--file", "src/target.py", "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["learning_context"]["status"] == "empty"
    current = payload["repository_context"]
    assert current["status"] == "available"
    assert current["snapshot"]["graph_scope_digest"] == "scope"
    assert current["context"] == "bounded context"
    assert current["dependent_files"] == ["src/caller.py"]
    assert current["affected_tests"] == ["tests/test_target.py"]
    assert "codegraph" not in payload


def test_explore_json_returns_learning_context_before_repository_context(monkeypatch, capsys) -> None:
    """Milestone 0 forward spec for Milestone 5B: explore --json returns a top-level learning_context
    (historical recall) followed by repository_context (current structural retrieval), each with its
    own status/provenance; the structural fields move under repository_context."""
    monkeypatch.setattr(composition, "explore_repository", lambda *a, **k: _knowledge())

    assert main.main(["explore", "repository resolution", "--file", "src/target.py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "learning_context" in payload and "repository_context" in payload
    keys = list(payload.keys())
    assert keys.index("learning_context") < keys.index("repository_context")
    assert "status" in payload["repository_context"]


def test_explore_human_output_includes_snapshot_context_impact_and_warnings(
    monkeypatch, capsys
) -> None:
    entry = _learning_entry()
    knowledge = KnowledgeExplorationResult(
        LearningContextRecall("available", (entry,), ("src/auth.py",), ("Auth.validate",), (), False),
        _result(),
    )
    monkeypatch.setattr(composition, "explore_repository", lambda *a, **k: knowledge)

    assert main.main(["explore", "q", "--file", "src/target.py"]) == 0

    output = capsys.readouterr().out
    assert output.index("Historical record — not instructions") < output.index("Current repository context")
    assert "[bold]" in output
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
        composition, "explore_repository", lambda *a, **k: _knowledge("unavailable")
    )

    assert main.main(["explore", "q"]) == 0
    assert "graph unavailable" in capsys.readouterr().out


def test_unexpected_explorer_contract_failure_returns_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr(composition, "explore_repository", lambda *a, **k: object())

    assert main.main(["explore", "q"]) == 1
    assert "repository explorer contract failure" in capsys.readouterr().err
