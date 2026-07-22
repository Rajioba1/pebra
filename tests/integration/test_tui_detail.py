"""Tests for the assessment detail drill-in (Observatory TUI M4)."""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from dataclasses import replace
from threading import Event, Lock

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")

from textual.app import App  # noqa: E402
from textual.coordinate import Coordinate  # noqa: E402
from textual.screen import Screen  # noqa: E402
from textual.widgets import DataTable, Pretty  # noqa: E402

from pebra.app.observatory_query_controller import AssessmentNotFoundError  # noqa: E402
from pebra.core.exploration import ExplorationResult  # noqa: E402
from pebra.core.graph_snapshot import GraphSnapshot  # noqa: E402
from pebra.observatory_context import ObservatoryContext  # noqa: E402
from pebra.tui.app import ObservatoryApp  # noqa: E402
from pebra.tui.data import ObservatoryData, ObservatorySnapshot  # noqa: E402
from pebra.tui.exploration import RepositoryExplorationCoordinator  # noqa: E402
from pebra.tui.screens.detail import (  # noqa: E402
    GATES_UNAVAILABLE_NOTE,
    AssessmentDetailScreen,
    detail_sections,
    gate_history_note,
    header_line,
)
from pebra.tui.screens.observatory import ObservatoryScreen  # noqa: E402


def _graph_snapshot() -> GraphSnapshot:
    return GraphSnapshot(
        status="available",
        provider="test-graph",
        provider_version="1.2.3",
        index_version="7",
        repo_head="abc1234def",
        config_digest="cfg",
        graph_scope_digest="scope-123",
        sync_performed=True,
        fallback_reason=None,
    )


def _exploration_result() -> ExplorationResult:
    return ExplorationResult(
        status="available",
        snapshot=_graph_snapshot(),
        context="AuthService validates credentials before issuing a session.",
        dependent_files=("src/caller.py",),
        affected_tests=("tests/test_auth.py",),
        warnings=("bounded context",),
        fallback_reason=None,
        truncated=True,
    )


class _RecordingExplorer:
    def __init__(self, *, fail: bool = False, block: bool = False) -> None:
        self.fail = fail
        self.block = block
        self.prepare_calls: list[str] = []
        self.explore_calls: list[tuple[str, str, GraphSnapshot, tuple[str, ...]]] = []
        self.started = Event()
        self.release = Event()
        self.completed = Event()
        self.cancelled = Event()

    def prepare(self, repo_root: str) -> GraphSnapshot:
        self.prepare_calls.append(repo_root)
        self.started.set()
        if self.block:
            self.release.wait(timeout=5)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return _graph_snapshot()

    def explore(self, repo_root, query, *, snapshot, files=(), max_files=8, max_bytes=24_000):
        self.explore_calls.append((repo_root, query, snapshot, files))
        result = _exploration_result()
        self.completed.set()
        return result

    def cancel(self) -> None:
        self.cancelled.set()
        self.release.set()


