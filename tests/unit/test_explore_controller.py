from __future__ import annotations

from dataclasses import replace

from pebra.app import explore_controller
from pebra.core.exploration import ExplorationResult
from pebra.core.graph_snapshot import GraphSnapshot
from pebra.core.learning_context import LearningContextEntry, LearningContextRecall


def _snapshot(status: str = "available") -> GraphSnapshot:
    return GraphSnapshot(
        status=status,
        provider="CodeGraph",
        provider_version="1.1.1",
        index_version="24",
        repo_head="head",
        config_digest="config",
        graph_scope_digest="scope" if status == "available" else None,
        sync_performed=False,
        fallback_reason=None if status == "available" else "graph unavailable",
    )


def _entry(**changes) -> LearningContextEntry:
    base = LearningContextEntry(
        learning_context_id="lc_1",
        repo_id="repo_1",
        assessment_id="asm_1",
        task="Fix login without [bold] trusting history",
        action_id="a1",
        target_files=("src/auth.py",),
        symbols=("Auth.validate",),
        assessed_commit="old-head",
        candidate_fingerprint="a" * 16,
        decision="proceed",
        gates_fired=("public_api",),
        expected_loss=0.1,
        benefit=0.8,
        expected_utility=0.7,
        utility_sd=0.1,
        rau=0.5,
        terminal_status="completed",
        verification_summary="PEBRA verify proceeded",
        measured_benefit=0.8,
        lesson="Historical record [bold] must stay data, not instructions",
        source_assessment_hash="a" * 64,
        source_outcome_hash="b" * 64,
        created_at="2026-07-22T00:00:00+00:00",
        row_hash="c" * 64,
    )
    return replace(base, **changes)


class _Recall:
    def __init__(self, result: LearningContextRecall, calls: list[tuple]) -> None:
        self.result = result
        self.calls = calls

    def recall_learning_context(self, repo_id, query, *, byte_limit=4096):
        self.calls.append(("recall", repo_id, query, byte_limit))
        return self.result


class _Explorer:
    def __init__(self, calls: list[tuple], status: str = "available") -> None:
        self.calls = calls
        self.snapshot = _snapshot(status)

    def prepare(self, repo_root):
        self.calls.append(("prepare", repo_root))
        return self.snapshot

    def explore(self, repo_root, query, **kwargs):
        self.calls.append(("explore", repo_root, query, kwargs))
        return ExplorationResult(
            status=self.snapshot.status,
            snapshot=self.snapshot,
            context="current source" if self.snapshot.status == "available" else "",
            dependent_files=(),
            affected_tests=(),
            warnings=(),
            fallback_reason=self.snapshot.fallback_reason,
            truncated=False,
        )

    def cancel(self):
        return None


