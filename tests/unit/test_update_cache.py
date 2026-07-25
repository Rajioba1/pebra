"""Update-check cache: pure, offline, and outside repo state."""

from __future__ import annotations

import json
import time

from pebra import update_cache as uc


def test_cache_path_respects_pebra_cache_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PEBRA_CACHE_DIR", str(tmp_path))

    assert uc.cache_path() == tmp_path / "update-check.json"


def test_read_cache_missing_or_malformed_is_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PEBRA_CACHE_DIR", str(tmp_path))
    assert uc.read_cache() is None

    uc.cache_path().write_text("{bad json", encoding="utf-8")
    assert uc.read_cache() is None


def test_write_cache_round_trips_atomically(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PEBRA_CACHE_DIR", str(tmp_path))
    entry = uc.UpdateCache(checked_at=123.5, current_version="0.2.0", latest_version="0.3.0")

    uc.write_cache(entry)

    assert uc.read_cache() == entry
    assert json.loads(uc.cache_path().read_text(encoding="utf-8")) == {
        "checked_at": 123.5,
        "current_version": "0.2.0",
        "latest_version": "0.3.0",
    }


def test_stable_version_compare_ignores_prerelease_and_local_versions() -> None:
    assert uc.compare_stable_versions("1.10", "1.9") == 1
    assert uc.compare_stable_versions("1.0", "1.0.1") == -1
    assert uc.compare_stable_versions("1.0", "1.0") == 0
    assert uc.compare_stable_versions("1.0rc1", "1.0") is None
    assert uc.compare_stable_versions("1.0+local", "1.1") is None


def test_should_nag_requires_fresh_cache_and_newer_latest() -> None:
    now = time.time()
    fresh = uc.UpdateCache(checked_at=now, current_version="0.2.0", latest_version="0.3.0")
    same = uc.UpdateCache(checked_at=now, current_version="0.2.0", latest_version="0.2.0")
    stale = uc.UpdateCache(
        checked_at=now - uc.DEFAULT_TTL_SECONDS - 1,
        current_version="0.2.0",
        latest_version="0.3.0",
    )

    assert uc.should_nag(fresh, "0.2.0", now=now) is True
    assert uc.should_nag(same, "0.2.0", now=now) is False
    assert uc.should_nag(stale, "0.2.0", now=now) is False
