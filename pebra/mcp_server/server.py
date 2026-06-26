"""PEBRA MCP stdio server (Phase 3c) — exposes the assess/verify/sanction surfaces over MCP.

Pattern (graphify serve.py): the ``mcp`` SDK is imported *only* inside ``serve()`` so this module — and
every ``_handle_*`` function — loads in the dep-light env without the SDK. Handlers are thin: they map
the tool arguments onto the shared composition root, call the app controller, and return a
JSON-serialisable dict whose bytes match the corresponding ``pebra ... --json`` CLI surface.

mcp_server is a sibling surface of cli (it must never import cli); both delegate wiring to
``pebra.composition`` so the CLI and MCP results can't drift.
"""

from __future__ import annotations

import json
from typing import Any

from pebra import composition
from pebra.app import (
    accept_risk_controller,
    assess_controller,
    record_outcome_controller,
    verify_controller,
)
from pebra.core import candidate_parser

# --- tool schemas (plain dicts; turned into mcp.types.Tool lazily in serve()) --

_COMMON_PROPS = {
    "repo_root": {"type": "string", "description": "Repo root (defaults to the current directory)."},
    "db": {"type": "string", "description": "SQLite store path (defaults to <repo>/.pebra/pebra.db)."},
}

_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "pebra_assess": {
        "description": (
            "Assess a single candidate edit (short form) and return the recommended decision, "
            "scores, rationale and the model-guidance packet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What the edit is trying to achieve."},
                "action": {
                    "type": "object",
                    "description": "One candidate action (id, label, action_type, affected_symbols, "
                    "expected_files, proposed_patch, is_*_change flags).",
                },
                "evidence": {"type": "object", "description": "Evidence block (events, p_success, ...)."},
                "thresholds": {"type": "object", "description": "Threshold overrides."},
                **_COMMON_PROPS,
            },
            "required": ["task", "action"],
        },
    },
    "pebra_compare": {
        "description": (
            "Assess several candidate actions for one task and return every scored action plus the "
            "recommended one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "candidate_actions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Two or more candidate actions to compare.",
                },
                "evidence": {"type": "object"},
                "thresholds": {"type": "object"},
                **_COMMON_PROPS,
            },
            "required": ["task", "candidate_actions"],
        },
    },
    "pebra_verify": {
        "description": (
            "Check the actual diff against a stored assessment's approved envelope and return the "
            "pre-commit decision."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "assessment_id": {"type": "string", "description": "The stored assessment id (e.g. asm_1)."},
                "scope": {"type": "string", "enum": ["staged", "all", "branch"], "default": "staged"},
                "completed_checks": {
                    "type": "object",
                    "description": "Map of required-check -> status, e.g. {\"pytest -q\": \"passed\"}.",
                },
                "dry_run_preview": {"type": "boolean", "default": False},
                **_COMMON_PROPS,
            },
            "required": ["assessment_id"],
        },
    },
    "pebra_accept_risk": {
        "description": "Create a controlled-high-risk sanction bound to a risk profile (AD-26).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sanction_spec": {
                    "type": "object",
                    "description": "Sanction spec; must include a risk_profile.",
                },
                **_COMMON_PROPS,
            },
            "required": ["sanction_spec"],
        },
    },
    "pebra_record_outcome": {
        "description": "Record the terminal outcome (completed/skipped/rejected) of an assessed action.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "assessment_id": {"type": "string"},
                "status": {"type": "string", "enum": ["completed", "skipped", "rejected"]},
                "detail": {
                    "type": "object",
                    "description": "Optional result detail. Recognized learning labels (4b): "
                    "actual_success (bool), event_outcomes ({event: bool}), benefit_realized (bool), "
                    "actual_review_cost (number), actual_rework_cost (number). Absent -> censored.",
                },
                **_COMMON_PROPS,
            },
            "required": ["assessment_id", "status"],
        },
    },
}


# --- handlers (sync, SDK-free; raise ValueError/KeyError on bad input) ----------


def _require_action_dicts(actions: list[Any]) -> list[dict[str, Any]]:
    """Candidate actions must be JSON objects. A non-dict (e.g. a bare string) would make the pure
    parser raise AttributeError — which the call_tool dispatcher does NOT catch — and break the stdio
    frame. Surface it as a ValueError instead so it returns a structured tool error."""
    if not all(isinstance(a, dict) for a in actions):
        raise ValueError("each candidate action must be a JSON object")
    return actions


def _handle_assess(arguments: dict[str, Any]) -> dict[str, Any]:
    action = arguments.get("action") or {}
    if not isinstance(action, dict):
        raise ValueError("'action' must be a JSON object")
    request = candidate_parser.parse(
        {
            "task": arguments.get("task", ""),
            "candidate_actions": [action],
            "evidence": arguments.get("evidence") or {},
            "thresholds": arguments.get("thresholds") or {},
        }
    )
    start_path = arguments.get("repo_root") or "."
    ctx = composition.resolve_repo_and_db(start_path, arguments.get("db"))
    try:
        outcome = assess_controller.assess(
            request,
            thresholds=request.thresholds,
            start_path=start_path,
            **composition.build_assess_ports(request, ctx),
        )
        return composition.assess_payload(outcome)
    finally:
        ctx.store.close()


