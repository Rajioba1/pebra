"""AssessmentDetailScreen — the repo-scoped drill-in for one assessment (Observatory TUI M4).

A normal Screen (not a modal): pushed on row-select, Escape pops it. It renders the persisted detail —
scores, evidence, guidance, guardrails, outcomes — and is honest about the one thing that is NOT
persisted: `gates_fired` exists only in the live MCP payload, so history shows an explicit "unavailable"
note rather than reconstructing gate state from scores.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Pretty, Static

from pebra.core.assessment_history import project_assessment_identity
from pebra.tui.widgets.ledger_table import short_commit

# gates_fired / high_risk_triggers are composed only for the live MCP response; db._canonical never
# persists them. History must SAY this, never re-derive gate state from the stored scores.
GATES_UNAVAILABLE_NOTE = (
    "Gates fired: not available in history — recorded only on the live assessment, not persisted."
)

_EVIDENCE_KEYS = ("variance_breakdown", "symbol_scope_evidence", "candidate_verification")
_PROVENANCE_LABELS = {
    "candidate_bound": "candidate binding",
    "declared": "declared request",
    "legacy_guidance": "legacy guidance inference",
    "legacy_graph": "legacy graph inference",
    "unavailable": "unavailable",
}


def header_line(detail: dict[str, Any]) -> str:
    content = detail.get("content") or {}
    return (
        f"{detail.get('assessment_id', '—')}   ·   decision {content.get('decision', '—')}"
        f"   ·   assessed commit {short_commit(content.get('assessed_commit'))}"
        f"   ·   repo {content.get('repo_id', '—')}"
    )


def detail_sections(
    detail: dict[str, Any], *, assessment_ids: tuple[str, ...] = ()
) -> list[tuple[str, Any]]:
    """Split the persisted detail into labelled sections. Evidence sub-dicts are pulled out of scores so
    the Scores section stays the scalar risk/benefit numbers."""
    content = detail.get("content") or {}
    scores = content.get("scores") or {}
    evidence = {key: scores[key] for key in _EVIDENCE_KEYS if key in scores}
    plain_scores = {key: value for key, value in scores.items() if key not in evidence}
    identity = project_assessment_identity(content)
    identity_payload = {
        "Task": identity.task or "—",
        "Action ID": identity.action_id or "—",
        "Assessed at": identity.assessed_at or "—",
        "Assessed commit": content.get("assessed_commit") or "—",
        "Candidate fingerprint": identity.candidate_fingerprint or "—",
        "Declared files": list(identity.declared_files),
        "Bound files": list(identity.bound_files),
        "Chosen targets": list(identity.target_files) or ["target unavailable"],
        "Target provenance": _PROVENANCE_LABELS[identity.target_provenance],
    }
    if len(assessment_ids) > 1:
        identity_payload["Contained assessment IDs"] = list(assessment_ids)
    return [
        ("Assessment identity", identity_payload),
        ("Scores", plain_scores),
        ("Evidence", evidence),
        ("Guidance", detail.get("model_guidance_packet")),
        ("Guardrails", detail.get("guardrails") or []),
        ("Outcomes", detail.get("outcomes") or []),
    ]


class AssessmentDetailScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, detail: dict[str, Any], *, assessment_ids: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._detail = detail
        self.assessment_ids = assessment_ids

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="detail-body"):
            yield Static(header_line(self._detail), id="detail-header")
            yield Static(GATES_UNAVAILABLE_NOTE, id="gates-note")
            for title, payload in detail_sections(
                self._detail, assessment_ids=self.assessment_ids
            ):
                yield Static(title, classes="section-title")
                yield Pretty(payload, classes="section-body")
        yield Footer()

    def action_back(self) -> None:
        self.dismiss()
