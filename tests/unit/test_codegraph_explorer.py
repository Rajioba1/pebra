from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from pebra.adapters.codegraph_explorer import CodeGraphExplorer
from pebra.core.graph_snapshot import GraphSnapshot


def _snapshot(status: str = "available") -> GraphSnapshot:
    return GraphSnapshot(
        status=status,
        provider="CodeGraph",
        provider_version="1.1.1",
        index_version="24",
        repo_head="commit-b",
        config_digest="absent",
        graph_scope_digest="scope",
        sync_performed=True,
        fallback_reason=None if status == "available" else "not available",
    )


class _Graph:
    def __init__(self, *, revalidates: bool = True) -> None:
        self.snapshot = _snapshot()
        self.revalidates = revalidates
        self.prepare_calls: list[str] = []
        self.dependent_calls: list[tuple[str, str]] = []
        self.revalidate_calls: list[tuple[str, GraphSnapshot]] = []
        self.dependent_files = ["src/z.py", "src/a.py", "src/z.py"]

    def prepare(self, repo_root: str) -> GraphSnapshot:
        self.prepare_calls.append(repo_root)
        return self.snapshot

    def dependent_files_result(self, target: str, repo_root: str):
        self.dependent_calls.append((target, repo_root))
        return {
            "available": True,
            "graph_freshness": "fresh",
            "dependent_files": self.dependent_files,
            "count": len(self.dependent_files),
            "fallback_reason": None,
        }

    def revalidate_snapshot(self, repo_root: str, snapshot: GraphSnapshot) -> bool:
        self.revalidate_calls.append((repo_root, snapshot))
        return self.revalidates