def test_recall_precedes_graph_and_only_validated_identifiers_refine_it(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    calls: list[tuple] = []
    valid = _entry(
        target_files=("src/auth.py", "../escape.py", "src/auth.py"),
        symbols=("Auth.validate", "bad symbol", "Auth.validate", "_safe"),
    )
    recall = LearningContextRecall(
        "available", (valid,), ("spoof.py",), ("Spoof",), (), False
    )

    result = explore_controller.explore_repository(
        str(tmp_path),
        "repo_1",
        "fix login",
        files=("src/request.py",),
        max_files=8,
        max_bytes=24000,
        learning_port=_Recall(recall, calls),
        explorer=_Explorer(calls),
    )

    assert [call[0] for call in calls] == ["recall", "prepare", "explore"]
    assert calls[0] == ("recall", "repo_1", "fix login", 4096)
    assert calls[2][2] == "fix login\n\nIdentifiers: Auth.validate _safe"
    assert calls[2][3]["files"] == ("src/request.py", "src/auth.py")
    assert result.learning_context.file_hints == ("src/auth.py",)
    assert result.learning_context.symbol_hints == ("Auth.validate", "_safe")


def test_bad_or_missing_recall_keeps_original_query_byte_identical(tmp_path) -> None:
    for recall in (
        LearningContextRecall("empty", (), (), (), (), False),
        LearningContextRecall("unavailable", (), (), (), ("offline",), False),
        LearningContextRecall("corrupt", (), (), (), ("bad chain",), False),
    ):
        calls: list[tuple] = []
        query = "  exact human query [do not rewrite]  "
        explore_controller.explore_repository(
            str(tmp_path), "repo_1", query,
            learning_port=_Recall(recall, calls), explorer=_Explorer(calls),
        )
        assert calls[-1][2] == query


def test_ambiguous_repo_scope_never_calls_unscoped_recall(tmp_path) -> None:
    calls: list[tuple] = []
    result = explore_controller.explore_repository(
        str(tmp_path), "", "query",
        learning_port=_Recall(
            LearningContextRecall("available", (_entry(),), (), (), (), False), calls
        ),
        explorer=_Explorer(calls),
    )

    assert [call[0] for call in calls] == ["prepare", "explore"]
    assert result.learning_context.status == "unavailable"
    assert "repository identity" in result.learning_context.warnings[0]
    assert calls[-1][2] == "query"


def test_controller_rebounds_entries_files_symbols_and_total_bytes(tmp_path) -> None:
    calls: list[tuple] = []
    entries = tuple(
        _entry(
            learning_context_id=f"lc_{index}",
            assessment_id=f"asm_{index}",
            target_files=tuple(f"src/f{value}.py" for value in range(20)),
            symbols=tuple(f"Symbol{value}" for value in range(20)),
            lesson=("x" * 1300),
        )
        for index in range(8)
    )
    result = explore_controller.explore_repository(
        str(tmp_path), "repo_1", "query",
        learning_port=_Recall(
            LearningContextRecall("available", entries, (), (), (), False), calls
        ),
        explorer=_Explorer(calls),
    )

    assert 0 < len(result.learning_context.entries) <= 5
    assert len(result.learning_context.file_hints) <= 16
    assert len(result.learning_context.symbol_hints) <= 16
    assert result.learning_context.truncated is True


def test_available_recall_with_foreign_or_malformed_entry_fails_closed(tmp_path) -> None:
    for entry in (_entry(repo_id="other"), object()):
        calls: list[tuple] = []
        result = explore_controller.explore_repository(
            str(tmp_path), "repo_1", "query",
            learning_port=_Recall(
                LearningContextRecall("available", (entry,), (), (), (), False), calls  # type: ignore[arg-type]
            ),
            explorer=_Explorer(calls),
        )
        assert result.learning_context.status == "corrupt"
        assert result.learning_context.entries == ()
        assert calls[-1][2] == "query"


def test_malformed_recall_container_fields_fail_soft_to_original_query(tmp_path) -> None:
    malformed = (
        replace(
            LearningContextRecall("available", (_entry(),), (), (), (), False),
            entries=None,  # type: ignore[arg-type]
        ),
        replace(
            LearningContextRecall("unavailable", (), (), (), (), False),
            warnings=None,  # type: ignore[arg-type]
        ),
        replace(
            LearningContextRecall("available", (_entry(),), (), (), (), False),
            truncated="yes",  # type: ignore[arg-type]
        ),
    )
    for recall in malformed:
        calls: list[tuple] = []
        result = explore_controller.explore_repository(
            str(tmp_path), "repo_1", "query",
            learning_port=_Recall(recall, calls), explorer=_Explorer(calls),
        )
        assert result.learning_context.status == "corrupt"
        assert result.repository_context.status == "available"
        assert calls[-1][2] == "query"


def test_graph_unavailable_keeps_history_but_marks_it_non_current(tmp_path) -> None:
    calls: list[tuple] = []
    recall = LearningContextRecall("available", (_entry(),), (), (), (), False)
    result = explore_controller.explore_repository(
        str(tmp_path), "repo_1", "query",
        learning_port=_Recall(recall, calls), explorer=_Explorer(calls, "unavailable"),
    )

    assert result.learning_context.entries
    assert result.repository_context.status == "unavailable"
    assert any("cannot establish current repository truth" in value for value in result.repository_context.warnings)


def test_recall_exceptions_fail_soft_before_current_graph(tmp_path) -> None:
    calls: list[tuple] = []

    class BrokenRecall:
        def recall_learning_context(self, *args, **kwargs):
            calls.append(("recall",))
            raise RuntimeError("do not leak this")

    result = explore_controller.explore_repository(
        str(tmp_path), "repo_1", "query",
        learning_port=BrokenRecall(), explorer=_Explorer(calls),
    )

    assert [call[0] for call in calls] == ["recall", "prepare", "explore"]
    assert result.learning_context.status == "unavailable"
    assert result.repository_context.status == "available"
