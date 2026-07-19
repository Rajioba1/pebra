from __future__ import annotations

import pytest

from e2e.experiments.agent_ab.metrics import blinding
from pebra.adapters import gate_check_adapter as gca


@pytest.mark.parametrize(
    "reason",
    [
        gca._deny_reason([r"C:\repo\src\Gamma.cs"], "abcdef1234567890"),
        gca._exact_restrictive_reason("revise_safer", None),
        gca._exact_restrictive_reason("inspect_first", None),
        gca._exact_restrictive_reason("test_first", None),
        gca._exact_restrictive_reason("reject", None),
        gca._exact_restrictive_reason("ask_human", None, consult_only=True),
    ],
)
def test_real_gate_reason_generators_do_not_leak_arm_identity(reason: str) -> None:
    leaked, matched = blinding.scan_text(reason)
    assert not leaked, matched
