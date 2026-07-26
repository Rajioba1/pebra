from __future__ import annotations

import subprocess
from pathlib import Path

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import run_pair, slot_pool
from e2e.external.utils import repo_source as rs


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _external(tmp_path: Path) -> rs.ExternalRepo:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "PEBRA test")
    _git(source, "config", "user.email", "test@users.noreply.github.com")
    (source / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    (source / ".gitignore").write_text(
        "node_modules/\n.codegraph/\ndist/\n.agent-instructions/\n",
        encoding="utf-8",
    )
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "baseline")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return rs.ExternalRepo(
        source_path=source,
        copy_path=source,
        head_sha=head,
        dirty_source=False,
    )


def test_assign_slots_is_unique_and_rotates_between_run_ids() -> None:
    arms = ("sham", "graph_context", "pebra", "pebra_graph_repair")

    first = slot_pool.assign_slots(arms, run_id="run-a", task_id="JS4", seed=0)
    alternatives = [
        slot_pool.assign_slots(arms, run_id=run_id, task_id="JS4", seed=0)
        for run_id in ("run-b", "run-c", "run-d", "run-e")
    ]

    assert set(first) == set(arms)
    assert set(first.values()) == set(range(len(arms)))
    assert all(set(item.values()) == set(range(len(arms))) for item in alternatives)
    assert any(
        any(first[arm] != item[arm] for arm in arms)
        for item in alternatives
    )


def test_reused_slot_resets_trial_state_but_preserves_graph_and_dependencies(
    tmp_path: Path,
) -> None:
    external = _external(tmp_path)
    slots_root = tmp_path / "slots"

    first = slot_pool.acquire_slot(external, 0, slots_root=slots_root)
    try:
        assert first.reused is False
        (first.repo_path / "tracked.txt").write_text("agent edit\n", encoding="utf-8")
        (first.repo_path / "trial-junk.txt").write_text("junk\n", encoding="utf-8")
        (first.repo_path / "dist").mkdir()
        (first.repo_path / "dist" / "output.js").write_text("built\n", encoding="utf-8")
        (first.repo_path / ".agent-instructions").mkdir()
        (first.repo_path / ".agent-instructions" / "edit_protocol.md").write_text(
            "old arm\n", encoding="utf-8"
        )
        (first.repo_path / "node_modules").mkdir()
        (first.repo_path / "node_modules" / ".modules.yaml").write_text(
            "layoutVersion: 5\n", encoding="utf-8"
        )
        (first.repo_path / ".codegraph").mkdir()
        (first.repo_path / ".codegraph" / "codegraph.db").write_bytes(b"index")
    finally:
        first.release()

    second = slot_pool.acquire_slot(external, 0, slots_root=slots_root)
    try:
        assert second.reused is True
        assert second.generation == first.generation + 1
        assert (second.repo_path / "tracked.txt").read_text(encoding="utf-8") == "baseline\n"
        assert not (second.repo_path / "trial-junk.txt").exists()
        assert not (second.repo_path / "dist").exists()
        assert not (second.repo_path / ".agent-instructions").exists()
        assert (second.repo_path / "node_modules" / ".modules.yaml").is_file()
        assert (second.repo_path / ".codegraph" / "codegraph.db").read_bytes() == b"index"
    finally:
        second.release()


def test_slot_source_change_uses_a_distinct_pool(tmp_path: Path) -> None:
    external = _external(tmp_path)
    slots_root = tmp_path / "slots"
    first = slot_pool.acquire_slot(external, 0, slots_root=slots_root)
    first_path = first.repo_path
    first.release()

    (external.source_path / "tracked.txt").write_text("next\n", encoding="utf-8")
    _git(external.source_path, "add", "tracked.txt")
    _git(external.source_path, "commit", "-qm", "next")
    next_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=external.source_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    updated = rs.ExternalRepo(
        source_path=external.source_path,
        copy_path=external.copy_path,
        head_sha=next_head,
        dirty_source=False,
    )

    second = slot_pool.acquire_slot(updated, 0, slots_root=slots_root)
    try:
        assert second.repo_path != first_path
        assert second.reused is False
        assert (second.repo_path / "tracked.txt").read_text(encoding="utf-8") == "next\n"
    finally:
        second.release()


