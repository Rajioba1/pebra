from __future__ import annotations

import json
from pathlib import Path

from pebra.cli.main import main


def test_candidate_patch_cli_emits_canonical_json(tmp_path: Path, capsys) -> None:
    (tmp_path / "a.ts").write_text("export const before = 1;\n", encoding="utf-8")
    request = tmp_path / "edits.json"
    request.write_text(json.dumps({"edits": [{
        "path": "a.ts",
        "old_string": "before",
        "new_string": "after",
    }]}), encoding="utf-8")

    assert main([
        "candidate-patch",
        str(request),
        "--repo-root",
        str(tmp_path),
        "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["expected_files"] == ["a.ts"]
    assert "+export const after = 1;" in payload["proposed_patch"]
