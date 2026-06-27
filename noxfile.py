"""Nox sessions (Architecture §4.4). `tests`, `lint`, and a `core-only` session that asserts the
engine imports with zero adapters present."""

from __future__ import annotations

import nox

DEV = [
    "pytest", "pytest-cov", "hypothesis", "syrupy", "jsonschema",
    "pyyaml", "radon", "bandit",
    "fastapi", "uvicorn", "jinja2", "httpx",  # Risk Observatory dashboard surface + test client
]


@nox.session
def tests(session: nox.Session) -> None:
    session.install("-e", ".", "--no-deps")
    session.install(*DEV)
    session.run("pytest", "-q")


@nox.session
def lint(session: nox.Session) -> None:
    session.install("-e", ".", "--no-deps")
    session.install("ruff", "import-linter")
    session.run("ruff", "check", "pebra")
    session.run("lint-imports")


@nox.session(name="mcp-smoke")
def mcp_smoke(session: nox.Session) -> None:
    """Install the mcp SDK and exercise the real serve() glue (the default `tests` env stays SDK-free
    to prove lazy import, so SDK API drift would otherwise slip past the gate)."""
    session.install("-e", ".", "--no-deps")
    session.install("pytest", "mcp>=1.0,<2")
    session.run("pytest", "tests/integration/test_mcp_server_serve.py", "-q")


@nox.session(name="core-only")
def core_only(session: nox.Session) -> None:
    """Install the base package and assert the engine imports with no adapters present."""
    session.install("-e", ".", "--no-deps")
    session.run(
        "python",
        "-c",
        "import pebra.core.decision_engine, pebra.core.assessment_builder, "
        "pebra.core.prediction_error, pebra.core.prediction_capture, "
        "pebra.core.outcome_labels, pebra.core.structural_features, "
        "pebra.core.apply_snapshot; print('core-only OK')",
    )
