"""Positive-control ("blast-radius") backend for the shared ``advisory_check`` tool — a CTXO-STYLE analog.

The assay's ACTIVE positive control: it hands the agent the FILES that depend on its target (from
``pebra dependents`` — PEBRA's own graph) as a pre-edit advisory WITH NO RISK VERDICT. The information
(which files reference what you're changing) is the intervention; there is NO PEBRA decision or score —
``recommended_decision`` stays None, ``risk_level`` "unknown". It answers the assay-sensitivity question
"can a realistic graph-guidance intervention help?". This is NOT literal CTXO (that's a deferred, gated
external comparator); it's a mechanism analog on PEBRA's graph.

Blinding: output is byte-shape-identical to sham/pebra (``detail={}``); only the advisory TEXT differs,
and it uses "files that reference/depend on your target" wording — never engine/experiment vocabulary.
Never imports pebra (reaches the graph only via the ``pebra dependents`` CLI through ``cli_harness``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab.tools import advisory_contract
from e2e.utils import cli_harness

_UNAVAILABLE = "The advisory tool is temporarily unavailable. Continue with normal code review and tests."
_MISSING = "Provide target_file, change_summary, and proposed_patch to get a pre-edit advisory."


def advise(payload: dict[str, Any], *, repo_root: Path | str, db: Path | str | None = None) -> dict[str, Any]:
    """Return the shared advisory shape with the dependent-file list in the advisory text (no verdict)."""
    if [k for k in advisory_contract.INPUT_SCHEMA["required"] if not payload.get(k)]:
        return advisory_contract.normalize_output({"advisory": _MISSING})
    try:
        result = cli_harness.dependents_result(payload["target_file"], repo_root=repo_root)
    except Exception:  # noqa: BLE001 - a tool failure returns an arm-neutral fallback, never crashes the run
        return advisory_contract.normalize_output({"advisory": _UNAVAILABLE})
    if not result.get("available"):
        return advisory_contract.normalize_output({"advisory": _UNAVAILABLE})
    files = result.get("dependent_files", [])
    files = list(files) if isinstance(files, list) else []
    if files:
        listed = "\n".join(f"  - {f}" for f in files)
        advisory = ("Before editing, review the code that depends on your target. These files reference "
                    f"what you are changing and could break:\n{listed}\n"
                    "Update or verify each of them, then run the build.")
    else:
        advisory = ("No other files appear to reference your target. Make the change, then run the build "
                    "to confirm nothing broke.")
    return advisory_contract.normalize_output({"advisory": advisory})
