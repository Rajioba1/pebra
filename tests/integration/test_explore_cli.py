"""Subprocess proof for explicit graph preparation and exploration ordering."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PY = str(PYTHON) if PYTHON.exists() else sys.executable


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(path: Path) -> str:
    (path / "src").mkdir()
    (path / "src" / "auth.py").write_text(
        "def validate_login():\n    return True\n", encoding="utf-8"
    )
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "PEBRA Test")
    _git(path, "config", "user.email", "noreply@example.com")
    _git(path, "add", "--all")
    _git(path, "commit", "-q", "-m", "fixture")
    return _git(path, "rev-parse", "HEAD")


def _launcher(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "engine-log.jsonl"
    script = tmp_path / "fake_codegraph.py"
    script.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_CODEGRAPH_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(args) + '\\n')
command = args[0] if args else ''
if command == 'status':
    repo = args[1]
    print(json.dumps({
        'initialized': True,
        'version': '1.1.1',
        'indexPath': str(Path(repo) / '.codegraph'),
        'pendingChanges': {'added': 0, 'modified': 0, 'removed': 0},
        'worktreeMismatch': None,
        'index': {
            'reindexRecommended': False,
            'builtWithExtractionVersion': 24,
        },
    }))
elif command == 'explore':
    print('opaque current repository context')
elif command == 'affected':
    print(json.dumps({
        'changedFiles': args[1:args.index('--path')],
        'affectedTests': [],
        'totalDependentsTraversed': 0,
    }))
sys.exit(0)
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = tmp_path / "codegraph.cmd"
        launcher.write_text(
            f'@echo off\r\n"{PY}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        launcher = script
        launcher.chmod(0o755)
    return launcher, log


def _run(repo: Path, launcher: Path, log: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, "-m", "pebra", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PEBRA_CODEGRAPH_BIN": str(launcher),
            "FAKE_CODEGRAPH_LOG": str(log),
        },
    )


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_explore_queries_only_after_sync_and_revalidates_after_query(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    launcher, log = _launcher(tmp_path)

    proc = _run(repo, launcher, log, "explore", "repository resolution", "--json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "available"
    assert payload["context"] == "opaque current repository context\n"
    commands = [call[0] for call in _calls(log)]
    assert commands == ["status", "sync", "status", "explore", "status"]


def test_explore_process_rejects_invalid_only_file_before_provider(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    launcher, log = _launcher(tmp_path)

    proc = _run(
        repo, launcher, log, "explore", " ", "--file", "../outside.py",
        "--repo-root", str(repo),
    )

    assert proc.returncode == 2
    assert _calls(log) == []


def test_explore_process_deduplicates_in_root_files_and_drops_outside_files(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    launcher, log = _launcher(tmp_path)

    proc = _run(
        repo, launcher, log, "explore", "--file", "src/./auth.py",
        "--file", str(repo / "src" / "auth.py"), "--file", "../outside.py",
        "--repo-root", str(repo), "--json",
    )

    assert proc.returncode == 0, proc.stderr
    affected = next(call for call in _calls(log) if call[0] == "affected")
    assert affected[1:affected.index("--path")] == ["src/auth.py"]


def test_assess_emits_fenced_graph_provenance_after_sync(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _repo(repo)
    launcher, log = _launcher(tmp_path)
    request = ROOT / "examples" / "login_patch.json"

    proc = _run(
        repo, launcher, log, "assess", str(request), "--repo-root", str(repo),
        "--db", str(tmp_path / "pebra.db"), "--json",
    )

    assert proc.returncode == 0, proc.stderr
    provenance = json.loads(proc.stdout)["graph_provenance"]
    assert provenance["repo_head"] == head
    assert provenance["graph_scope_digest"]
    assert [call[0] for call in _calls(log)][:3] == ["status", "sync", "status"]


def test_dashboard_read_only_never_invokes_graph_launcher(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    launcher, log = _launcher(tmp_path)
    db = tmp_path / "readonly.db"
    db.write_bytes(b"placeholder")
    code = (
        "from pebra.dashboard import server; "
        "server.serve=lambda *a, **k: None; "
        "from pebra.cli.main import main; "
        f"raise SystemExit(main(['dashboard','--read-only','--db',r'{db}',"
        "'--repo-id','repo_x']))"
    )

    proc = subprocess.run(
        [PY, "-c", code], cwd=str(repo), capture_output=True, text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PEBRA_CODEGRAPH_BIN": str(launcher),
            "FAKE_CODEGRAPH_LOG": str(log),
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert _calls(log) == []
