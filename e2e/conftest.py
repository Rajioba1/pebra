"""Shared e2e fixtures + env-gate flags. Function-scoped DBs/repos so each test is isolated."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from e2e.utils import learning_scenario
from e2e.utils import repo_factory

E2E_CODEGRAPH = os.environ.get("E2E_CODEGRAPH") == "1"
E2E_UI = os.environ.get("E2E_UI") == "1"
E2E_ORGANIC = os.environ.get("E2E_ORGANIC") == "1"

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def risky_repo(tmp_path) -> Path:
    return repo_factory.create_risky_repo(tmp_path / "repo")


@pytest.fixture
def e2e_db(tmp_path) -> Path:
    return tmp_path / "pebra.db"  # created on first `pebra assess`


@pytest.fixture
def request_json_path() -> Path:
    return _FIXTURES / "request_first_edit.json"


@pytest.fixture
def request_second_json_path() -> Path:
    return _FIXTURES / "request_second_edit.json"


@pytest.fixture
def out_dir() -> Path:
    d = Path(__file__).parent / "out"
    for sub in ("screenshots", "reports", "dbs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def seeded_learning_state(tmp_path_factory):
    base = tmp_path_factory.mktemp("seeded_learning")
    repo = repo_factory.create_risky_repo(base / "repo")
    db = repo / ".pebra" / "pebra.db"
    first_request = _FIXTURES / "request_first_edit.json"
    second_request = _FIXTURES / "request_second_edit.json"
    return learning_scenario.build_seeded_learning_state(repo, db, first_request, second_request)
