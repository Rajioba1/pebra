"""Blinded repo instruction file for the subject agent.

Production hosts get their real PEBRA-branded skill via ``pebra agent-init``. The assay is blinded, so
it writes an arm-neutral instruction file into every clone and points the subject prompt at that path.
"""

from __future__ import annotations

from pathlib import Path

from e2e.experiments.agent_ab import forbidden, models

INSTRUCTION_REL_PATH = ".agent-instructions/edit_protocol.md"

_UNDERSTAND_PHASE = """2. **Understand.** For significant or unfamiliar work, reuse equivalent current
   repository context already supplied. If none is available, use ordinary repository search/read tools
   before the advisory. Do not repeat equivalent exploration."""

_SAFE_EDIT_PROTOCOL = f"""# Repository edit protocol

1. **Interpret.** Interpret the requested goal. Read-only investigation may stop after understanding;
   every repository file creation, edit, rename, or deletion continues through the advisory before writing.
{_UNDERSTAND_PHASE}
3. **Design.** Choose the smallest suitable route. Draft the intended patch before writing. Prefer
   candidate_edits with exact old_string and new_string values over hand-writing a unified diff.
4. **Assess.** Treat advisory_check as a required pre-write step for the exact candidate, not background text.
5. **Decide.** Follow the returned decision. inspect_first and test_first require those prerequisites and
   reassessment. revise_safer holds the current patch: preserve public behavior, choose a narrower route,
   consider an alias, wrapper, adapter, default, or deprecation bridge, and
   resubmit a safer or compatibility-preserving candidate. ask_human requires request_human_approval and
   exact-candidate reassessment after approval.
   reject holds the exact candidate, not the requested goal; do not write it. Choose a different candidate or
   route. If approval is denied or unavailable, stop.
6. **Apply.** Write only a permitted exact candidate. When advisory_check returns
   detail.candidate_patch_id, call apply_patch with that candidate_patch_id instead of reconstructing or
   expanding the patch.
7. **Verify.** Run the relevant build or tests after writing and resolve failures before stopping.
"""

_PLACEBO_PROTOCOL = f"""# Repository edit protocol

1. **Interpret.** Interpret the requested goal. Read-only investigation may stop after understanding;
   every repository file creation, edit, rename, or deletion continues through the advisory before writing.
{_UNDERSTAND_PHASE}
3. **Design.** Choose the smallest suitable route. Draft the intended patch before writing. Prefer
   candidate_edits with exact old_string and new_string values over hand-writing a unified diff.
4. **Assess.** Treat advisory_check as a required pre-write step for the exact candidate, not background text.
5. **Decide.** Consider the returned advisory while keeping the requested goal and existing public behavior.
6. **Apply.** Write only the intended candidate. When advisory_check returns detail.candidate_patch_id,
   call apply_patch with that candidate_patch_id instead of reconstructing or expanding the patch.
7. **Verify.** Run the relevant build or tests after writing and resolve failures before stopping.
"""

def protocol_for_arm(arm: str) -> str:
    return _SAFE_EDIT_PROTOCOL if arm in models.REAL_ADVISORY_ARMS else _PLACEBO_PROTOCOL


def install(repo_path: Path, arm: str) -> Path:
    text = protocol_for_arm(arm)
    assert_blinded(text)
    path = repo_path / INSTRUCTION_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def assert_blinded(text: str) -> None:
    leaked = forbidden.match_terms(text, forbidden.EXPERIMENT_LEAK_TERMS)
    if leaked:
        raise ValueError(f"subject protocol contains forbidden terms: {leaked}")
