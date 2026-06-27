"""Shared pytest configuration.

Registers the ``requires_codegraph`` marker and auto-skips those tests unless the ``codegraph`` CLI is
on PATH — so the real subprocess path (``codegraph sync``/``status --json``) is exercised when the binary
is installed, and cleanly skipped on dep-light machines without it.
"""

from __future__ import annotations

import shutil

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_codegraph: only run when the codegraph CLI is installed on PATH",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("requires_codegraph") and shutil.which("codegraph") is None:
        pytest.skip("codegraph CLI not installed on PATH")
