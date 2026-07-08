"""exposure_model.derive_exposure — pure [0,1] benefit VALUE-weight from trusted graph fan-in.

Exposure = how much a measured maintainability delta matters (proportional to the code's future-change
reach). Reuses the SAME graph-reach percentile the MODIFY-risk term uses, so it's exactly as trustworthy
as the risk injection; falls back to 0.0 for absent/untrusted graph (never a guess). BENEFIT-only.
"""

from __future__ import annotations

from pebra.core.exposure_model import derive_exposure
from pebra.core.models import FanInEvidence


def _trusted(**kw) -> FanInEvidence:
    base = dict(graph_freshness="fresh", resolution_method="location", graph_file_error_count=0)
    base.update(kw)
    return FanInEvidence(**base)


def test_none_or_untrusted_is_zero() -> None:
    assert derive_exposure(None) == 0.0
    assert derive_exposure(_trusted(graph_freshness="stale",
                                    symbol_fan_in_percentile=0.9, symbol_caller_count=3)) == 0.0
    assert derive_exposure(_trusted(graph_file_error_count=2,
                                    symbol_fan_in_percentile=0.9, symbol_caller_count=3)) == 0.0


def test_trusted_uses_fan_in_percentile() -> None:
    assert derive_exposure(_trusted(symbol_fan_in_percentile=0.8, symbol_caller_count=1)) == 0.8


def test_max_of_three_channels_is_monotonic() -> None:
    f = _trusted(symbol_fan_in_percentile=0.2, symbol_caller_count=1,
                 modify_transitive_impact_percentile=0.9, modify_transitive_impact_count=5)
    assert derive_exposure(f) == 0.9  # the strongest reach (transitive) dominates


def test_percentile_ignored_when_its_own_count_is_zero() -> None:
    # a percentile with zero supporting count must not contribute (matches the risk-side selection)
    assert derive_exposure(_trusted(symbol_fan_in_percentile=0.9, symbol_caller_count=0)) == 0.0


def test_cap_clamps() -> None:
    assert derive_exposure(_trusted(symbol_fan_in_percentile=0.8, symbol_caller_count=1), cap=0.5) == 0.5


def test_misconfigured_cap_above_one_is_clamped() -> None:
    assert derive_exposure(_trusted(symbol_fan_in_percentile=1.0, symbol_caller_count=1), cap=1.7) == 1.0