def _handle_compare(arguments: dict[str, Any]) -> dict[str, Any]:
    request = candidate_parser.parse(
        {
            "task": arguments.get("task", ""),
            "candidate_actions": _require_action_dicts(
                list(arguments.get("candidate_actions") or [])
            ),
            "evidence": arguments.get("evidence") or {},
            "thresholds": arguments.get("thresholds") or {},
        }
    )
    start_path = arguments.get("repo_root") or "."
    ctx = composition.resolve_repo_and_db(start_path, arguments.get("db"))
    try:
        outcome = assess_controller.assess(
            request,
            thresholds=request.thresholds,
            start_path=start_path,
            **composition.build_assess_ports(request, ctx),
        )
        return composition.compare_payload(outcome)
    finally:
        ctx.store.close()


def _handle_verify(arguments: dict[str, Any]) -> dict[str, Any]:
    ctx = composition.resolve_repo_and_db(arguments.get("repo_root") or ".", arguments.get("db"))
    try:
        outcome = verify_controller.verify(
            arguments["assessment_id"],
            scope=arguments.get("scope", "staged"),
            completed_checks=dict(arguments.get("completed_checks") or {}),
            dry_run_preview_present=bool(arguments.get("dry_run_preview", False)),
            repo_root=ctx.repo.repo_root,
            store=ctx.store,
            **composition.build_verify_ports(),
        )
        return composition.verify_payload(outcome)
    finally:
        ctx.store.close()


def _handle_accept_risk(arguments: dict[str, Any]) -> dict[str, Any]:
    ctx = composition.resolve_repo_and_db(arguments.get("repo_root") or ".", arguments.get("db"))
    try:
        sid = accept_risk_controller.accept_risk(
            ctx.repo.repo_id,
            dict(arguments.get("sanction_spec") or {}),
            sanction_port=composition.build_sanction_port(ctx),
        )
        return {"sanction_id": sid, "repo_id": ctx.repo.repo_id}
    finally:
        ctx.store.close()


def _handle_record_outcome(arguments: dict[str, Any]) -> dict[str, Any]:
    ctx = composition.resolve_repo_and_db(arguments.get("repo_root") or ".", arguments.get("db"))
    try:
        record_outcome_controller.record_outcome(
            arguments["assessment_id"],
            arguments["status"],
            outcome_port=ctx.store,
            detail=arguments.get("detail"),
        )
        return {
            "assessment_id": arguments["assessment_id"],
            "status": arguments["status"],
            "recorded": True,
        }
    finally:
        ctx.store.close()


_HANDLERS = {
    "pebra_assess": _handle_assess,
    "pebra_compare": _handle_compare,
    "pebra_verify": _handle_verify,
    "pebra_accept_risk": _handle_accept_risk,
    "pebra_record_outcome": _handle_record_outcome,
}


# --- stdio glue (the only place the mcp SDK is imported) -----------------------
#
# Split into small lazy pieces so the `mcp-smoke` nox session can exercise the real SDK API
# (Tool/TextContent construction, Server registration, init options) without spinning up stdio — the
# default `tests` env stays SDK-free to prove the lazy-import contract.


def _tool_definitions() -> list[Any]:
    """The MCP Tool list, built from _TOOL_SCHEMAS. Exercises ``mcp.types.Tool`` construction."""
    import mcp.types as types

    return [
        types.Tool(name=name, description=spec["description"], inputSchema=spec["inputSchema"])
        for name, spec in _TOOL_SCHEMAS.items()
    ]


def _dispatch(name: str, arguments: dict[str, Any] | None) -> list[Any]:
    """Route a tool call to its handler and wrap the JSON result as a single TextContent. Known caller
    errors (validation, unknown id, duplicate outcome) become a structured error rather than a broken
    stdio frame; unexpected errors still propagate."""
    import mcp.types as types

    handler = _HANDLERS.get(name)
    if handler is None:
        payload: dict[str, Any] = {"error": f"unknown tool: {name}"}
    else:
        try:
            payload = handler(arguments or {})
        except (ValueError, KeyError) as exc:
            payload = {"error": str(exc)}
    return [types.TextContent(type="text", text=json.dumps(payload, sort_keys=True))]


def _build_server() -> Any:
    """Create and configure the low-level MCP Server (registers the list_tools/call_tool handlers)."""
    from mcp.server import Server

    server = Server("pebra")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return _tool_definitions()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        return _dispatch(name, arguments)

    return server


def serve() -> None:
    """Run the PEBRA MCP server over stdio. Lazy-imports the ``mcp`` SDK so the rest of this module
    stays importable (and testable) without it."""
    import asyncio

    from mcp.server.stdio import stdio_server

    server = _build_server()

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())
