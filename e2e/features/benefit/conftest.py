"""RCA benefit e2e lane — CLI-boundary only (NO ``import pebra``; boundary discipline).

Gated on the ``rust-code-analysis-cli`` binary being present, resolved via the shared boundary-safe
``rca_probe`` twin of production ``find_rca`` (PEBRA_RCA_BIN file/dir -> PATH), pinned to production by
``tests/unit/test_rca_probe_parity.py`` so this gate can't drift from what the CLI subprocess resolves.
``require_rca`` is a plain (non-autouse) fixture so the fail-safe test — which must pass with OR without
the binary — can opt out of it.
"""

from __future__ import annotations

import pytest

from e2e.utils import rca_probe


def rca_binary() -> str | None:
    """Locate rust-code-analysis-cli without importing pebra (delegates to the shared rca_probe twin)."""
    return rca_probe.find_rca()


@pytest.fixture
def require_rca() -> None:
    if rca_binary() is None:
        pytest.skip("rust-code-analysis-cli not installed (cargo install --git .../rust-code-analysis)")
