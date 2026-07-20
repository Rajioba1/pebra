"""AssessmentDetailScreen — the repo-scoped drill-in for one assessment (Observatory TUI M4).

A normal Screen (not a modal): pushed on row-select, Escape pops it. It renders the persisted detail —
scores, evidence, guidance, guardrails, outcomes — and is honest about the one thing that is NOT
persisted: `gates_fired` exists only in the live MCP payload, so history shows an explicit "unavailable"
note rather than reconstructing gate state from scores.
"""

from __future__ import annotations

from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Pretty, Static

from pebra.core.assessment_history import project_assessment_identity
from pebra.core.exploration import ExplorationResult
from pebra.ports.repository_explorer_port import RepositoryExplorer
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
        explorer: RepositoryExplorer | None = None,
    ) -> None:
        super().__init__()
        self._detail = detail
        self.assessment_ids = assessment_ids
        self._repo_root = repo_root
        self._explorer = explorer
        self._exploration_available = repo_root is not None and explorer is not None
        identity = project_assessment_identity(detail.get("content") or {})
        self._explore_query = identity.task or ""
        self._explore_files = tuple(identity.target_files)
        self._exploring = False

    @property
    def exploring(self) -> bool:
        return self._exploring

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
            yield Static(GATES_UNAVAILABLE_NOTE, id="gates-note")
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

    def on_unmount(self) -> None:
        # Textual cancels screen-owned worker delivery on pop; release only the UI-thread guard.
        # A blocking provider thread may still finish, but it has no mounted children to update.
        self._exploring = False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "explore":
            return self._exploration_available
        return super().check_action(action, parameters)

    def action_explore(self) -> bool:
        """Start one explicit prepare-then-query operation; repeated busy presses are ignored."""
        if self._exploring or not self._exploration_available:
            return False
        self._exploring = True
        self.query_one("#exploration-status", Static).update(
            "Preparing repository graph, then querying its accepted snapshot…"
        )
        self._explore_worker()
        return True

    @work(thread=True)
    def _explore_worker(self) -> None:
        repo_root = self._repo_root
        explorer = self._explorer
        if repo_root is None or explorer is None:
            self.app.call_from_thread(self._finish_explore_error)
            return
        try:
            snapshot = explorer.prepare(repo_root)
            result = explorer.explore(
                repo_root,
                self._explore_query,
                snapshot=snapshot,
                files=self._explore_files,
            )
        except Exception:  # provider/runtime boundary: fail visibly without damaging detail
            self.app.call_from_thread(self._finish_explore_error)
            return
        self.app.call_from_thread(self._finish_explore_result, result)

    def _can_update_children(self) -> bool:
        return self.is_mounted and not self._pruning

    def _finish_explore_error(self) -> None:
        self._exploring = False
        if not self._can_update_children():
            return
        self.query_one("#exploration-status", Static).update(
            "Exploration failed — existing assessment detail and last good impact are preserved."
        )

    def _finish_explore_result(self, result: ExplorationResult) -> None:
        self._exploring = False
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
