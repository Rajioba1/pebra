"""Blinded repo instruction file for the subject agent.

Production hosts get their real PEBRA-branded skill via ``pebra agent-init``. The assay is blinded, so
it writes an arm-neutral instruction file into every clone and points the subject prompt at that path.
"""

from __future__ import annotations

from pathlib import Path

from e2e.experiments.agent_ab import forbidden, models

INSTRUCTION_REL_PATH = ".agent-instructions/edit_protocol.md"

_SAFE_EDIT_PROTOCOL = """# Repository edit protocol

1. For a significant or unfamiliar edit, reuse equivalent current repository context already
   supplied. If none is available, use ordinary repository search/read tools before the advisory.
   Do not repeat equivalent exploration.
2. Treat the pre-edit advisory as a required step, not background text.
3. If the advisory says revise_safer, the current patch is not acceptable. Do not write it.
4. For revise_safer, preserve the existing public behavior and make the smallest sufficient change.
   For a public contract change, consider a compatibility alias, wrapper, adapter, default, or
   deprecation bridge; resubmit a safer or compatibility-preserving candidate to advisory_check, and
   write only after that candidate is no longer blocked. Prefer candidate_edits with exact old_string
   and new_string values over hand-writing a unified diff.
5. After writing, run the relevant build or tests before stopping.
   When advisory_check returns detail.candidate_patch_id, call apply_patch with that
   candidate_patch_id instead of reconstructing the patch or applying its files one at a time.
6. If the advisory says ask_human, do not write. Call request_human_approval with a concise reason.
   If approval is granted, resubmit the exact candidate to advisory_check and write only after the
   reassessment permits it. If approval is denied or unavailable, stop.
"""

_PLACEBO_PROTOCOL = """# Repository edit protocol

1. For a significant or unfamiliar edit, reuse equivalent current repository context already
   supplied. If none is available, use ordinary repository search/read tools before the advisory.
   Do not repeat equivalent exploration.
2. Treat the pre-edit advisory as a required step, not background text.
3. Draft the intended patch before writing it. Prefer candidate_edits with exact old_string and
   new_string values over hand-writing a unified diff.
4. Prefer the smallest sufficient change and keep existing public behavior unless the task asks for it.
5. When advisory_check returns detail.candidate_patch_id, call apply_patch with that
   candidate_patch_id instead of reconstructing the patch or applying its files one at a time.
6. After writing, run the relevant build or tests before stopping.
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
