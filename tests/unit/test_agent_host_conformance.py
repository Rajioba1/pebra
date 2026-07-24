from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import re

import pytest

from pebra.adapters import enforcement_capability
from pebra.cli import agent_init
from pebra.cli.main import build_parser
from pebra.core.agent_hook_contract import HOOK_COMMAND
from pebra.core.agent_hosts import AGENT_HOSTS, HostSpec
from pebra.core.constants import Decision


_SEMANTIC_TOKENS = (
    "Interpret → Recall verified lessons → Retrieve current repository context",
    "2. **Recall verified lessons.**",
    "3. **Retrieve current repository context.**",
    "Do not repeat equivalent exploration",
    "not trusted PEBRA scoring evidence",
    "repository search/read tools",
    "pebra assess",
    "revise_safer",
    "trusted human or host",
    "apply-candidate --assessment-id",
    "pebra verify",
    "record-outcome",
)


def test_every_host_uses_the_byte_identical_protocol_v5_projection(tmp_path) -> None:
    bodies = []
    for target, spec in AGENT_HOSTS.items():
        assert _run(target, tmp_path) == 0
        bodies.append((tmp_path / spec.skill_path).read_bytes())

    assert agent_init.PROTOCOL_VERSION == 5
    assert len(set(bodies)) == 1
    lowered = bodies[0].lower()
    for provider_detail in (
        b"codegraph", b"agentmemory", b"mcp", b"prompt hook", b"provider selector",
        b"localhost:", b"token savings",
    ):
        assert provider_detail not in lowered


def _run(target: str, repo_root: Path) -> int:
    args = build_parser().parse_args(
        ["agent-init", "--target", target, "--repo-root", str(repo_root)]
    )
    return args.func(args)


def _run_with_hook(target: str, repo_root: Path) -> int:
    args = build_parser().parse_args(
        [
            "agent-init",
            "--target",
            target,
            "--repo-root",
            str(repo_root),
            "--with-hook",
        ]
    )
    return args.func(args)


def test_host_spec_is_the_minimal_five_fact_surface() -> None:
    assert tuple(field.name for field in fields(HostSpec)) == (
        "skill_path",
        "instruction_paths",
        "hook_path",
        "hook_matcher",
        "declared_support",
    )


def test_parser_choices_match_registry() -> None:
    parser = build_parser()
    action = next(
        action
        for action in parser._subparsers._group_actions[0].choices["agent-init"]._actions
        if action.dest == "target"
    )
    assert tuple(action.choices) == (*AGENT_HOSTS, "auto")


def test_auto_target_detects_only_hosts_with_repository_markers(tmp_path) -> None:
    (tmp_path / ".claude").mkdir()

    assert _run("auto", tmp_path) == 0

    assert (tmp_path / AGENT_HOSTS["claude"].skill_path).is_file()
    assert not (tmp_path / AGENT_HOSTS["codex"].skill_path).exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_auto_target_is_failure_atomic_across_detected_hosts(tmp_path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / "AGENTS.md").write_bytes(b"\xff")

    assert _run("auto", tmp_path) == 2

    assert not (tmp_path / AGENT_HOSTS["claude"].skill_path).exists()
    assert (tmp_path / "AGENTS.md").read_bytes() == b"\xff"


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
def test_every_host_materializes_the_safe_edit_protocol(target, tmp_path) -> None:
    assert _run(target, tmp_path) == 0
    spec = AGENT_HOSTS[target]
    assert (tmp_path / spec.skill_path).read_text(encoding="utf-8") == agent_init._SKILL_MD


def test_no_unverified_runtime_is_declared() -> None:
    assert tuple(AGENT_HOSTS) == ("claude", "codex")


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
def test_host_projection_contains_complete_protocol(target, tmp_path) -> None:
    assert _run(target, tmp_path) == 0
    skill = (tmp_path / AGENT_HOSTS[target].skill_path).read_text(encoding="utf-8")
    for token in _SEMANTIC_TOKENS:
        assert token in skill


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
def test_instruction_surfaces_match_registry(target, tmp_path) -> None:
    assert _run(target, tmp_path) == 0
    spec = AGENT_HOSTS[target]
    assert all((tmp_path / path).is_file() for path in spec.instruction_paths)


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
def test_installed_hook_matches_registry_and_probe(target, tmp_path) -> None:
    assert _run_with_hook(target, tmp_path) == 0
    spec = AGENT_HOSTS[target]
    hook_path = tmp_path / spec.hook_path
    document = json.loads(hook_path.read_text(encoding="utf-8"))
    assert {
        "matcher": spec.hook_matcher,
        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
    } in document["hooks"]["PreToolUse"]
    assert enforcement_capability._hook_installed(
        hook_path, spec.hook_matcher, host=target
    )
    observed = enforcement_capability.probe(
        tmp_path,
        graph_available=True,
        git_available=True,
        hook_runtime_available=True,
        user_hooks_disabled=False,
    )
    assert observed[target]["mode"] == spec.declared_support
    assert observed[target]["candidate_bound"] is (
        spec.declared_support == "configured_enforcing"
    )


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
def test_missing_hook_never_claims_verified_enforcement(target, tmp_path) -> None:
    observed = enforcement_capability.probe(
        tmp_path,
        graph_available=True,
        git_available=True,
        hook_runtime_available=True,
        user_hooks_disabled=False,
    )
    assert observed[target]["mode"] == "advisory_only"
    assert observed[target]["candidate_bound"] is False
    if AGENT_HOSTS[target].declared_support == "best_effort":
        assert observed[target]["mode"] != "configured_enforcing"


def test_readme_support_rows_match_registry() -> None:
    body = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")
    declared = set(re.findall(r"<!-- agent-host:([a-z0-9_-]+) -->", body))
    assert declared == set(AGENT_HOSTS)
    for target, spec in AGENT_HOSTS.items():
        marker = f"<!-- agent-host:{target} -->"
        row = next(line for line in body[body.index(marker) + len(marker):].splitlines() if line)
        assert row.startswith("|")
        assert f"| `{spec.declared_support}` |" in row


def test_readme_and_managed_protocol_cover_every_decision_branch() -> None:
    readme = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")
    for decision in Decision:
        assert decision.value in readme
        assert decision.value in agent_init._PROTOCOL_BODY

    for required in (
        "inspect → reassess",
        "test → reassess",
        "revise → reassess",
        "accept-risk --apply",
        "new route or eligible override",
        "requires_confirmation=false",
    ):
        assert required in readme


def test_managed_protocol_cannot_turn_confirmation_into_bare_apply() -> None:
    normalized = " ".join(agent_init._PROTOCOL_BODY.split())
    assert "requires_confirmation=true" in normalized
    assert "pebra accept-risk --apply" in normalized
    assert "requires_confirmation=false" in normalized
    assert "pebra apply-candidate --assessment-id <returned-id>" in normalized


def test_managed_protocol_uses_reassessment_id_and_never_double_applies() -> None:
    normalized = " ".join(agent_init._PROTOCOL_BODY.split())
    assert "use its returned `reassessment_id` for Verify and Record" in normalized
    assert "do not run `pebra apply-candidate` or apply it again" in normalized


def test_managed_protocol_distinguishes_agent_labels_from_trusted_outcomes() -> None:
    normalized = " ".join(agent_init._PROTOCOL_BODY.split())
    assert "Agent-provided outcome labels remain agent-sourced" in normalized
    assert "`pebra finalize-outcome` is a trusted host-only path" in normalized
