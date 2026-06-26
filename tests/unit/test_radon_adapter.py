"""Slice 4b — RadonAdapter benefit evidence (temp-apply measured hybrid).

Radon affects BENEFIT only. With a clean-applying proposed_patch it measures real before/after
complexity/maintainability deltas (source_type=measured); otherwise it yields a projected evidence
gap (no model-invented delta). Never mutates the real repo.
"""

from __future__ import annotations

import difflib

from pebra.adapters.radon_adapter import RadonAdapter
from pebra.core.benefit_model import METRIC_DIRECTION

_SIMPLE = "def f(x):\n    return x + 1\n"
_COMPLEX = (
    "def f(x):\n"
    "    if x > 0:\n"
    "        for i in range(x):\n"
    "            if i % 2 == 0:\n"
    "                x += i\n"
    "    return x + 1\n"
)


def _patch(rel: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=rel,
            tofile=rel,
        )
    )


def _write(tmp_path, rel: str, content: str) -> str:
    (tmp_path / rel).write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_no_python_files_returns_projected(tmp_path) -> None:
    ev = RadonAdapter().gather_benefit_evidence(str(tmp_path), ["README.md"], None)
    assert ev.source_type == "projected"
    assert ev.deltas == {}


def test_no_patch_returns_projected(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SIMPLE)
    ev = RadonAdapter().gather_benefit_evidence(root, ["m.py"], None)
    assert ev.source_type == "projected"
    assert ev.deltas == {}


def test_unappliable_patch_returns_projected(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SIMPLE)
    ev = RadonAdapter().gather_benefit_evidence(root, ["m.py"], "this is not a real diff\n")
    assert ev.source_type == "projected"


def test_real_repo_file_not_mutated(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SIMPLE)
    RadonAdapter().gather_benefit_evidence(root, ["m.py"], _patch("m.py", _SIMPLE, _COMPLEX))
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == _SIMPLE  # temp-only; repo untouched


def test_more_complex_after_gives_positive_complexity_delta(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SIMPLE)
    ev = RadonAdapter().gather_benefit_evidence(root, ["m.py"], _patch("m.py", _SIMPLE, _COMPLEX))
    assert ev.source_type == "measured"
    assert ev.deltas["complexity_delta"] > 0  # complexity went up (a negative benefit direction)


def test_simpler_after_gives_negative_complexity_delta(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _COMPLEX)
    ev = RadonAdapter().gather_benefit_evidence(root, ["m.py"], _patch("m.py", _COMPLEX, _SIMPLE))
    assert ev.source_type == "measured"
    assert ev.deltas["complexity_delta"] < 0  # complexity reduced (a positive benefit direction)


def test_delta_keys_are_in_metric_direction(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SIMPLE)
    after = "def f(x):\n    return x + 2\n"
    ev = RadonAdapter().gather_benefit_evidence(root, ["m.py"], _patch("m.py", _SIMPLE, after))
    assert ev.source_type == "measured"
    assert ev.deltas  # non-empty
    assert all(k in METRIC_DIRECTION for k in ev.deltas)  # core would silently ignore unknown keys


def test_only_unsafe_files_paths_return_projected(tmp_path) -> None:
    # a traversal path in `files` must be rejected before any read -> projected (no escape).
    root = _write(tmp_path, "m.py", _SIMPLE)
    (tmp_path.parent / "outside.py").write_text(_COMPLEX, encoding="utf-8")  # must never be read
    ev = RadonAdapter().gather_benefit_evidence(
        root, ["../outside.py"], _patch("m.py", _SIMPLE, _COMPLEX)
    )
    assert ev.source_type == "projected"


def test_unsafe_file_path_filtered_valid_one_processed(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SIMPLE)
    (tmp_path.parent / "outside.py").write_text(_COMPLEX, encoding="utf-8")
    ev = RadonAdapter().gather_benefit_evidence(
        root, ["../outside.py", "m.py"], _patch("m.py", _SIMPLE, _COMPLEX)
    )
    assert ev.source_type == "measured"
    assert ev.scope == "m.py"  # the traversal path was dropped before scope/read


def test_absolute_file_path_returns_projected(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SIMPLE)
    abs_path = str((tmp_path / "m.py").resolve())
    ev = RadonAdapter().gather_benefit_evidence(root, [abs_path], None)
    assert ev.source_type == "projected"


def test_patch_escaping_the_temp_dir_is_refused(tmp_path) -> None:
    # a patch with a ../ path must be refused (git work tree blocks escape) -> projected, never a
    # measured result built from a write outside the temp copy.
    root = _write(tmp_path, "m.py", _SIMPLE)
    evil = "--- a/../../escape.py\n+++ b/../../escape.py\n@@ -1 +1 @@\n-x\n+pwned\n"
    ev = RadonAdapter().gather_benefit_evidence(root, ["m.py"], evil)
    assert ev.source_type == "projected"


def test_future_change_exposure_passed_through(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SIMPLE)
    ev = RadonAdapter().gather_benefit_evidence(
        root, ["m.py"], _patch("m.py", _SIMPLE, _COMPLEX), future_change_exposure=0.4
    )
    assert ev.future_change_exposure == 0.4
