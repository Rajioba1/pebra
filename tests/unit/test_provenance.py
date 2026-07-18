"""Unit tests for source provenance (which PEBRA is running)."""

from __future__ import annotations

from pebra import provenance


def test_version_is_a_version_string() -> None:
    v = provenance.version()
    assert v and v[0].isdigit()  # e.g. "0.1.0"


def test_is_editable_returns_bool() -> None:
    assert isinstance(provenance.is_editable(), bool)


def test_git_short_hash_is_none_or_hex() -> None:
    short = provenance.git_short_hash()
    assert short is None or (short and all(c in "0123456789abcdef" for c in short))


def test_provenance_line_has_version_and_install_mode() -> None:
    line = provenance.provenance_line()
    assert line.startswith("PEBRA ")
    assert ("editable" in line) or ("installed" in line)


def test_provenance_line_without_prefix_omits_the_pebra_word() -> None:
    line = provenance.provenance_line(prefix=False)
    assert not line.startswith("PEBRA")
    assert ("editable" in line) or ("installed" in line)


def test_is_editable_degrades_to_false_on_malformed_direct_url(monkeypatch) -> None:
    import importlib.metadata

    class _Dist:
        def __init__(self, text: str) -> None:
            self._text = text

        def read_text(self, name: str) -> str:
            return self._text

    # valid JSON that isn't an object, and a non-dict dir_info — must return False, never raise
    for raw in ("null", "[]", "42", '"x"', '{"dir_info": 42}', '{"url": "file:///x"}'):
        monkeypatch.setattr(importlib.metadata, "distribution", lambda _d, raw=raw: _Dist(raw))
        assert provenance.is_editable() is False


def test_editable_checkout_reports_editable_and_a_hash() -> None:
    # The test env installs pebra with `pip install -e .`, so provenance must reflect the checkout.
    if not provenance.is_editable():
        return  # a non-editable env (e.g. an installed-wheel smoke) legitimately reports "installed"
    line = provenance.provenance_line()
    assert "editable" in line
    assert provenance.git_short_hash() is not None  # a git checkout exposes its short hash