def test_prepare_arm_initializes_graph_once_then_uses_incremental_admission(
    monkeypatch, tmp_path: Path
) -> None:
    external = _external(tmp_path)
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path / "runs")
    monkeypatch.setattr(slot_pool, "SLOTS_ROOT", tmp_path / "slots")
    setup_calls: list[Path] = []
    explore_calls: list[Path] = []
    fail_next_explore = {"value": False}

    def _setup_graph(*, repo_root: Path) -> None:
        setup_calls.append(repo_root)
        graph = repo_root / ".codegraph"
        graph.mkdir()
        (graph / "codegraph.db").write_bytes(b"index")

    def _explore(*_args, repo_root: Path, **_kwargs):
        explore_calls.append(repo_root)
        if fail_next_explore["value"]:
            fail_next_explore["value"] = False
            return {
                "status": "unavailable",
                "snapshot": {"status": "stale"},
                "fallback_reason": "incremental sync failed",
            }
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return {
            "status": "available",
            "related_files": ["tracked.txt"],
            "related_tests": [],
            "context": "tracked.txt",
            "snapshot": {
                "status": "available",
                "repo_head": head,
                "graph_scope_digest": "a" * 64,
            },
        }

    class _Backend:
        def run_build_delta(self, repo_path, _spec):
            modules = repo_path / "node_modules"
            modules.mkdir(exist_ok=True)
            (modules / ".modules.yaml").write_text("layoutVersion: 5\n", encoding="utf-8")
            return type(
                "Build", (), {
                    "available": True,
                    "ran": True,
                    "passed": True,
                    "error_summary": "",
                },
            )()

    spec = TaskSpec(
        "JS4",
        "inspect tracked source",
        ("tracked.txt",),
        "risky",
        ("tracked.txt",),
        "build_failure",
        True,
        language="typescript",
        harness_id="node",
        required_language_tier="full",
    )
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", _setup_graph)
    monkeypatch.setattr(
        run_pair.cli_harness, "explore_repository_context", _explore
    )
    monkeypatch.setattr(
        run_pair.cli_harness,
        "graph_node_counts",
        lambda *, repo_root: {"csharp_callable": 0},
    )
    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda _spec: _Backend())

    first = run_pair.prepare_arm(
        external,
        spec,
        models.ARM_PEBRA,
        0,
        "run-a",
        expected_graph_scope_digest="a" * 64,
        slot_index=0,
    )
    first_repo = first.repo_path
    assert first.workspace_path == (
        tmp_path
        / "runs"
        / "run-a"
        / f"JS4_seed0_{run_pair._arm_token(models.ARM_PEBRA, 'run-a')}"
    )
    assert (first.workspace_path / "slot-receipt.json").is_file()
    first.slot_lease.release()

    (first_repo / "tracked.txt").write_text("agent edit\n", encoding="utf-8")
    (first_repo / "trial-junk.txt").write_text("junk\n", encoding="utf-8")

    second = run_pair.prepare_arm(
        external,
        spec,
        models.ARM_PEBRA,
        0,
        "run-b",
        expected_graph_scope_digest="a" * 64,
        slot_index=0,
    )
    try:
        assert second.repo_path == first_repo
        assert second.slot_lease.reused is True
        assert (second.repo_path / "tracked.txt").read_text(encoding="utf-8") == "baseline\n"
        assert not (second.repo_path / "trial-junk.txt").exists()
        assert (second.repo_path / "node_modules" / ".modules.yaml").is_file()
        assert setup_calls == [first_repo]
        assert explore_calls == [first_repo, first_repo]
    finally:
        second.slot_lease.release()

    fail_next_explore["value"] = True
    third = run_pair.prepare_arm(
        external,
        spec,
        models.ARM_PEBRA,
        0,
        "run-c",
        expected_graph_scope_digest="a" * 64,
        slot_index=0,
    )
    try:
        assert third.repo_path == first_repo
        assert third.slot_lease.reused is False
        assert setup_calls == [first_repo, first_repo]
        assert explore_calls == [first_repo, first_repo, first_repo, first_repo]
    finally:
        third.slot_lease.release()
