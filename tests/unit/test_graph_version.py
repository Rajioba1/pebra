"""M5c.5 setup-graph — pure version policy: exact install pin + accepted runtime range parser."""

from __future__ import annotations

import pytest

from pebra.core import graph_version as gv


def test_pins_are_consistent() -> None:
    # the exact default install version must itself be inside the accepted runtime range
    assert gv.in_accepted_range(gv.CODEGRAPH_DEFAULT_VERSION)


@pytest.mark.parametrize(
    "version, expected",
    [
        ("1.1.1", True),
        ("v1.1.1", True),     # leading 'v' tolerated
        ("1.1.0", True),      # lower bound inclusive
        ("1.1.99", True),
        ("1.1.1-rc.1", False),  # prereleases are not accepted by the lock policy
        ("1.2.0", False),     # upper bound exclusive
        ("1.0.9", False),
        ("2.0.0", False),
        ("0.9.0", False),
        ("not-a-version", False),
        ("", False),
    ],
)
def test_in_accepted_range_default(version: str, expected: bool) -> None:
    assert gv.in_accepted_range(version) is expected


def test_in_accepted_range_custom_range() -> None:
    assert gv.in_accepted_range("1.2.5", ">=1.2,<1.3") is True
    assert gv.in_accepted_range("1.3.0", ">=1.2,<1.3") is False


def test_malformed_range_raises() -> None:
    # a bad range string is a programming error (not user input) -> raise, don't silently pass
    with pytest.raises(ValueError):
        gv.in_accepted_range("1.1.1", ">=1.1")


@pytest.mark.parametrize(
    "version",
    [
        "1." + ("9" * 5_000),
        "1.1.1 || private-command",
        " 1.1.1 ",
        "1.1.1\x00",
        None,
    ],
)
def test_in_accepted_range_fails_soft_for_malformed_or_oversized_version(version) -> None:
    assert gv.in_accepted_range(version) is False
