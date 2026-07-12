"""The SHARED advisory-tool contract — the load-bearing blinding invariant.

BOTH arms expose a tool with the IDENTICAL name, input schema, and output shape:

    name:   "advisory_check"   (never "pebra_assess" — the name must not reveal the arm)
    input:  {"target_file": str, "change_summary": str,
             "proposed_patch": str? | "candidate_edits": list?, "candidate_verification": dict?}
    output: {"recommended_decision": str|None, "risk_level": str, "advisory": str, "detail": dict}

Only the BACKING CONTENT differs:
  - control  -> advisory_check_sham.advise   (generic, recommended_decision=None, risk_level="unknown")
  - treatment-> advisory_check_real.advise   (PEBRA's real decision, via the pebra CLI)

If the two arms ever differ in tool NAME, input schema, or output KEYS, the subject could infer its
arm and the trial is unblinded. Keep this module the single source of that shape.
"""

from __future__ import annotations

from typing import Any

TOOL_NAME = "advisory_check"

TOOL_DESCRIPTION = (
    "Get a pre-edit advisory for a proposed change before you make significant edits. "
    "Provide the target file and a short summary of the change you intend to make."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_file": {"type": "string", "description": "Repo-relative path you intend to change."},
        "change_summary": {"type": "string", "description": "One-line summary of the intended change."},
        "proposed_patch": {"type": "string", "description": "Unified diff of the intended change."},
        "candidate_edits": {
            "type": "array",
            "description": (
                "Exact replacements for the intended change. Prefer this over hand-writing a unified "
                "diff; the host converts it to the assessed patch."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
        "candidate_verification": {
            "type": "object",
            "description": "Optional pre-edit verification result for a revised candidate patch.",
        },
    },
    "required": ["target_file", "change_summary"],
}

OUTPUT_KEYS: tuple[str, ...] = ("recommended_decision", "risk_level", "advisory", "detail")


def normalize_output(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce any backend's output to the exact shared shape (missing keys filled with safe defaults),
    so the two arms are byte-shape-identical to the subject."""
    return {
        "recommended_decision": raw.get("recommended_decision"),
        "risk_level": raw.get("risk_level", "unknown"),
        "advisory": raw.get("advisory", ""),
        "detail": raw.get("detail", {}),
    }
