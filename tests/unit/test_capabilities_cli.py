from __future__ import annotations

import json

from pebra.cli import capabilities
from pebra.cli.main import build_parser


def test_capabilities_json_includes_enforcement_modes(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(capabilities.composition, "probe_language_capabilities", lambda root: [])
    seen = {}

    def _probe(root, *, graph_available, git_available=None):
        seen["graph_available"] = graph_available
        return {
            "claude": {"mode": "advisory_only", "candidate_bound": False, "reasons": []},
            "codex": {"mode": "best_effort", "candidate_bound": True, "reasons": []},
            "mcp": {"mode": "advisory_only", "candidate_bound": False, "reasons": []},
        }

    monkeypatch.setattr(
        capabilities.enforcement_capability,
        "probe",
        _probe,
    )

    args = build_parser().parse_args(["capabilities", "--repo-root", str(tmp_path), "--json"])
    assert capabilities.run_capabilities(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["enforcement"]["claude"]["mode"] == "advisory_only"
    assert payload["enforcement"]["codex"]["mode"] == "best_effort"
    assert seen["graph_available"] is False
