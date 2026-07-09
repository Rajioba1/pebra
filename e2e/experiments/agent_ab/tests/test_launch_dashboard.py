"""The dashboard launch helper discovers a run's per-arm PEBRA stores and builds the launch command."""

from __future__ import annotations

import sys

import pytest

from e2e.experiments.agent_ab.runners import launch_dashboard as ld


def _make_arm(ab_out, run_id, clone, *, with_repo=True):
    d = ab_out / run_id / clone
    (d / "repo").mkdir(parents=True) if with_repo else d.mkdir(parents=True)
    (d / "pebra.db").write_text("", encoding="utf-8")
    return d


def test_list_run_dbs_finds_stores_and_repos(tmp_path):
    _make_arm(tmp_path, "run1", "JS1_seed0_aaaaaaaaaaaa")
    _make_arm(tmp_path, "run1", "JS1_seed0_bbbbbbbbbbbb", with_repo=False)
    nested = tmp_path / "run1" / "JS1_seed0_aaaaaaaaaaaa" / "repo" / "nested"
    nested.mkdir(parents=True)
    (nested / "pebra.db").write_text("", encoding="utf-8")
    dbs = ld.list_run_dbs("run1", ab_out=tmp_path)
    assert len(dbs) == 2
    by_clone = {d["clone"]: d for d in dbs}
    assert by_clone["JS1_seed0_aaaaaaaaaaaa"]["repo"] is not None
    assert by_clone["JS1_seed0_bbbbbbbbbbbb"]["repo"] is None  # no sibling repo/ -> flagged


def test_list_run_dbs_empty_for_unknown_run(tmp_path):
    assert ld.list_run_dbs("nope", ab_out=tmp_path) == []


def test_list_run_dbs_rejects_path_like_run_id(tmp_path):
    with pytest.raises(ValueError, match="run-id"):
        ld.list_run_dbs("../escape", ab_out=tmp_path)
    with pytest.raises(ValueError, match="run-id"):
        ld.list_run_dbs("C:/escape", ab_out=tmp_path)


def test_dashboard_command_shape():
    cmd = ld.dashboard_command("/r", "/r/../pebra.db", 4500)
    assert cmd[:4] == [sys.executable, "-m", "pebra", "dashboard"]
    assert "--open" in cmd and "4500" in cmd
    # write-free posture: --db + --repo-id + --read-only, and NO --repo-root (which would init .pebra/).
    assert "--db" in cmd and "--repo-id" in cmd and "--read-only" in cmd
    assert "--repo-root" not in cmd
    assert ld.repo_id_for("/r") in cmd  # the repo_id is derived from the repo, not passed as --repo-root


def test_render_command_quotes_paths_with_spaces():
    cmd = ld.dashboard_command("C:/with space/repo", "C:/with space/pebra.db", 4500)
    rendered = ld.render_command(cmd)
    assert '"C:/with space/pebra.db"' in rendered  # the db path (the only path in the command) is quoted


def test_main_prints_command_for_a_real_store(tmp_path, capsys):
    _make_arm(tmp_path, "run1", "JS1_seed0_aaaaaaaaaaaa")
    import e2e.experiments.agent_ab.runners.launch_dashboard as mod
    mod._AB_OUT = tmp_path  # redirect discovery root
    rc = ld.main(["--run-id", "run1", "--port", "4500"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pebra dashboard" in out and "--open" in out


def test_main_fails_when_no_store(tmp_path):
    import e2e.experiments.agent_ab.runners.launch_dashboard as mod
    mod._AB_OUT = tmp_path
    assert ld.main(["--run-id", "empty"]) == 1


def test_main_fails_for_path_like_run_id(tmp_path):
    import e2e.experiments.agent_ab.runners.launch_dashboard as mod
    mod._AB_OUT = tmp_path
    assert ld.main(["--run-id", "../escape"]) == 1