async def _pause_until(predicate, pilot, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause()
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


def _detail() -> dict:
    return {
        "assessment_id": "asm_1",
        "content": {
            "repo_id": "r",
            "decision": "ask_human",
            "assessed_commit": "abc1234def",
            "assessed_at": "2026-07-20T12:34:56.123456+00:00",
            "request": {
                "task": "Fix login validation",
                "action_id": "edit-auth",
                "revision_envelope": {"expected_files": ["src/declared.py"]},
            },
            "model_guidance_packet": {
                "binding": {
                    "candidate": {
                        "algorithm": "sha256-normalized-content-v1",
                        "files": {"src/bound.py": "a" * 64},
                    }
                }
            },
            "scores": {
                "rau": -0.14,
                "benefit": 0.5,
                "symbol_scope_evidence": {"symbol_fanin": {"resolved_qualified_names": ["A::b"]}},
                "variance_breakdown": {"p_success": 0.4},
            },
        },
        "model_guidance_packet": {"decision": "ask_human"},
        "guardrails": [{"decision": "proceed"}],
        "outcomes": [],
    }


def test_sections_split_scores_from_evidence_and_carry_guidance() -> None:
    sections = dict(detail_sections(_detail()))
    assert "rau" in sections["Scores"] and "symbol_scope_evidence" not in sections["Scores"]
    assert set(sections["Evidence"]) == {"symbol_scope_evidence", "variance_breakdown"}
    assert sections["Guidance"] == {"decision": "ask_human"}
    assert sections["Guardrails"] == [{"decision": "proceed"}]


def test_detail_scores_keep_exact_decimals_with_human_unit_annotations() -> None:
    detail = _detail()
    detail["content"]["scores"] = {"rau": -0.14, "expected_loss": 0.1, "benefit": 0.82}
    scores = dict(detail_sections(detail))["Scores"]
    assert scores["expected_loss"] == "0.1 (10 pts)"
    assert scores["benefit"] == "0.82 (82/100)"


def test_detail_annotates_loss_points_and_benefit_score_beside_exact_decimals() -> None:
    """Milestone 0 forward spec for Milestone 2: detail keeps the exact decimals AND shows the human
    units — expected loss 0.1 (10 pts), benefit 0.82 (82/100). Exact decimals must remain."""
    detail = _detail()
    detail["content"]["scores"] = {"rau": -0.14, "expected_loss": 0.1, "benefit": 0.82}
    rendered = repr(dict(detail_sections(detail)))
    assert "10 pts" in rendered and "82/100" in rendered
    assert "0.1" in rendered and "0.82" in rendered  # round-trippable decimals remain


def test_detail_exposes_persisted_expected_utility_and_uncertainty() -> None:
    detail = _detail()
    detail["content"]["scores"] = {
        "expected_utility": 0.3868,
        "utility_sd": 0.06,
    }
    scores = dict(detail_sections(detail))["Scores"]
    assert scores["expected_utility"] == "0.3868"
    assert scores["utility_sd"] == "0.06"


def test_detail_score_decimals_are_round_trippable() -> None:
    value = 0.123456789
    detail = _detail()
    detail["content"]["scores"] = {
        "expected_loss": value,
        "benefit": value,
        "expected_utility": value,
        "utility_sd": value,
    }
    scores = dict(detail_sections(detail))["Scores"]
    assert scores["expected_loss"] == "0.123456789 (12.3456789 pts)"
    assert scores["benefit"] == "0.123456789 (12.3456789/100)"
    assert scores["expected_utility"] == "0.123456789"
    assert scores["utility_sd"] == "0.123456789"
    for key in ("expected_loss", "benefit", "expected_utility", "utility_sd"):
        assert float(scores[key].split()[0]) == value


def test_detail_marks_invalid_display_scores_unavailable() -> None:
    detail = _detail()
    detail["content"]["scores"] = {"expected_loss": None, "benefit": 1.01}
    scores = dict(detail_sections(detail))["Scores"]
    assert scores["expected_loss"] == "—"
    assert scores["benefit"] == "—"


def test_detail_lists_declared_and_bound_files_separately() -> None:
    sections = dict(detail_sections(_detail()))
    identity = sections["Assessment identity"]
    assert identity["Task"] == "Fix login validation"
    assert identity["Action ID"] == "edit-auth"
    assert identity["Assessed at"] == "2026-07-20T12:34:56.123456+00:00"
    assert identity["Assessed commit"] == "abc1234def"
    assert identity["Declared files"] == ["src/declared.py"]
    assert identity["Bound files"] == ["src/bound.py"]
    assert identity["Chosen targets"] == ["src/bound.py"]
    assert identity["Target provenance"] == "candidate binding"
    assert len(identity["Candidate fingerprint"]) == 64


def test_detail_labels_legacy_inference() -> None:
    detail = _detail()
    detail["content"]["request"] = {"task": "Legacy task"}
    detail["content"]["model_guidance_packet"] = {
        "binding": {"safe_scope": {"files": ["legacy/auth.py"]}}
    }

    identity = dict(detail_sections(detail))["Assessment identity"]

    assert identity["Chosen targets"] == ["legacy/auth.py"]
    assert identity["Target provenance"] == "legacy guidance inference"


def test_header_line_summarizes_identity() -> None:
    line = header_line(_detail())
    assert "asm_1" in line and "ask_human" in line and "abc1234" in line and "repo r" in line


def test_reject_detail_explains_reason_without_claiming_override_availability() -> None:
    detail = _detail()
    content = detail["content"]
    content["decision"] = "reject"
    content["decision_reason"] = "Expected loss exceeds the configured limit."
    content["gates_fired"] = [{
        "gate": 3,
        "name": "expected_loss_over_threshold",
        "expected_loss": 0.73,
        "threshold": 0.5,
    }]

    review = dict(detail_sections(detail))["Candidate decision"]

    assert review["Decision"] == "Reject candidate"
    assert review["Reason"] == "Expected loss exceeds the configured limit."
    assert review["Controlling gate"] == 3
    assert "Gate-eligible only" in review["Trusted risk override"]
    assert "availability is not claimed" in review["Trusted risk override"]
    assert "hash-covered" in gate_history_note(content)


@pytest.mark.parametrize(
    "field,value",
    (
        ("candidate_replay", None),
        ("candidate_binding", {"algorithm": "bad", "files": {}}),
        ("scores", {"rau": float("nan")}),
        ("decision_reason", ""),
    ),
)
def test_reject_detail_never_infers_override_availability_from_partial_history(
    field: str, value: object
) -> None:
    detail = _detail()
    content = detail["content"]
    content.update(
        {
            "decision": "reject",
            "decision_reason": "Recorded reason.",
            "gates_fired": [{"gate": 3, "name": "expected_loss_over_threshold"}],
            "candidate_replay": {"digest": "a" * 64},
            "candidate_binding": {
                "algorithm": "sha256-normalized-content-v1",
                "files": {"src/a.py": "b" * 64},
            },
            "scores": {"rau": -0.5, "expected_loss": 0.9, "benefit": 0.1},
        }
    )
    content[field] = value

    override = dict(detail_sections(detail))["Candidate decision"]["Trusted risk override"]

    assert "Conditionally available" not in override
    assert (
        "availability is not claimed" in override.lower()
        or override.startswith("Unavailable;")
    )


def test_gates_note_says_unavailable_and_is_not_reconstructed() -> None:
    assert "not available in history" in GATES_UNAVAILABLE_NOTE.lower()
    # The detail carries no gates_fired; the note is a constant, never derived from scores.
    assert "gate" in GATES_UNAVAILABLE_NOTE.lower()


def test_grouped_detail_lists_every_contained_assessment_id() -> None:
    sections = dict(detail_sections(_detail(), assessment_ids=("asm_3", "asm_2", "asm_1")))

    assert sections["Assessment identity"]["Contained assessment IDs"] == [
        "asm_3",
        "asm_2",
        "asm_1",
    ]


# --- integration ---


def _seed(tmp_path) -> str:
    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.ASK_HUMAN,
            requires_confirmation=True,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={"rau": -0.14, "benefit": 0.5, "expected_loss": 0.15},
            repo_id="r",
            repo_root="/x",
            model_guidance_packet={"decision": "ask_human"},
            assessed_commit="abc1234",
        ),
        {"task": "t"},
    )
    store.close()
    return db


