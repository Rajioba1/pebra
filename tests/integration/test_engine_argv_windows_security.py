from __future__ import annotations

import json
import os
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
