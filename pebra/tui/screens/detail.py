"""AssessmentDetailScreen — the repo-scoped drill-in for one assessment (Observatory TUI M4).

A normal Screen (not a modal): pushed on row-select, Escape pops it. It renders the persisted detail —
scores, evidence, guidance, guardrails, outcomes — and is honest about the one thing that is NOT
persisted. New assessments carry hash-covered gate and reason evidence; legacy history states that the
evidence is unavailable rather than reconstructing it from scores.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Pretty, Static

from pebra.core.assessment_history import project_assessment_identity
from pebra.core.human_review import controlling_gate, reject_override_eligible
from pebra.core.exploration import ExplorationResult
from pebra.tui.exploration import RepositoryExplorationCoordinator
from pebra.tui.widgets.ledger_table import (
    format_benefit_score,
    format_exact_score,
    format_loss_points,
    short_commit,
)

# Legacy rows may predate persisted gate evidence. History must say so and never re-derive gate state.
GATES_UNAVAILABLE_NOTE = (
    "Gates fired: not available in history (legacy assessment); never reconstructed from scores."
)

_EVIDENCE_KEYS = ("variance_breakdown", "symbol_scope_evidence", "candidate_verification")
_PROVENANCE_LABELS = {
    "candidate_bound": "candidate binding",
    "declared": "declared request",
    "legacy_guidance": "legacy guidance inference",
    "legacy_graph": "legacy graph inference",
    "unavailable": "unavailable",
}


def _detail_scores(scores: dict[str, Any]) -> dict[str, Any]:
    """Add human units beside the exact persisted decimals in the detail-only projection."""
    displayed = dict(scores)
    if "expected_loss" in scores:
        exact = format_exact_score(scores["expected_loss"])
        units = format_loss_points(scores["expected_loss"])
        displayed["expected_loss"] = f"{exact} ({units})" if exact != "—" and units != "—" else "—"
    if "benefit" in scores:
        exact = format_exact_score(scores["benefit"])
        units = format_benefit_score(scores["benefit"])
        displayed["benefit"] = f"{exact} ({units})" if exact != "—" and units != "—" else "—"
    if "expected_utility" in scores:
        displayed["expected_utility"] = format_exact_score(scores["expected_utility"])
    if "utility_sd" in scores:
        displayed["utility_sd"] = format_exact_score(scores["utility_sd"])
    return displayed


def header_line(detail: dict[str, Any]) -> str:
    content = detail.get("content") or {}
    decision = content.get("decision", "—")
    decision_label = "Reject candidate" if decision == "reject" else decision
    return (
        f"{detail.get('assessment_id', '—')}   ·   decision {decision_label}"
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
    plain_scores = _detail_scores({key: value for key, value in scores.items() if key not in evidence})
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
    decision = str(content.get("decision") or "—")
    gate = controlling_gate(content.get("gates_fired") or ())
    if decision == "reject" and reject_override_eligible(
        decision, content.get("gates_fired") or ()
    ):
        override = (
            "Gate-eligible only; override availability is not claimed from history. accept-risk "
            "revalidates replay, binding, finite scores, the recorded reason, ledger integrity, "
            "and current HEAD before prompting."
        )
    elif decision == "reject":
        override = "Unavailable; revise the candidate or follow the stated policy-resolution route."
    else:
        override = "Not applicable to this decision."
    decision_payload = {
        "Decision": "Reject candidate" if decision == "reject" else decision,
        "Reason": content.get("decision_reason") or "not recorded",
        "Controlling gate": gate if gate is not None else "unavailable",
        "Trusted risk override": override,
    }
    return [
        ("Assessment identity", identity_payload),
        ("Candidate decision", decision_payload),
        ("Scores", plain_scores),
        ("Evidence", evidence),
        ("Guidance", detail.get("model_guidance_packet")),
        ("Guardrails", detail.get("guardrails") or []),
        ("Outcomes", detail.get("outcomes") or []),
    ]


def gate_history_note(content: dict[str, Any]) -> str:
    gates = content.get("gates_fired")
    if not isinstance(gates, list):
        return GATES_UNAVAILABLE_NOTE
    gate = controlling_gate(gates)
    label = str(gate) if gate is not None else "unavailable"
    return f"Gates fired: recorded in hash-covered history; controlling gate {label}."


class AssessmentDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("x", "explore", "Explore impact"),
    ]

    def __init__(
        self,
        detail: dict[str, Any],
        *,
        assessment_ids: tuple[str, ...] = (),
        repo_root: str | None = None,
        exploration: RepositoryExplorationCoordinator | None = None,
    ) -> None:
        super().__init__()
        self._detail = detail
        self.assessment_ids = assessment_ids
        self._repo_root = repo_root
        self._exploration = exploration
        self._exploration_available = (
            repo_root is not None and exploration is not None and exploration.available
        )
        identity = project_assessment_identity(detail.get("content") or {})
        self._explore_query = identity.task or ""
        self._explore_files = tuple(identity.target_files)

    @property
    def exploring(self) -> bool:
        return self._exploration is not None and self._exploration.busy

    def compose(self) -> ComposeResult:
        if self._exploration_available:
            exploration_status = (
                "Press x to prepare the repository graph and explore this assessment's impact."
            )
        elif self._repo_root is None:
            exploration_status = (
                "Repository exploration unavailable — no repository context was supplied."
            )
        else:
            exploration_status = "Repository exploration unavailable — no explorer is configured."
        yield Header()
        with VerticalScroll(id="detail-body"):
            yield Static(header_line(self._detail), id="detail-header")
            yield Static(gate_history_note(self._detail.get("content") or {}), id="gates-note")
            for title, payload in detail_sections(
                self._detail, assessment_ids=self.assessment_ids
            ):
                yield Static(title, classes="section-title")
                yield Pretty(payload, classes="section-body")
            yield Static("Repository impact", classes="section-title")
            yield Static(
                exploration_status,
                id="exploration-status",
                markup=False,
            )
            yield Static("", id="exploration-result", markup=False)
        yield Footer()

    def action_back(self) -> None:
        self.dismiss()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "explore":
            return self._exploration_available
        return super().check_action(action, parameters)

    def action_explore(self) -> bool:
        """Start one explicit prepare-then-query operation; repeated busy presses are ignored."""
        if not self._exploration_available:
            return False
        repo_root = self._repo_root
        exploration = self._exploration
        if repo_root is None or exploration is None:
            return False
        app = self.app
        started = exploration.start(
            app,
            repo_root=repo_root,
            query=self._explore_query,
            files=self._explore_files,
            on_result=self._finish_explore_result,
            on_error=self._finish_explore_error,
        )
        if not started:
            self.query_one("#exploration-status", Static).update(
                "Only one repository exploration can run at a time. Press x to retry this assessment."
            )
            return False
        self.query_one("#exploration-status", Static).update(
            "Preparing repository graph, then querying its accepted snapshot…"
        )
        return True

    def _can_update_children(self) -> bool:
        return self.is_mounted and not self._pruning

    def _finish_explore_error(self) -> None:
        if not self._can_update_children():
            return
        result = self.query_one("#exploration-result", Static)
        preserved = "last good impact" if result.render().plain else "assessment detail"
        self.query_one("#exploration-status", Static).update(
            f"Exploration failed — existing {preserved} is preserved."
        )

    def _finish_explore_result(self, result: ExplorationResult) -> None:
        if not self._can_update_children():
            return
        status = self.query_one("#exploration-status", Static)
        if result.status != "available":
            reason = result.fallback_reason or "repository exploration unavailable"
            status.update(f"Exploration failed — {reason}")
            return
        status.update("Repository impact loaded from the accepted graph snapshot.")
        self.query_one("#exploration-result", Static).update(
            format_exploration_result(result)
        )


def format_exploration_result(result: ExplorationResult) -> str:
    """Render already-bounded provider-neutral context without markup interpretation."""
    snapshot = result.snapshot
    lines = [
        f"Snapshot HEAD: {snapshot.repo_head or 'unavailable'}",
        f"Graph scope: {snapshot.graph_scope_digest or 'unavailable'}",
        f"Freshness: {snapshot.status}",
        f"Provider: {snapshot.provider or 'unavailable'} {snapshot.provider_version or ''}".rstrip(),
        f"Prepared now: {'yes' if snapshot.sync_performed else 'no'}",
        f"Truncated: {'yes' if result.truncated else 'no'}",
    ]
    if result.context:
        lines.extend(("", "Context:", result.context))
    if result.dependent_files:
        lines.extend(("", "Dependent files:", *(f"- {path}" for path in result.dependent_files)))
    if result.affected_tests:
        lines.extend(("", "Affected tests:", *(f"- {path}" for path in result.affected_tests)))
    if result.warnings:
        lines.extend(("", "Warnings:", *(f"- {warning}" for warning in result.warnings)))
    return "\n".join(lines)