def _ctx(db: str) -> ObservatoryContext:
    return ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True)


def test_row_select_opens_detail_then_escape_returns(tmp_path) -> None:
    db = _seed(tmp_path)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx(db))
        async with app.run_test() as pilot:
            app.query_one("#ledger", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, AssessmentDetailScreen)
            # persisted sections + the honest gates note are present
            assert app.screen.query("Pretty").results(Pretty)
            assert any(
                "hash-covered history" in str(s.render()).lower()
                for s in app.screen.query("#gates-note")
            )
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ObservatoryScreen)

    asyncio.run(scenario())


class _FakeData:
    repo_id = "r"

    def __init__(self, *, detail_error: Exception | None = None) -> None:
        self._detail_error = detail_error

    def refresh_snapshot(self) -> ObservatorySnapshot:
        return ObservatorySnapshot(
            overview={"total": 1},
            assessments=[
                {
                    "assessment_id": "asm_1",
                    "decision": "proceed",
                    "assessed_commit": "x",
                    "terminal_status": None,
                    "scores": {"rau": 0.1},
                }
            ],
            scores_series=[],
            chain={"valid": True},
        )

    def detail(self, assessment_id: str) -> dict:
        if self._detail_error is not None:
            raise self._detail_error
        return _detail()


class _Harness(App):
    def __init__(self, screen: Screen) -> None:
        super().__init__()
        self._screen = screen

    def get_default_screen(self) -> Screen:
        return self._screen


