from __future__ import annotations

import configparser
from pathlib import Path


def test_ports_must_not_import_tui() -> None:
    root = Path(__file__).resolve().parents[2]
    config = configparser.ConfigParser()
    config.read(root / ".importlinter", encoding="utf-8")

    contract = config["importlinter:contract:ports-no-tui"]
    assert contract["type"] == "forbidden"
    assert contract["source_modules"].split() == ["pebra.ports"]
    assert contract["forbidden_modules"].split() == ["pebra.tui"]


def test_recall_never_enters_scoring_or_authorization() -> None:
    root = Path(__file__).resolve().parents[2]
    config = configparser.ConfigParser()
    config.read(root / ".importlinter", encoding="utf-8")

    contract = config["importlinter:contract:recall-no-scoring"]
    assert contract["type"] == "forbidden"
    assert contract["source_modules"].split() == [
        "pebra.app.assess_controller",
        "pebra.core.assessment_builder",
        "pebra.core.decision_engine",
        "pebra.core.apply_snapshot",
        "pebra.adapters.gate_check_adapter",
        "pebra.app.candidate_apply_controller",
        "pebra.app.accept_risk_controller",
        "pebra.app.human_approval_controller",
        "pebra.adapters.sanction_store",
        "pebra.app.promotion_controller",
    ]
    assert contract["forbidden_modules"].split() == [
        "pebra.core.learning_context",
        "pebra.ports.learning_context_port",
        "pebra.app.explore_controller",
    ]


def test_core_exploration_remains_independent_of_historical_recall() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "pebra" / "core" / "exploration.py").read_text(encoding="utf-8")

    assert "learning_context" not in source
    assert "explore_controller" not in source