class _Runner:
    def __init__(self, *, context: str = "opaque provider context", affected=None) -> None:
        self.context = context
        self.affected = affected or {
            "changedFiles": ["src/target.py"],
            "affectedTests": ["tests/test_target.py", "tests/test_target.py"],
            "totalDependentsTraversed": 2,
        }
        self.calls: list[list[str]] = []
        self.returncode = 0
        self.exception: BaseException | None = None

    def __call__(self, argv, **_kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if self.exception is not None:
            raise self.exception
        stdout = self.context if "explore" in argv else json.dumps(self.affected)
        return SimpleNamespace(returncode=self.returncode, stdout=stdout, stderr="")


def _explorer(graph: _Graph, runner: _Runner, engine: str | None = "/tools/codegraph"):
    return CodeGraphExplorer(graph_adapter=graph, runner=runner, engine_fn=lambda: engine)


def test_prepare_delegates_once_and_explore_locks_exact_argv() -> None:
    graph = _Graph()
    runner = _Runner()
    explorer = _explorer(graph, runner)

    snapshot = explorer.prepare("/repo")
    result = explorer.explore(
        "/repo", "repository resolution", snapshot=snapshot,
        files=("src/target.py",), max_files=2, max_bytes=24_000,
    )

    assert graph.prepare_calls == ["/repo"]
    assert runner.calls == [
        ["/tools/codegraph", "explore", "repository resolution", "--path", "/repo",
         "--max-files", "2"],
        ["/tools/codegraph", "affected", "src/target.py", "--path", "/repo", "--json"],
    ]
    assert graph.dependent_calls == [("src/target.py", "/repo")]
    assert graph.revalidate_calls == [("/repo", snapshot)]
    assert result.context == "opaque provider context"
    assert result.dependent_files == ("src/a.py", "src/z.py")
    assert result.affected_tests == ("tests/test_target.py",)


def test_file_only_mode_skips_free_text_explore_and_normalizes_duplicate_windows_paths() -> None:
    graph = _Graph()
    runner = _Runner(affected={
        "changedFiles": ["src/target.py"],
        "affectedTests": [],
        "totalDependentsTraversed": 0,
    })
    explorer = _explorer(graph, runner)
    snapshot = explorer.prepare("C:/repo")

    result = explorer.explore(
        "C:/repo", "", snapshot=snapshot,
        files=(r"src\target.py", "./src/target.py", r"src\target.py"),
    )

    assert runner.calls == [[
        "/tools/codegraph", "affected", "src/target.py", "--path", "C:/repo", "--json",
    ]]
    assert graph.dependent_calls == [("src/target.py", "C:/repo")]
    assert result.context == ""
    assert result.truncated is False


def test_files_canonicalize_and_reject_every_path_outside_resolved_repo(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"

    normalized = CodeGraphExplorer._files(
        str(repo),
        (
            "./src/./target.py",
            "src/nested/../target.py",
            str(repo / "src" / "target.py"),
            "../outside.py",
            "src/../../outside.py",
            str(outside),
        ),
    )

    assert normalized == ("src/target.py",)


def test_invalid_only_files_with_blank_query_return_error_without_provider_call(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = _Graph()
    runner = _Runner()
    explorer = _explorer(graph, runner)

    result = explorer.explore(
        str(repo), " ", snapshot=explorer.prepare(str(repo)),
        files=("../outside.py", str(tmp_path / "absolute-outside.py")),
    )

    assert result.status == "error"
    assert result.fallback_reason == "query or file is required"
    assert runner.calls == []
    assert graph.dependent_calls == []


def test_oversized_context_is_utf8_safe_and_explicitly_truncated() -> None:
    graph = _Graph()
    runner = _Runner(context="a" * 999 + "💥tail")
    explorer = _explorer(graph, runner)

    result = explorer.explore("/repo", "q", snapshot=explorer.prepare("/repo"), max_bytes=1_000)

    assert result.context == "a" * 999
    assert len(result.context.encode("utf-8")) <= 1_000
    assert result.truncated is True
    assert "provider context truncated to byte limit" in result.warnings


@pytest.mark.parametrize(
    "affected",
    [
        "not-json",
        [],
        {"changedFiles": [], "affectedTests": []},
        {"changedFiles": "src/a.py", "affectedTests": [], "totalDependentsTraversed": 0},
        {"changedFiles": [], "affectedTests": [1], "totalDependentsTraversed": 0},
        {"changedFiles": [], "affectedTests": [], "totalDependentsTraversed": True},
        {
            "changedFiles": [], "affectedTests": [], "totalDependentsTraversed": 0,
            "dependentFiles": ["must-not-be-trusted.py"],
        },
    ],
)
def test_malformed_or_wrong_affected_schema_never_populates_affected_tests(affected) -> None:
    graph = _Graph()
    runner = _Runner(affected={})
    runner.affected = affected
    explorer = _explorer(graph, runner)

    result = explorer.explore(
        "/repo", "q", snapshot=explorer.prepare("/repo"), files=("src/a.py",),
    )

    assert result.status == "available"
    assert result.affected_tests == ()
    assert result.warnings == ("codegraph affected output was malformed",)
    assert "must-not-be-trusted.py" not in result.dependent_files


@pytest.mark.parametrize(
    "raw",
    [
        '{"number":' + ("9" * 5_000) + "}",
        ("[" * 2_000) + "0" + ("]" * 2_000),
    ],
    ids=("oversized-integer", "deep-nesting"),
)
def test_pathological_affected_json_fails_soft(raw) -> None:
    tests, warning, truncated = CodeGraphExplorer._affected_tests(raw, 8)

    assert tests == ()
    assert warning == "codegraph affected output was malformed"
    assert truncated is False


@pytest.mark.parametrize("failure", ["missing", "timeout", "nonzero"])
def test_provider_failures_return_structured_empty_results(failure) -> None:
    graph = _Graph()
    runner = _Runner()
    engine = "/tools/codegraph"
    if failure == "missing":
        engine = None
    elif failure == "timeout":
        runner.exception = subprocess.TimeoutExpired(["codegraph"], 30)
    else:
        runner.returncode = 7
    explorer = _explorer(graph, runner, engine)

    result = explorer.explore("/repo", "q", snapshot=explorer.prepare("/repo"))

    assert result.status in ("unavailable", "error")
    assert result.context == ""
    assert result.dependent_files == ()
    assert result.affected_tests == ()
    assert result.fallback_reason


def test_unavailable_snapshot_never_spawns_or_fabricates() -> None:
    graph = _Graph()
    graph.snapshot = _snapshot("stale")
    runner = _Runner()
    explorer = _explorer(graph, runner)

    result = explorer.explore("/repo", "q", snapshot=explorer.prepare("/repo"))

    assert result.status == "stale"
    assert result.context == ""
    assert result.dependent_files == ()
    assert result.affected_tests == ()
    assert runner.calls == []
    assert graph.dependent_calls == []


def test_post_query_fence_failure_discards_all_provider_output() -> None:
    graph = _Graph(revalidates=False)
    runner = _Runner()
    explorer = _explorer(graph, runner)
    snapshot = explorer.prepare("/repo")

    result = explorer.explore(
        "/repo", "q", snapshot=snapshot, files=("src/target.py",),
    )

    assert result.status == "stale"
    assert result.context == ""
    assert result.dependent_files == ()
    assert result.affected_tests == ()
    assert "changed during exploration" in (result.fallback_reason or "")


def test_result_collections_are_count_and_string_bounded_by_max_files() -> None:
    graph = _Graph()
    graph.dependent_files = [
        "src/z.py", "src/a.py", "src/b.py", "x" * 5_000,
    ]
    runner = _Runner(affected={
        "changedFiles": ["src/target.py"],
        "affectedTests": ["tests/z.py", "tests/a.py", "tests/b.py", "y" * 5_000],
        "totalDependentsTraversed": 100,
    })
    explorer = _explorer(graph, runner)

    result = explorer.explore(
        "/repo", "q", snapshot=explorer.prepare("/repo"),
        files=("src/target.py",), max_files=2,
    )

    assert result.affected_tests == ("tests/a.py", "tests/b.py")
    assert result.dependent_files == ("src/a.py", "src/b.py")
    assert result.truncated is True
    assert "affected tests truncated to result limit" in result.warnings
    assert "dependent files truncated to result limit" in result.warnings


def test_fake_runner_oversized_affected_json_is_rejected_at_independent_cap() -> None:
    graph = _Graph()
    runner = _Runner(affected={
        "changedFiles": ["src/target.py"],
        "affectedTests": ["tests/" + "x" * 300_000],
        "totalDependentsTraversed": 1,
    })
    explorer = _explorer(graph, runner)

    result = explorer.explore(
        "/repo", "", snapshot=explorer.prepare("/repo"), files=("src/target.py",),
    )

    assert result.affected_tests == ()
    assert "affected output exceeded byte limit" in result.warnings
    assert result.truncated is True


def test_provider_exception_is_reported_without_raw_query_or_path() -> None:
    graph = _Graph()
    runner = _Runner()
    runner.exception = OSError("SECRET query /private/repo")
    explorer = _explorer(graph, runner)

    result = explorer.explore("/private/repo", "SECRET query", snapshot=explorer.prepare("/private/repo"))

    assert result.status == "error"
    assert result.fallback_reason == "codegraph query launch failed"
    assert "SECRET" not in (result.fallback_reason or "")
