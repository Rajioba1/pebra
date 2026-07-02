"""Nox sessions for tests, lint, benchmarks, e2e lanes, MCP smoke, and core-only import checks."""

from __future__ import annotations

import os
from pathlib import Path

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
    session.run("ruff", "check", "pebra", "benchmarks", "e2e")
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
    """Learning-loop replay benchmark (wiring proof, NOT an agent/product e2e — that lives in e2e/):
    deterministic scorecard JSON + fixture-safe comparison over the real promote/apply machinery."""
    session.install("-e", ".")
    session.install("pytest")
    session.run("pytest", "benchmarks/flow", "-q")


@nox.session(name="bench-flow-regen")
def bench_flow_regen(session: nox.Session) -> None:
    """Regenerate the learning-loop replay benchmark corpus + frozen scorecard/comparison artifacts."""
    session.install("-e", ".")
    session.run("python", "-m", "benchmarks.flow.corpus.export_fixture")
    session.run("python", "-m", "benchmarks.flow.replay")
    session.run("python", "-m", "benchmarks.flow.compare")


@nox.session(name="e2e")
def e2e(session: nox.Session) -> None:
    """Full current agent/product e2e. Runs the fast lane plus seeded-learning/dashboard metrics.

    This takes minutes on Windows because the seeded-learning lane runs 100+ real CLI cycles.
    """
    session.install("-e", ".")
    session.install("pytest")
    targets = [
        "e2e/utils/tests", "e2e/test_boundary_discipline.py", "e2e/smoke",
        "e2e/features/agent", "e2e/features/learning", "e2e/features/dashboard",
    ]
    graph_path = Path("e2e/features/graph")
    if os.environ.get("E2E_CODEGRAPH") == "1" and graph_path.exists():
        targets.append("e2e/features/graph")
    session.run("pytest", *targets, "-v")


@nox.session(name="e2e-fast")
def e2e_fast(session: nox.Session) -> None:
    """Fast e2e boundary/smoke lane: no seeded 100-cycle learning, no UI browser."""
    session.install("-e", ".")
    session.install("pytest")
    session.run(
        "pytest",
        "e2e/utils/tests",
        # pure attribution unit tests (parser/resolver/harness delta) — no dotnet, no external gate, so
        # the delta-only + implements-edge canaries run in fast CI, not only under gated e2e-external.
        "e2e/external/utils/tests",
        # agent-A/B deterministic runner plumbing (gate/client/tools/loop/preflight/evaluator) — all
        # driven by ScriptedClient/mock, no LLM, no dotnet, no gate: safe for fast CI.
        "e2e/experiments/agent_ab/tests",
        "e2e/test_boundary_discipline.py",
        "e2e/smoke",
        "e2e/features/agent",
        "-v",
    )


@nox.session(name="e2e-learning")
def e2e_learning(session: nox.Session) -> None:
    """Seeded-learning e2e lane: 100+ CLI cycles, promotion, future reassess, dashboard metrics."""
    session.install("-e", ".")
    session.install("pytest")
    session.run("pytest", "e2e/features/learning", "e2e/features/dashboard", "-v")


@nox.session(name="e2e-external")
def e2e_external(session: nox.Session) -> None:
    """Heavy gated real-repo proof: index a real external C# repo with CodeGraph and prove graph-backed
    risk via the graph-vs-no-graph DELETE delta (and later: real dotnet build outcome + agent A/B).

    Requires E2E_EXTERNAL=1 and E2E_TEMPLATE_BLUEPRINT_REPO=<local git checkout>. Clones the source into
    the gitignored e2e/out/ (never mutates it), runs pebra setup-graph. NOT for per-PR CI."""
    if os.environ.get("E2E_EXTERNAL") != "1":
        session.skip("Set E2E_EXTERNAL=1 (+ E2E_TEMPLATE_BLUEPRINT_REPO) to run the external lane.")
    session.install("-e", ".")
    session.install("pytest")
    session.run("pytest", "e2e/external", "-v", env={**os.environ})


@nox.session(name="e2e-ab")
def e2e_ab(session: nox.Session) -> None:
    """Blinded agent A/B efficacy experiment (real coding subagent per arm). FAIL-CLOSED and NOT for CI:
    requires E2E_AB_RUN=1 AND E2E_EXTERNAL=1 AND E2E_TEMPLATE_BLUEPRINT_REPO=<checkout> AND
    ANTHROPIC_API_KEY. Runs pre-flights (oracle labels + graph freshness) then paired trials. Never
    mutates the source (clones into gitignored e2e/out/)."""
    if (os.environ.get("E2E_AB_RUN") != "1" or os.environ.get("E2E_EXTERNAL") != "1"
            or not os.environ.get("ANTHROPIC_API_KEY")):
        session.skip("Set E2E_AB_RUN=1 E2E_EXTERNAL=1 E2E_TEMPLATE_BLUEPRINT_REPO=<path> "
                     "ANTHROPIC_API_KEY=<key> to run the agent A/B experiment.")
    session.install("-e", ".")
    session.install("pytest", "anthropic")
    mode = os.environ.get("E2E_AB_MODE", "pilot")
    run_id = os.environ.get("E2E_AB_RUN_ID", f"run_{mode}")
    session.run("python", "-m", "e2e.experiments.agent_ab.runners.orchestrator",
                "--run-id", run_id, "--mode", mode, env={**os.environ})


@nox.session(name="e2e-ui")
def e2e_ui(session: nox.Session) -> None:
    """Dashboard-visual e2e: launch the dashboard on a local port, drive it with Playwright, capture a
    screenshot for human review. Needs the Chromium browser binary. Set E2E_UI=1."""
    session.install("-e", ".[ui-e2e]")
    session.install("pytest", "pytest-playwright")
    session.run("playwright", "install", "chromium")
    session.run("pytest", "e2e/features/dashboard", "-v", env={**os.environ, "E2E_UI": "1"})


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
