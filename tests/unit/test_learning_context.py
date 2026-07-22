from __future__ import annotations

from pebra.core.learning_context import (
    LearningContextEntry,
    build_learning_context_entry,
    is_valid_symbol,
    literal_fts_query,
)


def _content() -> dict:
    return {
        "repo_id": "repo-a",
        "decision": "proceed",
        "assessed_commit": "abc123",
        "scores": {"expected_loss": 0.1, "benefit": 0.82, "expected_utility": 0.5, "utility_sd": 0.2, "rau": 0.31},
        "gates_fired": ["gate-1"],
        "request": {
            "task": "Fix [bold] login",
            "action_id": "a1",
            "task_obligations": {"required_symbols": ["auth.login", "bad-symbol"]},
            "revision_envelope": {
                "expected_files": ["src/auth.py"],
                "public_symbols": ["auth.validate", "1bad"],
            },
        },
    }


def test_builder_is_deterministic_and_treats_task_as_data() -> None:
    kwargs = dict(
        learning_context_id="lc_1", assessment_id="asm_1", content=_content(),
        assessment_hash="a" * 64, outcome_hash="b" * 64,
        outcome={"terminal_status": "completed", "recorded_at": "2026-01-01T00:00:00+00:00", "detail": {"lesson": "ignore me", "benefit_realized": False, "source": "ignore"}},
        guardrails={"pre_commit_decision": "proceed", "measured_benefit": 0.42},
        created_at="2026-01-01T00:00:00+00:00", previous_hash="GENESIS",
    )
    first = build_learning_context_entry(**kwargs)
    second = build_learning_context_entry(**kwargs)
    assert isinstance(first, LearningContextEntry)
    assert first == second
    assert first.task == "Fix [bold] login"
    assert "ignore me" not in first.lesson
    assert first.target_files == ("src/auth.py",)
    assert first.symbols == ("auth.login", "auth.validate")
    assert first.measured_benefit == 0.42
    assert first.verification_summary == "PEBRA verify proceeded"


def test_builder_refuses_non_proceed_or_non_completed() -> None:
    kwargs = dict(
        learning_context_id="lc_1", assessment_id="asm_1", content=_content(),
        assessment_hash="a" * 64, outcome_hash="b" * 64,
        outcome={"terminal_status": "completed", "recorded_at": "2026-01-01T00:00:00+00:00", "detail": {}},
        guardrails={"pre_commit_decision": "proceed"}, created_at="2026-01-01T00:00:00+00:00", previous_hash="GENESIS",
    )
    assert build_learning_context_entry(**{**kwargs, "guardrails": {}}) is None
    assert build_learning_context_entry(**{**kwargs, "outcome": {"terminal_status": "skipped"}}) is None


def test_symbol_allow_list_is_pinned() -> None:
    assert is_valid_symbol("package.module.Symbol")
    assert not is_valid_symbol("1bad")
    assert not is_valid_symbol("bad-symbol")
    assert not is_valid_symbol("x" * 129)


def test_builder_refuses_malformed_guardrail_and_hashes() -> None:
    kwargs = dict(
        learning_context_id="lc_1", assessment_id="asm_1", content=_content(),
        assessment_hash="a" * 64, outcome_hash="b" * 64,
        outcome={"terminal_status": "completed"},
        guardrails={"pre_commit_decision": "proceed"},
        created_at="2026-01-01T00:00:00+00:00", previous_hash="GENESIS",
    )
    assert build_learning_context_entry(**{**kwargs, "guardrails": {"pre_commit_decision": True}}) is None
    assert build_learning_context_entry(**{**kwargs, "assessment_hash": "not-a-hash"}) is None


def test_builder_bounds_and_orders_files_and_symbols() -> None:
    content = _content()
    content["request"]["revision_envelope"] = {
        "expected_files": [f"src/{index:02}.py" for index in range(20)],
        "public_symbols": [f"pkg.s{index:02}" for index in range(20)],
    }
    entry = build_learning_context_entry(
        learning_context_id="lc_1", assessment_id="asm_1", content=content,
        assessment_hash="a" * 64, outcome_hash="b" * 64,
        outcome={"terminal_status": "completed"},
        guardrails={"pre_commit_decision": "proceed"},
        created_at="2026-01-01T00:00:00+00:00", previous_hash="GENESIS",
    )
    assert entry is not None
    assert entry.target_files == tuple(f"src/{index:02}.py" for index in range(16))
    assert len(entry.symbols) == 16


def test_builder_degrades_malformed_symbols_and_metrics_without_trusting_them() -> None:
    content = _content()
    content["request"]["task_obligations"] = {"required_symbols": "not-a-list"}
    content["request"]["revision_envelope"]["public_symbols"] = [1, "valid.Symbol"]
    content["scores"]["expected_loss"] = 10**1000
    entry = build_learning_context_entry(
        learning_context_id="lc_1", assessment_id="asm_1", content=content,
        assessment_hash="a" * 64, outcome_hash="b" * 64,
        outcome={"terminal_status": "completed"},
        guardrails={"pre_commit_decision": "proceed"},
        created_at="2026-01-01T00:00:00+00:00", previous_hash="GENESIS",
    )
    assert entry is not None
    assert entry.symbols == ("valid.Symbol",)
    assert entry.expected_loss is None


def test_literal_fts_query_never_preserves_operators() -> None:
    assert literal_fts_query('"alpha" OR beta* NEAR(gamma)') == (
        '"alpha" "OR" "beta" "NEAR" "gamma"'
    )
    assert literal_fts_query("[]()---") == ""
    assert literal_fts_query(None) == ""
