"""Update-check cache for PEBRA's interactive surfaces.

Pure local state only: no network, no subprocess, no repo writes. The CLI update module owns PyPI I/O;
TUI/dashboard surfaces may read this cache passively.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

DEFAULT_TTL_SECONDS = 24 * 60 * 60
_STABLE_VERSION = re.compile(r"^\d+(?:\.\d+)*$")


@dataclass(frozen=True)
class UpdateCache:
    checked_at: float
    current_version: str
    latest_version: str | None


def disabled() -> bool:
    return os.environ.get("PEBRA_NO_UPDATE_CHECK") == "1"


def cache_root() -> Path:
    override = os.environ.get("PEBRA_CACHE_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "pebra" / "cache"
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "pebra"


def cache_path() -> Path:
    return cache_root() / "update-check.json"


def read_cache(path: Path | None = None) -> UpdateCache | None:
    target = path or cache_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    checked_at = raw.get("checked_at")
    current_version = raw.get("current_version")
    latest_version = raw.get("latest_version")
    if not isinstance(checked_at, int | float) or not isinstance(current_version, str):
        return None
    if latest_version is not None and not isinstance(latest_version, str):
        return None
    return UpdateCache(
        checked_at=float(checked_at),
        current_version=current_version,
        latest_version=latest_version,
    )


def write_cache(entry: UpdateCache, path: Path | None = None) -> None:
    target = path or cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    payload = {
        "checked_at": entry.checked_at,
        "current_version": entry.current_version,
        "latest_version": entry.latest_version,
    }
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def is_stale(entry: UpdateCache | None, *, now: float | None = None, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    if entry is None:
        return True
    current_time = time.time() if now is None else now
    return current_time - entry.checked_at >= ttl


def _parse_stable(version: str) -> tuple[int, ...] | None:
    if not _STABLE_VERSION.fullmatch(version):
        return None
    return tuple(int(part) for part in version.split("."))


def compare_stable_versions(left: str, right: str) -> int | None:
    left_parts = _parse_stable(left)
    right_parts = _parse_stable(right)
    if left_parts is None or right_parts is None:
        return None
    for left_part, right_part in zip_longest(left_parts, right_parts, fillvalue=0):
        if left_part > right_part:
            return 1
        if left_part < right_part:
            return -1
    return 0


def should_nag(entry: UpdateCache | None, current_version: str, *, now: float | None = None) -> bool:
    if entry is None or is_stale(entry, now=now) or not entry.latest_version:
        return False
    return compare_stable_versions(entry.latest_version, current_version) == 1


def notice_from_entry(entry: UpdateCache | None, current_version: str) -> str | None:
    if disabled():
        return None
    if not should_nag(entry, current_version):
        return None
    return (
        f"PEBRA {entry.latest_version} is available; you have {current_version}. "
        "Run: pebra update"
    )
