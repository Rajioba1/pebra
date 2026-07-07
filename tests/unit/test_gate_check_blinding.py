from __future__ import annotations

import pytest

from e2e.experiments.agent_ab.metrics import blinding
from pebra.adapters import gate_check_adapter as gca


@pytest.mark.parametrize(
    "reason",
    [
        gca._deny_reason([r"C:\repo\src\Gamma.cs"], "abcdef1234567890"),
        gca._review_reason([r"C:\repo\src\Gamma.cs"], "abcdef1234567890"),
        gca._revise_reason([r"C:\repo\src\Gamma.cs"], "abcdef1234567890"),
    ],
)
def test_real_gate_reason_generators_do_not_leak_arm_identity(reason: str) -> None:
    leaked, matched = blinding.scan_text(reason)
    assert not leaked, matched
