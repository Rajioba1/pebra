"""Nox sessions (Architecture §4.4). `tests`, `lint`, and a `core-only` session that asserts the
engine imports with zero adapters present."""

from __future__ import annotations

import nox

DEV = [
    "pytest", "pytest-cov", "hypothesis", "syrupy", "jsonschema",
    "pyyaml", "radon", "bandit",
    "fastapi", "uvicorn", "jinja2", "httpx",  # Risk Observatory dashboard surface + test client
    "numpy", "scikit-learn>=1.2", "scipy",  # Oracle math references for tests/oracles.
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
    session.run("ruff", "check", "pebra", "benchmarks")
    session.run("lint-imports")


@nox.session(name="bench-math")
def bench_math(session: nox.Session) -> None:
    """Fast benchmark math/oracle tier: formula references + deterministic report shaping."""
    session.install("-e", ".", "--no-deps")
    session.install("pytest", "numpy", "scikit-learn>=1.2", "scipy")
    session.run("pytest", "benchmarks/math", "-q")


@nox.session(name="bench-math-regen")
def bench_math_regen(session: nox.Session) -> None:
    """Offline regeneration of the math fixture (the Tauri 'run the reference offline' analog): drives
    the REAL ignition loop, re-exports the CSV, then writes reference/PEBRA/comparison artifacts.
    Installs full pebra deps (the adapter stack runs here) + numpy/sklearn for the reference lane.
    Run manually after a schema/formula change, then COMMIT the regenerated data files."""
    session.install("-e", ".")  # full runtime deps — the ignition loop touches the adapter stack
    session.install("numpy", "scikit-learn>=1.2")
    session.run("python", "-m", "benchmarks.math.export_fixture")
    session.run("python", "-m", "benchmarks.math.run", "--write")


@nox.session(name="bench-flow")
def bench_flow(session: nox.Session) -> None:
    """Fast benchmark flow tier: deterministic scorecard JSON and fixture-safe comparison logic."""
    session.install("-e", ".", "--no-deps")
    session.install("pytest")
    session.run("pytest", "benchmarks/flow", "-q")


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
