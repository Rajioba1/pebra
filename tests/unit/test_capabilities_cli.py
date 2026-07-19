from __future__ import annotations

import json

from pebra.cli import capabilities
from pebra.cli.main import build_parser
from pebra.core.agent_hosts import AGENT_HOSTS


def test_capabilities_json_includes_enforcement_modes(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(capabilities.composition, "probe_language_capabilities", lambda root: [])
    seen = {}

    def _probe(root, *, graph_available, git_available=None):
        seen["graph_available"] = graph_available
        return {
            "claude": {"mode": "advisory_only", "candidate_bound": False, "reasons": []},
            "codex": {"mode": "best_effort", "candidate_bound": False, "reasons": []},
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
    assert payload["enforcement"]["codex"]["candidate_bound"] is False
    assert seen["graph_available"] is False


def test_capabilities_text_orders_registered_hosts_before_mcp(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(capabilities.composition, "probe_language_capabilities", lambda root: [])
    enforcement = {
        target: {
            "mode": spec.declared_support,
            "candidate_bound": spec.declared_support == "configured_enforcing",
            "reasons": [],
        }
        for target, spec in AGENT_HOSTS.items()
    }
    enforcement["mcp"] = {"mode": "advisory_only", "candidate_bound": False, "reasons": []}
    monkeypatch.setattr(
        capabilities.enforcement_capability,
        "probe",
        lambda root, *, graph_available: enforcement,
    )
    args = build_parser().parse_args(["capabilities", "--repo-root", str(tmp_path)])

    assert capabilities.run_capabilities(args) == 0

    output = capsys.readouterr().out
    positions = [output.index(target) for target in (*AGENT_HOSTS, "mcp")]
    assert positions == sorted(positions)
