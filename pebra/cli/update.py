"""`pebra update` / `pebra update-check` — explicit update status and upgrade guidance."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from builtins import input
from typing import Any

from pebra import provenance
from pebra import update_cache

PYPI_JSON_URL = "https://pypi.org/pypi/pebra/json"
_REFRESH_COMMANDS = frozenset({"version", "help", "tui", "dashboard"})


def disabled() -> bool:
    return update_cache.disabled()


def fetch_latest_version(*, timeout: int = 2) -> str | None:
    try:
        with urllib.request.urlopen(PYPI_JSON_URL, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None
    info = payload.get("info") if isinstance(payload, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) and version else None


def refresh(*, timeout: int = 2, now: float | None = None) -> update_cache.UpdateCache:
    current = provenance.version()
    latest = fetch_latest_version(timeout=timeout)
    entry = update_cache.UpdateCache(
        checked_at=time.time() if now is None else now,
        current_version=current,
        latest_version=latest,
    )
    update_cache.write_cache(entry)
    return entry


def refresh_if_allowed(command: str, args: Any) -> None:
    if command not in _REFRESH_COMMANDS or disabled() or provenance.is_editable():
        return
    if getattr(args, "as_json", False) or not sys.stdout.isatty():
        return
    if update_cache.is_stale(update_cache.read_cache()):
        refresh(timeout=2)


def notice_from_cache(*, require_tty: bool = True) -> str | None:
    if disabled() or provenance.is_editable():
        return None
    if require_tty and not sys.stdout.isatty():
        return None
    return update_cache.notice_from_entry(update_cache.read_cache(), provenance.version())


def _pipx_install() -> bool:
    parts = [part.lower() for part in sys.prefix.replace("\\", "/").split("/") if part]
    return (
        "pipx" in parts
        and "venvs" in parts
        and "pebra" in parts
    )


def install_command() -> list[str]:
    if _pipx_install():
        return ["pipx", "upgrade", "pebra"]
    return [sys.executable, "-m", "pip", "install", "--upgrade", "pebra"]


def _status_payload(entry: update_cache.UpdateCache | None) -> dict[str, Any]:
    current = provenance.version()
    command = install_command()
    latest = entry.latest_version if entry else None
    comparison = (
        update_cache.compare_stable_versions(latest, current)
        if latest is not None
        else None
    )
    return {
        "current_version": current,
        "latest_version": latest,
        "update_available": comparison == 1,
        "install_method": "pipx" if command[:2] == ["pipx", "upgrade"] else "pip",
        "command": command,
    }


def _print_status(entry: update_cache.UpdateCache | None) -> None:
    payload = _status_payload(entry)
    if payload["update_available"]:
        print(
            f"PEBRA {payload['latest_version']} is available; "
            f"you have {payload['current_version']}."
        )
        print("To update, run:")
        print("  " + " ".join(payload["command"]))
    elif payload["latest_version"]:
        print(f"PEBRA is up to date ({payload['current_version']}).")
    else:
        print("Could not check PyPI for the latest PEBRA version.")
        print("To update anyway, run:")
        print("  " + " ".join(payload["command"]))


def _entry_for_update(args: argparse.Namespace) -> update_cache.UpdateCache | None:
    timeout = 5 if args.check else 2
    return refresh(timeout=timeout)


def run_update(args: argparse.Namespace) -> int:
    if args.as_json and (args.run or args.yes):
        print("error: cannot combine --json with --run/--yes.", file=sys.stderr)
        return 2
    if disabled():
        if args.as_json:
            print(json.dumps({"disabled": True, "error": "update checks disabled"}))
        else:
            print("error: update checks disabled by PEBRA_NO_UPDATE_CHECK=1.", file=sys.stderr)
        return 2
    if provenance.is_editable():
        if args.as_json:
            print(json.dumps({"editable": True, "error": "editable checkout"}))
        else:
            print(
                "error: this is an editable checkout; update it with git pull instead.",
                file=sys.stderr,
            )
        return 2

    entry = _entry_for_update(args)
    payload = _status_payload(entry)
    if args.as_json:
        print(json.dumps(payload, sort_keys=True))
        return 0
    _print_status(entry)

    if not (args.run or args.yes):
        return 0
    if args.run and not args.yes and not sys.stdin.isatty():
        print("error: pebra update --run requires an interactive terminal.", file=sys.stderr)
        return 2
    if not args.yes and input("Type UPGRADE to update PEBRA: ") != "UPGRADE":
        print("aborted", file=sys.stderr)
        return 2
    return subprocess.run(payload["command"], check=False).returncode


def run_check(args: argparse.Namespace) -> int:
    if disabled():
        if args.as_json:
            print(json.dumps({"disabled": True, "error": "update checks disabled"}))
        else:
            print("error: update checks disabled by PEBRA_NO_UPDATE_CHECK=1.", file=sys.stderr)
        return 2
    if provenance.is_editable():
        if args.as_json:
            print(json.dumps({"editable": True, "error": "editable checkout"}))
        else:
            print("error: this is an editable checkout; update it with git pull instead.", file=sys.stderr)
        return 2
    cached = update_cache.read_cache()
    entry = refresh(timeout=5) if args.no_cache or update_cache.is_stale(cached) else cached
    payload = _status_payload(entry)
    if args.as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        _print_status(entry)
    return 0


def register(subparsers: Any) -> None:
    update = subparsers.add_parser(
        "update",
        help="Show or run the command to update PEBRA from PyPI.",
    )
    update.add_argument("--check", action="store_true", help="Force a fresh PyPI version check.")
    update.add_argument("--json", action="store_true", dest="as_json", help="Print machine-readable status.")
    update.add_argument("--run", action="store_true", help="Run the update command after confirmation.")
    update.add_argument("--yes", action="store_true", help="Run the update command without the UPGRADE prompt.")
    update.set_defaults(func=run_update)

    check = subparsers.add_parser(
        "update-check",
        help="Check whether a newer PEBRA release is available.",
    )
    check.add_argument("--no-cache", action="store_true", help="Force a fresh PyPI version check.")
    check.add_argument("--json", action="store_true", dest="as_json", help="Print machine-readable status.")
    check.set_defaults(func=run_check)
