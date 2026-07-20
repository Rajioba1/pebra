from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pebra.core.engine_argv import resolve_engine_argv


@pytest.mark.skipif(os.name != "nt", reason="Windows command-shim security canary")
def test_windows_node_resolution_preserves_metacharacters_without_running_shim(tmp_path) -> None:
    launcher = tmp_path / "npm" / "codegraph.cmd"
    script = (
        launcher.parent / "node_modules" / "@colbymchenry" / "codegraph"
        / "dist" / "bin" / "codegraph.js"
    )
    output = tmp_path / "argv.json"
    marker = tmp_path / "shim-ran.txt"
    script.parent.mkdir(parents=True)
    launcher.write_text(f'@echo unsafe>"{marker}"\r\n', encoding="utf-8")
    script.write_text(
        "const fs=require('fs'); fs.writeFileSync(process.env.ARGV_OUT, "
        "JSON.stringify(process.argv.slice(2)));\n",
        encoding="utf-8",
    )
    query = "query&|<>^()%!"
    file_arg = "src/file&|<>^()%!.py"
    repo_arg = r"C:\repo&|<>^()%!"

    argv = resolve_engine_argv(str(launcher), [query, file_arg, repo_arg])
    proc = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        env={**os.environ, "ARGV_OUT": str(output)},
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert json.loads(output.read_text(encoding="utf-8")) == [query, file_arg, repo_arg]
    assert not marker.exists()
    assert Path(argv[0]).name.lower() == "node.exe"


@pytest.mark.skipif(os.name != "nt", reason="Windows command-shim security canary")
def test_windows_npm_resolution_runs_real_version_without_cmd() -> None:
    npm = shutil.which("npm")
    assert npm is not None and npm.lower().endswith((".cmd", ".bat"))

    argv = resolve_engine_argv(npm, ["--version"])
    proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=30)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()
    assert Path(argv[0]).name.lower() == "node.exe"
    assert Path(argv[1]).name.lower() == "npm-cli.js"
    assert not any(value.lower().endswith((".cmd", ".bat")) for value in argv[:2])


@pytest.mark.skipif(os.name != "nt", reason="Windows command-shim security canary")
def test_windows_npm_metacharacters_are_literal_and_shim_never_runs(tmp_path) -> None:
    real_node = shutil.which("node")
    assert real_node is not None
    launcher = tmp_path / "npm" / "npm.cmd"
    script = launcher.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    output = tmp_path / "npm-argv.json"
    marker = tmp_path / "npm-shim-ran.txt"
    script.parent.mkdir(parents=True)
    launcher.write_text(f'@echo unsafe>"{marker}"\r\n', encoding="utf-8")
    script.write_text(
        "const fs=require('fs'); fs.writeFileSync(process.env.ARGV_OUT, "
        "JSON.stringify(process.argv.slice(2)));\n",
        encoding="utf-8",
    )
    values = ["install", "pkg&|<>^()%!", r"C:\repo&|<>^()%!"]

    argv = resolve_engine_argv(str(launcher), values)
    proc = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        env={**os.environ, "ARGV_OUT": str(output)},
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert json.loads(output.read_text(encoding="utf-8")) == values
    assert not marker.exists()
