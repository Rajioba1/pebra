"""Phase 3c — exercises the REAL mcp SDK glue so the gate catches SDK API drift in serve().

Skipped when the mcp SDK isn't installed: the default `tests` nox env is intentionally SDK-free to
prove the lazy-import contract (handlers load without mcp). The dedicated `mcp-smoke` nox session
installs `mcp>=1.0,<2` and runs this file, hitting Tool/TextContent construction, low-level Server
registration, and create_initialization_options() — the surfaces most likely to drift across SDK
releases — without spinning up stdio.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from pebra.mcp_server import server

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="requires the mcp SDK (run via nox -s mcp-smoke)",
)

FIXTURE = Path(__file__).resolve().parents[2] / "examples" / "login_patch.json"


def test_build_server_registers_the_five_tools() -> None:
    srv = server._build_server()
    assert srv.create_initialization_options() is not None
    tools = server._tool_definitions()
    assert {t.name for t in tools} == set(server._HANDLERS)
    for tool in tools:
        assert tool.inputSchema["type"] == "object"


def test_dispatch_unknown_tool_returns_structured_error() -> None:
    (content,) = server._dispatch("nope", {})
    assert content.type == "text"
    assert json.loads(content.text)["error"].startswith("unknown tool")


def test_dispatch_assess_wraps_payload_as_textcontent(tmp_path) -> None:
    req = json.loads(FIXTURE.read_text(encoding="utf-8"))
    (content,) = server._dispatch(
        "pebra_assess",
        {
            "task": req["task"],
            "action": req["candidate_actions"][0],
            "evidence": req["evidence"],
            "thresholds": req["thresholds"],
            "repo_root": str(tmp_path),
            "db": str(tmp_path / "pebra.db"),
        },
    )
    assert json.loads(content.text)["recommended_decision"] == "proceed"


def test_dispatch_catches_malformed_compare(tmp_path) -> None:
    (content,) = server._dispatch(
        "pebra_compare",
        {
            "task": "t",
            "candidate_actions": ["bad"],
            "repo_root": str(tmp_path),
            "db": str(tmp_path / "x.db"),
        },
    )
    assert "error" in json.loads(content.text)  # ValueError -> structured error, not a crash
