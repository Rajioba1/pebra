"""Shared pytest configuration.

Registers the ``requires_codegraph`` marker and auto-skips those tests unless the codegraph engine is
resolvable (via find_engine: PEBRA_CODEGRAPH_BIN, PATH, or PEBRA's managed install) — so the real
subprocess path is exercised after `pebra setup-graph` even when the managed binary isn't on PATH, and
cleanly skipped on dep-light machines without it.
"""

from __future__ import annotations

import pytest

from pebra.core.engine_paths import find_engine


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_codegraph: only run when the codegraph engine is installed (PATH or managed)",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("requires_codegraph") and find_engine() is None:
        pytest.skip("codegraph engine not found (checked PEBRA_CODEGRAPH_BIN, PATH, managed install)")