class _RecordingData(_FakeData):
    def __init__(self) -> None:
        super().__init__()
        self.detail_ids: list[str] = []

    def detail(self, assessment_id: str) -> dict:
        self.detail_ids.append(assessment_id)
        return _detail()


def test_not_found_detail_does_not_push_or_leak(tmp_path) -> None:
    async def scenario() -> None:
        screen = ObservatoryScreen(_FakeData(detail_error=AssessmentNotFoundError("asm_1")))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            app.query_one("#ledger", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            # guard held: still on the ledger, no detail screen pushed
            assert isinstance(app.screen, ObservatoryScreen)

    asyncio.run(scenario())


def test_row_selection_event_keeps_assessment_identity_across_refresh() -> None:
    async def scenario() -> None:
        data = _RecordingData()
        screen = ObservatoryScreen(data)
        app = _Harness(screen)
        async with app.run_test():
            table = app.query_one("#ledger", DataTable)
            old_row_key = table.coordinate_to_cell_key(Coordinate(0, 0)).row_key
            old_event = DataTable.RowSelected(table, 0, old_row_key)

            refreshed = data.refresh_snapshot()
            refreshed.assessments[0]["assessment_id"] = "asm_2"
            screen._apply_snapshot(refreshed)
            app.push_screen = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            screen.on_data_table_row_selected(old_event)

            assert data.detail_ids == ["asm_1"]

    asyncio.run(scenario())


def test_grouped_row_opens_latest_assessment_and_shows_all_ids() -> None:
    class _GroupedData(_RecordingData):
        def refresh_snapshot(self) -> ObservatorySnapshot:
            rows = []
            for assessment_id in ("asm_2", "asm_1"):
                rows.append(
                    {
                        "assessment_id": assessment_id,
                        "candidate_fingerprint": "a" * 64,
                        "decision": "proceed",
                        "assessed_commit": "abc1234",
                        "terminal_status": None,
                        "task": "Fix authentication",
                        "action_id": "edit-auth",
                        "target_files": ["src/auth.py"],
                        "scores": {"rau": 0.2, "expected_loss": 0.1, "benefit": 0.3},
                    }
                )
            return ObservatorySnapshot(
                overview={"total": 2},
                assessments=rows,
                scores_series=[],
                chain={"valid": True},
            )

    async def scenario() -> None:
        data = _GroupedData()
        app = _Harness(ObservatoryScreen(data))
        async with app.run_test() as pilot:
            await pilot.press("g")
            app.query_one("#ledger", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()

            assert isinstance(app.screen, AssessmentDetailScreen)
            assert data.detail_ids == ["asm_2"]
            assert app.screen.assessment_ids == ("asm_2", "asm_1")
            identity = dict(detail_sections(_detail(), assessment_ids=app.screen.assessment_ids))[
                "Assessment identity"
            ]
            assert identity["Contained assessment IDs"] == ["asm_2", "asm_1"]

    asyncio.run(scenario())


def test_detail_never_explores_on_mount() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer()
        screen = AssessmentDetailScreen(
            _detail(),
            repo_root="/repo",
            exploration=RepositoryExplorationCoordinator(lambda: explorer),
        )
        async with _Harness(screen).run_test() as pilot:
            await pilot.pause()
            assert explorer.prepare_calls == []
            assert explorer.explore_calls == []

    asyncio.run(scenario())


def test_detail_without_repository_context_reports_exploration_unavailable() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer()
        screen = AssessmentDetailScreen(
            _detail(),
            repo_root=None,
            exploration=RepositoryExplorationCoordinator(lambda: explorer),
        )
        async with _Harness(screen).run_test() as pilot:
            await pilot.pause()
            status = str(screen.query_one("#exploration-status").render())
            assert "unavailable" in status.lower()
            assert "repository context" in status.lower()
            assert "press x" not in status.lower()
            assert screen.query_one("#exploration-result").render().plain == ""
            assert "x" not in screen.app.active_bindings
            await pilot.press("x")
            await pilot.pause()
            assert explorer.prepare_calls == []
            assert explorer.explore_calls == []

    asyncio.run(scenario())


def test_detail_without_explorer_reports_exploration_unavailable() -> None:
    async def scenario() -> None:
        screen = AssessmentDetailScreen(_detail(), repo_root="/repo", exploration=None)
        async with _Harness(screen).run_test() as pilot:
            await pilot.pause()
            status = str(screen.query_one("#exploration-status").render())
            assert "unavailable" in status.lower()
            assert "explorer" in status.lower()
            assert "no repository context" not in status.lower()
            assert "x" not in screen.app.active_bindings

    asyncio.run(scenario())


def test_five_second_refresh_never_calls_explorer() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer()
        screen = ObservatoryScreen(
            _FakeData(),
            repo_root="/repo",
            exploration=RepositoryExplorationCoordinator(lambda: explorer),
        )
        async with _Harness(screen).run_test() as pilot:
            screen._tick()
            await _pause_until(lambda: not screen._refreshing, pilot)
            assert explorer.prepare_calls == []
            assert explorer.explore_calls == []

    asyncio.run(scenario())


def test_explicit_explore_is_single_flight() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer(block=True)
        screen = AssessmentDetailScreen(
            _detail(),
            repo_root="/repo",
            exploration=RepositoryExplorationCoordinator(lambda: explorer),
        )
        async with _Harness(screen).run_test() as pilot:
            await pilot.press("x", "x")
            await _pause_until(explorer.started.is_set, pilot)
            assert explorer.prepare_calls == ["/repo"]
            explorer.release.set()
            await _pause_until(lambda: not screen.exploring, pilot)
            assert len(explorer.explore_calls) == 1

    asyncio.run(scenario())


def test_explicit_explore_prepares_once_then_queries_snapshot() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer()
        screen = AssessmentDetailScreen(
            _detail(),
            repo_root="/repo",
            exploration=RepositoryExplorationCoordinator(lambda: explorer),
        )
        async with _Harness(screen).run_test() as pilot:
            await pilot.press("x")
            await _pause_until(lambda: not screen.exploring, pilot)

            assert explorer.prepare_calls == ["/repo"]
            assert explorer.explore_calls == [(
                "/repo",
                "Fix login validation",
                _graph_snapshot(),
                ("src/bound.py",),
            )]
            rendered = str(screen.query_one("#exploration-result").render())
            assert "AuthService" in rendered
            assert "src/caller.py" in rendered
            assert "tests/test_auth.py" in rendered
            assert "abc1234def" in rendered
            assert "scope-123" in rendered
            assert "1.2.3" in rendered
            assert "bounded context" in rendered
            assert "truncated" in rendered.lower()

    asyncio.run(scenario())


def test_late_explore_result_cannot_touch_popped_screen() -> None:
    delivered = Event()

    class _DeliveryObservedDetail(AssessmentDetailScreen):
        def _finish_explore_result(self, result: ExplorationResult) -> None:
            try:
                super()._finish_explore_result(result)
            finally:
                delivered.set()

    async def scenario() -> None:
        explorer = _RecordingExplorer(block=True)
        screen = _DeliveryObservedDetail(
            _detail(),
            repo_root="/repo",
            exploration=RepositoryExplorationCoordinator(lambda: explorer),
        )
        app = _Harness(Screen())
        async with app.run_test() as pilot:
            await app.push_screen(screen)
            await pilot.press("x")
            await _pause_until(explorer.started.is_set, pilot)
            status = screen.query_one("#exploration-status")
            before = str(status.render())
            await app.pop_screen()
            await _pause_until(lambda: not screen._can_update_children(), pilot)
            explorer.release.set()
            await _pause_until(delivered.is_set, pilot, attempts=500)
            assert explorer.completed.is_set()
            assert len(explorer.explore_calls) == 1
            assert len(screen.query("#exploration-status")) == 0
            assert str(status.render()) == before

    asyncio.run(scenario())


def test_explore_failure_preserves_assessment_detail() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer()
        screen = AssessmentDetailScreen(
            _detail(),
            repo_root="/repo",
            exploration=RepositoryExplorationCoordinator(lambda: explorer),
        )
        async with _Harness(screen).run_test() as pilot:
            await pilot.press("x")
            await _pause_until(lambda: not screen.exploring, pilot)
            good = str(screen.query_one("#exploration-result").render())
            pretty_count = len(list(screen.query(".section-body").results(Pretty)))

            explorer.fail = True
            await pilot.press("x")
            await _pause_until(lambda: not screen.exploring, pilot)

            assert str(screen.query_one("#exploration-result").render()) == good
            assert len(list(screen.query(".section-body").results(Pretty))) == pretty_count
            assert "failed" in str(screen.query_one("#exploration-status").render()).lower()

    asyncio.run(scenario())


def test_exploration_result_never_enters_store_or_scores(tmp_path) -> None:
    async def scenario() -> None:
        db = _seed(tmp_path)
        context = ObservatoryContext(db, "r", "/repo", False)
        before = deepcopy(ObservatoryData(context).detail("asm_1"))
        explorer = _RecordingExplorer()
        app = ObservatoryApp(context, explorer_factory=lambda: explorer)
        async with app.run_test() as pilot:
            app.query_one("#ledger", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, AssessmentDetailScreen)
            await pilot.press("x")
            await _pause_until(lambda: not screen.exploring, pilot)

        after = ObservatoryData(context).detail("asm_1")
        assert after == before
        assert "AuthService" not in repr(after["content"]["scores"])

    asyncio.run(scenario())


def test_each_accepted_exploration_uses_a_fresh_provider_session() -> None:
    async def scenario() -> None:
        first = _RecordingExplorer(fail=True)
        second = _RecordingExplorer()
        sessions = [first, second]
        coordinator = RepositoryExplorationCoordinator(lambda: sessions.pop(0))
        screen = AssessmentDetailScreen(
            _detail(), repo_root="/repo", exploration=coordinator
        )
        async with _Harness(screen).run_test() as pilot:
            await pilot.press("x")
            await _pause_until(lambda: not coordinator.busy, pilot)
            failed = str(screen.query_one("#exploration-status").render()).lower()
            assert "failed" in failed
            assert "assessment detail" in failed
            assert "last good" not in failed

            await pilot.press("x")
            await _pause_until(lambda: not coordinator.busy, pilot)

            assert first.prepare_calls == ["/repo"]
            assert second.prepare_calls == ["/repo"]
            assert len(second.explore_calls) == 1
            assert "AuthService" in str(screen.query_one("#exploration-result").render())

    asyncio.run(scenario())


def test_mismatched_result_snapshot_is_rejected_before_render() -> None:
    class _MismatchedExplorer(_RecordingExplorer):
        def explore(
            self,
            repo_root,
            query,
            *,
            snapshot,
            files=(),
            max_files=8,
            max_bytes=24_000,
        ):
            result = _exploration_result()
            return ExplorationResult(
                status=result.status,
                snapshot=GraphSnapshot(
                    status="available",
                    provider="other-graph",
                    provider_version="9.9.9",
                    index_version="99",
                    repo_head="different",
                    config_digest="different",
                    graph_scope_digest="different",
                    sync_performed=False,
                    fallback_reason=None,
                ),
                context="must not render",
                dependent_files=result.dependent_files,
                affected_tests=result.affected_tests,
                warnings=result.warnings,
                fallback_reason=None,
                truncated=False,
            )

    async def scenario() -> None:
        explorer = _MismatchedExplorer()
        coordinator = RepositoryExplorationCoordinator(lambda: explorer)
        screen = AssessmentDetailScreen(
            _detail(), repo_root="/repo", exploration=coordinator
        )
        async with _Harness(screen).run_test() as pilot:
            await pilot.press("x")
            await _pause_until(lambda: not coordinator.busy, pilot)

            assert "failed" in str(
                screen.query_one("#exploration-status").render()
            ).lower()
            assert screen.query_one("#exploration-result").render().plain == ""

    asyncio.run(scenario())


def test_single_flight_is_shared_across_detail_screens() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer(block=True)
        coordinator = RepositoryExplorationCoordinator(lambda: explorer)
        first = AssessmentDetailScreen(_detail(), repo_root="/repo", exploration=coordinator)
        app = _Harness(Screen())
        async with app.run_test() as pilot:
            await app.push_screen(first)
            await pilot.press("x")
            await _pause_until(explorer.started.is_set, pilot)
            await app.pop_screen()
            second = AssessmentDetailScreen(
                _detail(), repo_root="/repo", exploration=coordinator
            )
            await app.push_screen(second)

            await pilot.press("x")
            await pilot.pause()

            assert coordinator.busy is True
            assert explorer.prepare_calls == ["/repo"]
            assert "only one" in str(
                second.query_one("#exploration-status").render()
            ).lower()
            explorer.release.set()
            await _pause_until(lambda: not coordinator.busy, pilot)
            assert len(explorer.explore_calls) == 1

    asyncio.run(scenario())


def test_quit_cancels_active_exploration_session_promptly() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer(block=True)
        context = ObservatoryContext("unused.db", "r", "/repo", True)

        class _QuitHarness(ObservatoryApp):
            def __init__(self) -> None:
                super().__init__(context, explorer_factory=lambda: explorer)
                self._detail_screen = AssessmentDetailScreen(
                    _detail(), repo_root="/repo", exploration=self.exploration
                )

            def get_default_screen(self) -> Screen:
                return self._detail_screen

        app = _QuitHarness()
        async with app.run_test() as pilot:
            await pilot.press("x")
            await _pause_until(explorer.started.is_set, pilot)
            app.exit()

        assert explorer.cancelled.is_set()
        assert app.exploration.busy is False

    asyncio.run(asyncio.wait_for(scenario(), timeout=2))


def test_newer_exploration_cannot_be_overwritten_by_blocked_stale_delivery() -> None:
    class _NamedExplorer(_RecordingExplorer):
        def __init__(self, context: str) -> None:
            super().__init__()
            self.context = context

        def explore(
            self,
            repo_root,
            query,
            *,
            snapshot,
            files=(),
            max_files=8,
            max_bytes=24_000,
        ):
            self.explore_calls.append((repo_root, query, snapshot, files))
            self.completed.set()
            return replace(_exploration_result(), context=self.context)

    async def scenario() -> None:
        first = _NamedExplorer("first stale impact")
        second = _NamedExplorer("second current impact")
        sessions = [first, second]
        coordinator = RepositoryExplorationCoordinator(lambda: sessions.pop(0))
        screen = AssessmentDetailScreen(
            _detail(), repo_root="/repo", exploration=coordinator
        )
        app = _Harness(screen)
        first_delivery_blocked = Event()
        release_first_delivery = Event()
        first_delivery_done = Event()
        delivery_lock = Lock()
        delivery_count = 0

        async with app.run_test() as pilot:
            real_call_from_thread = app.call_from_thread

            def controlled_call_from_thread(callback, *args, **kwargs):
                nonlocal delivery_count
                with delivery_lock:
                    delivery_count += 1
                    this_delivery = delivery_count
                if this_delivery == 1:
                    first_delivery_blocked.set()
                    release_first_delivery.wait(timeout=5)
                try:
                    return real_call_from_thread(callback, *args, **kwargs)
                finally:
                    if this_delivery == 1:
                        first_delivery_done.set()

            app.call_from_thread = controlled_call_from_thread  # type: ignore[method-assign]
            await pilot.press("x")
            await _pause_until(first_delivery_blocked.is_set, pilot)
            assert coordinator.busy is True

            await pilot.press("x")
            await pilot.pause()
            assert second.prepare_calls == []
            assert "only one" in str(
                screen.query_one("#exploration-status").render()
            ).lower()

            release_first_delivery.set()
            await _pause_until(first_delivery_done.is_set, pilot)
            await _pause_until(lambda: not coordinator.busy, pilot)
            assert "first stale impact" in str(
                screen.query_one("#exploration-result").render()
            )

            await pilot.press("x")
            await _pause_until(
                lambda: "second current impact"
                in str(screen.query_one("#exploration-result").render()),
                pilot,
            )

            rendered = str(screen.query_one("#exploration-result").render())
            assert "second current impact" in rendered
            assert "first stale impact" not in rendered

    asyncio.run(scenario())


def test_unavailable_result_may_change_only_snapshot_status_and_reason() -> None:
    class _UnavailableExplorer(_RecordingExplorer):
        def explore(
            self,
            repo_root,
            query,
            *,
            snapshot,
            files=(),
            max_files=8,
            max_bytes=24_000,
        ):
            failed = replace(
                snapshot,
                status="error",
                fallback_reason="provider query failed safely",
            )
            return ExplorationResult(
                status="error",
                snapshot=failed,
                context="",
                dependent_files=(),
                affected_tests=(),
                warnings=(),
                fallback_reason="provider query failed safely",
                truncated=False,
            )

    async def scenario() -> None:
        explorer = _UnavailableExplorer()
        coordinator = RepositoryExplorationCoordinator(lambda: explorer)
        screen = AssessmentDetailScreen(
            _detail(), repo_root="/repo", exploration=coordinator
        )
        async with _Harness(screen).run_test() as pilot:
            await pilot.press("x")
            await _pause_until(lambda: not coordinator.busy, pilot)

            status = str(screen.query_one("#exploration-status").render())
            assert "provider query failed safely" in status
            assert screen.query_one("#exploration-result").render().plain == ""

    asyncio.run(scenario())


def test_cancel_invalidates_a_blocked_completed_delivery() -> None:
    async def scenario() -> None:
        explorer = _RecordingExplorer()
        coordinator = RepositoryExplorationCoordinator(lambda: explorer)
        screen = AssessmentDetailScreen(
            _detail(), repo_root="/repo", exploration=coordinator
        )
        app = _Harness(screen)
        delivery_blocked = Event()
        release_delivery = Event()
        delivery_done = Event()

        async with app.run_test() as pilot:
            real_call_from_thread = app.call_from_thread

            def controlled_call_from_thread(callback, *args, **kwargs):
                delivery_blocked.set()
                release_delivery.wait(timeout=5)
                try:
                    return real_call_from_thread(callback, *args, **kwargs)
                finally:
                    delivery_done.set()

            app.call_from_thread = controlled_call_from_thread  # type: ignore[method-assign]
            await pilot.press("x")
            await _pause_until(delivery_blocked.is_set, pilot)
            began = time.monotonic()
            coordinator.cancel(wait=True)
            assert time.monotonic() - began < 0.5
            release_delivery.set()
            await _pause_until(delivery_done.is_set, pilot)

            assert screen.query_one("#exploration-result").render().plain == ""

    asyncio.run(scenario())
