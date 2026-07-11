from pathlib import Path

import pytest

from pebra.adapters.rca_adapter import RustCodeAnalysisAdapter


def _runner(path: Path):
    text = path.read_text(encoding="utf-8")
    # baseline complexity is the exposure weight; AFTER lowers each file by one branch but the
    # high-complexity file receives the larger maintainability improvement.
    if "heavy_before" in text:
        return {"metrics": {"cyclomatic": {"sum": 10}, "mi": {"mi_visual_studio": 40}}}
    if "heavy_after" in text:
        return {"metrics": {"cyclomatic": {"sum": 9}, "mi": {"mi_visual_studio": 50}}}
    if "light_before" in text:
        return {"metrics": {"cyclomatic": {"sum": 1}, "mi": {"mi_visual_studio": 80}}}
    return {"metrics": {"cyclomatic": {"sum": 0}, "mi": {"mi_visual_studio": 82}}}


def test_multifile_maintainability_uses_baseline_complexity_weight(tmp_path) -> None:
    (tmp_path / "heavy.py").write_text("heavy_before\n", encoding="utf-8")
    (tmp_path / "light.py").write_text("light_before\n", encoding="utf-8")
    patch = (
        "diff --git a/heavy.py b/heavy.py\n--- a/heavy.py\n+++ b/heavy.py\n"
        "@@ -1 +1 @@\n-heavy_before\n+heavy_after\n"
        "diff --git a/light.py b/light.py\n--- a/light.py\n+++ b/light.py\n"
        "@@ -1 +1 @@\n-light_before\n+light_after\n"
    )

    evidence = RustCodeAnalysisAdapter(runner=_runner).gather_benefit_evidence(
        str(tmp_path), ["heavy.py", "light.py"], patch
    )

    assert evidence.deltas["complexity_delta"] == -2
    expected = 10.0 * (1.0 + 10.0 / 20.0) + 2.0 * (1.0 + 1.0 / 11.0)
    assert evidence.deltas["maintainability_index_delta"] == pytest.approx(expected)
    assert evidence.file_deltas["heavy.py"]["exposure_weight"] == 10
    assert evidence.file_deltas["light.py"]["exposure_weight"] == 1
