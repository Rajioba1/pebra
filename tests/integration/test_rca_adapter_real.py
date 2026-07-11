"""REAL rust-code-analysis-cli validation of the RCA benefit adapter (Phase 5).

Gated on the binary being installed (``find_rca()``); skipped otherwise (every CI runner today, until a
build step is added). Runs the ACTUAL subprocess per language and asserts directional benefit deltas,
determinism, and — the key symmetry claim — that the pre-edit (``gather_benefit_evidence``, patch-driven)
and post-edit (``measure_delta``, plain-text) paths produce the SAME delta for the same edit, empirically
proving they share one measurement primitive rather than just asserting it in a comment.
"""

from __future__ import annotations

import difflib

import pytest

from pebra.adapters.rca_adapter import RustCodeAnalysisAdapter
from pebra.core.benefit_aggregation import aggregate_file_deltas
from pebra.core.rca_engine_paths import find_rca

requires_rca = pytest.mark.skipif(find_rca() is None, reason="rust-code-analysis-cli not installed")


@requires_rca
@pytest.mark.parametrize(
    "ext,simpler,complex_",
    [
        (".py", "def f(x):\n    return abs(x)\n",
         "def f(x):\n    if x > 0:\n        return x\n    return -x\n"),
        (".ts", "export function f(x: number) { return Math.abs(x) }\n",
         "export function f(x: number) { if (x > 0) return x; return -x }\n"),
        (".java", "class A { int f(int x) { return Math.abs(x); } }\n",
         "class A { int f(int x) { if (x > 0) return x; return -x; } }\n"),
        (".rs", "pub fn f(x: i32) -> i32 { x.abs() }\n",
         "pub fn f(x: i32) -> i32 { if x > 0 { x } else { -x } }\n"),
    ],
)
def test_real_binary_measures_directional_complexity(ext, simpler, complex_) -> None:
    a = RustCodeAnalysisAdapter()  # real subprocess runner
    up = a.measure_delta("f" + ext, simpler, complex_)  # simpler -> complex: complexity RISES
    assert up is not None and up[0] > 0
    down = a.measure_delta("f" + ext, complex_, simpler)  # complex -> simpler: complexity FALLS
    assert down is not None and down[0] < 0


@requires_rca
@pytest.mark.parametrize(
    "ext,before,after",
    [
        (".js", "function f(x) { return Math.abs(x) }\n",
         "function f(x) { if (x > 0) return x; return -x }\n"),
        (".jsx", "function C({x}) { return <span>{Math.abs(x)}</span> }\n",
         "function C({x}) { if (x > 0) return <span>{x}</span>; return <span>{-x}</span> }\n"),
        (".tsx", "export function f(x: number) { return Math.abs(x) }\n",
         "export function f(x: number) { if (x > 0) return x; return -x }\n"),
        (".c", "int f(int x) { return x < 0 ? -x : x; }\n",
         "int f(int x) { if (x > 0) { return x; } return -x; }\n"),
        (".cc", "int f(int x) { return x < 0 ? -x : x; }\n",
         "int f(int x) { if (x > 0) { return x; } return -x; }\n"),
        (".cpp", "int f(int x) { return x < 0 ? -x : x; }\n",
         "int f(int x) { if (x > 0) { return x; } return -x; }\n"),
        (".h", "int f(int x) { return x < 0 ? -x : x; }\n",
         "int f(int x) { if (x > 0) { return x; } return -x; }\n"),
        (".hpp", "int f(int x) { return x < 0 ? -x : x; }\n",
         "int f(int x) { if (x > 0) { return x; } return -x; }\n"),
    ],
)
def test_real_binary_supported_allowlist_emits_metrics(ext, before, after) -> None:
    assert RustCodeAnalysisAdapter().measure_delta("f" + ext, before, after) is not None


@requires_rca
def test_real_binary_is_deterministic() -> None:
    a = RustCodeAnalysisAdapter()
    before, after = "def f(x):\n    if x:\n        return 1\n    return 0\n", "def f(x):\n    return bool(x)\n"
    assert a.measure_delta("f.py", before, after) == a.measure_delta("f.py", before, after)


@requires_rca
def test_real_binary_unsupported_language_is_none() -> None:
    # Kotlin is in RCA's declared grammars but the built binary emits no output for .kt -> no credit.
    assert RustCodeAnalysisAdapter().measure_delta("f.kt", "fun f() = 1\n", "fun g() = 2\n") is None


@requires_rca
def test_pre_and_post_edit_paths_share_the_same_measurement(tmp_path) -> None:
    # THE symmetry proof: the pre-edit (patch-driven) and post-edit (plain-text) paths must produce the
    # SAME complexity + maintainability delta for the same edit, since both call the one RCA primitive.
    before = "def f(x):\n    if x > 0:\n        return x\n    return -x\n"
    after = "def f(x):\n    return abs(x)\n"
    (tmp_path / "m.py").write_text(before, encoding="utf-8")
    patch = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="m.py", tofile="m.py"))
    a = RustCodeAnalysisAdapter()
    pre = a.gather_benefit_evidence(str(tmp_path), ["m.py"], patch)
    post = a.measure_file_delta("m.py", before, after)
    assert pre.source_type == "measured" and post is not None
    assert pre.deltas == pytest.approx(aggregate_file_deltas({"m.py": post}))
