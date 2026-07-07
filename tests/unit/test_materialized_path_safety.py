"""The materialized CodeGraph diff's path gate now derives from the ONE shared canonical predicate.

Regression pin for cleanup #1: `_validate_materialized_paths` must reject the SAME escape classes as
`_paths.is_safe_relative` — including NTFS alternate-data-stream (`file.txt:stream`) paths, which the
previous private copy let through. It also normalizes a safe path to posix form.
"""

from __future__ import annotations

import pytest

from pebra.adapters.codegraph_materialized_diff import _validate_materialized_paths


def test_rejects_ads_and_escape_paths(tmp_path):
    root = str(tmp_path)
    for bad in ({"file.txt:stream"}, {"../x.cs"}, {"C:/abs.cs"}, {"D:evil.cs"}, {"/abs/x.cs"}):
        with pytest.raises(ValueError):
            _validate_materialized_paths(root, bad)


def test_normalizes_a_safe_path_to_posix(tmp_path):
    assert _validate_materialized_paths(str(tmp_path), {"a\\b.cs"}) == ("a/b.cs",)
