"""Pure hook ownership and inspection-state contract."""

from __future__ import annotations

import pytest

from pebra.core import agent_hook_contract as contract

_MATCHER = "Edit|Write|MultiEdit"


@pytest.mark.parametrize(
    ("document", "expected"),
    (
        ({}, "absent"),
        ({"hooks": {}}, "absent"),
        ({"hooks": {"PreToolUse": [contract.managed_hook_entry(_MATCHER)]}}, "exact"),
        ({"hooks": {"PreToolUse": [
            contract.managed_hook_entry(_MATCHER),
            {"matcher": "Read", "hooks": [{"type": "command", "command": "echo ok"}]},
        ]}}, "exact"),
        ({"hooks": {"PreToolUse": [
            contract.managed_hook_entry(_MATCHER), 42,
        ]}}, "malformed"),
        ({"hooks": {"PreToolUse": [
            contract.managed_hook_entry(_MATCHER),
            {"matcher": "Read", "hooks": [42]},
        ]}}, "malformed"),
        ({"hooks": {"PreToolUse": [
            contract.managed_hook_entry(_MATCHER),
            {"matcher": "Read", "hooks": [
                {"type": "command", "command": contract.HOOK_COMMAND}
            ]},
        ]}}, "conflicting"),
        ({"hooks": {"PreToolUse": [
            {"matcher": _MATCHER, "hooks": {}},
        ]}}, "malformed"),
        ({"hooks": {"PreToolUse": [
            {"matcher": _MATCHER, "hooks": [
                {"type": "command", "command": "echo run-my-gate-hook-check"}
            ]},
        ]}}, "absent"),
        (None, "malformed"),
        ({"hooks": []}, "malformed"),
        ({"hooks": {"PreToolUse": {}}}, "malformed"),
    ),
)
def test_classify_hook_document(document, expected):
    assert contract.classify_hook_document(document, _MATCHER, host="claude") == expected


@pytest.mark.parametrize(
    ("host", "handler"),
    (
        ("claude", {"type": "command", "command": "echo ok"}),
        ("claude", {"type": "http", "url": "https://example.invalid/hook"}),
        ("claude", {"type": "mcp_tool", "server": "tools", "tool": "check"}),
        ("claude", {"type": "prompt", "prompt": "Review this edit"}),
        ("claude", {"type": "agent", "prompt": "Review this edit"}),
        ("codex", {"type": "command", "command": "echo ok"}),
        ("codex", {"type": "prompt", "prompt": "Review this edit"}),
        ("codex", {"type": "agent", "prompt": "Review this edit"}),
    ),
)
@pytest.mark.parametrize("matcher", (None, ""))
def test_exact_plus_valid_match_all_sibling_remains_exact(host, handler, matcher):
    sibling = {"hooks": [handler]}
    if matcher is not None:
        sibling["matcher"] = matcher
    document = {
        "hooks": {"PreToolUse": [contract.managed_hook_entry(_MATCHER), sibling]},
    }

    assert contract.classify_hook_document(document, _MATCHER, host=host) == "exact"


@pytest.mark.parametrize("host", ("claude", "codex"))
@pytest.mark.parametrize(
    "sibling",
    (
        {"matcher": "Read", "hooks": [{"type": "unknown", "value": "x"}]},
        {"matcher": "Read", "hooks": [{"type": "command"}]},
        {"matcher": "Read", "hooks": []},
    ),
)
def test_invalid_sibling_makes_exact_document_malformed(host, sibling):
    document = {
        "hooks": {"PreToolUse": [contract.managed_hook_entry(_MATCHER), sibling]},
    }

    assert contract.classify_hook_document(document, _MATCHER, host=host) == "malformed"


@pytest.mark.parametrize("host", ("claude", "codex"))
def test_expected_matcher_with_empty_handler_list_is_malformed(host):
    document = {
        "hooks": {"PreToolUse": [{"matcher": _MATCHER, "hooks": []}]},
    }

    assert contract.classify_hook_document(document, _MATCHER, host=host) == "malformed"


@pytest.mark.parametrize(
    "handler",
    (
        {"type": "http", "url": "https://example.invalid/hook"},
        {"type": "mcp_tool", "server": "tools", "tool": "check"},
    ),
)
def test_codex_rejects_claude_only_handler_types(handler):
    document = {"hooks": {"PreToolUse": [{"hooks": [handler]}]}}
    assert contract.classify_hook_document(document, _MATCHER, host="codex") == "malformed"


@pytest.mark.parametrize(
    ("host", "handler"),
    (
        ("claude", {"type": "http", "url": ""}),
        ("claude", {"type": "mcp_tool", "server": "tools"}),
        ("claude", {"type": "prompt", "prompt": 42}),
        ("codex", {"type": "agent", "prompt": ""}),
        ("codex", {"type": "command", "command": 42}),
    ),
)
def test_recognized_handler_requires_nonblank_host_fields(host, handler):
    document = {"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [handler]}]}}
    assert contract.classify_hook_document(document, _MATCHER, host=host) == "malformed"
