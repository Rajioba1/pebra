from __future__ import annotations

import os
from pathlib import Path

from scripts import demo_observatory


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_prepare_demo_uses_dedicated_store_without_touching_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    (checkout / ".pebra").mkdir()
    (checkout / ".pebra" / "pebra.db").write_bytes(b"real-ledger-sentinel\x00")
    (checkout / "tracked.py").write_text("REAL = True\n", encoding="utf-8")
    before = _tree_bytes(checkout)
    workspace = tmp_path / "demo-workspace"
    monkeypatch.chdir(checkout)

    demo = demo_observatory.prepare_demo(workspace)
    command, _env = demo_observatory.launch_spec(demo, surface="tui")

    assert _tree_bytes(checkout) == before
    assert demo.db_path == workspace / "pebra-demo.db"
    assert demo.db_path.is_file()
    assert demo.db_path != checkout / ".pebra" / "pebra.db"
    assert demo.repo_id.startswith("repo_demo_")
    assert demo.label == "DEMO"
    assert demo.assessment_count >= 5
    assert str(checkout) not in command


def test_demo_rows_are_varied_and_include_terminal_outcomes(tmp_path: Path) -> None:
    from pebra.adapters.store.db import SqliteStore

    demo = demo_observatory.prepare_demo(tmp_path / "demo")
    store = SqliteStore(str(demo.db_path), read_only=True)
    try:
        rows = store.list_assessments(demo.repo_id, limit=100)
        details = [store.assessment_detail(row["assessment_id"]) for row in rows]
    finally:
        store.close()

    assert len(rows) == demo.assessment_count
    assert len({row["task"] for row in rows}) == len(rows)
    assert len({tuple(row["target_files"]) for row in rows}) == len(rows)
    assert len({row["decision"] for row in rows}) >= 5
    assert len({tuple(sorted(row["scores"].items())) for row in rows}) == len(rows)
    assert len({row["assessed_commit"] for row in rows}) == len(rows)
    assert all(row["assessed_at"] for row in rows)
    assert any(detail["outcomes"] for detail in details)


def test_launch_command_is_read_only_explicit_and_visibly_demo(tmp_path: Path) -> None:
    demo = demo_observatory.prepare_demo(tmp_path / "demo")

    command, env = demo_observatory.launch_spec(demo, surface="tui")

    assert command[:3] == [os.fspath(Path(os.sys.executable)), "-m", "pebra"]
    assert command[3:] == [
        "tui",
        "--read-only",
        "--db",
        str(demo.db_path),
        "--repo-id",
        demo.repo_id,
    ]
    assert env[demo_observatory.DEMO_LABEL_ENV] == "DEMO"
    assert "--repo-root" not in command


def test_parser_exposes_only_developer_demo_switches() -> None:
    parser = demo_observatory.build_parser()

    assert parser.parse_args([]).surface == "tui"
    assert parser.parse_args(["--dashboard"]).surface == "dashboard"
    assert parser.parse_args(["--tui", "--keep"]).keep is True
