"""rca_probe — boundary-safe stdlib twin of ``pebra.core.rca_engine_paths.find_rca``.

The e2e tree may not ``import pebra`` (boundary discipline), yet several e2e lanes must decide whether
the rust-code-analysis-cli benefit engine is present using the SAME lookup order the production CLI will
use — otherwise a skip predicate can drift from what the subprocess actually resolves. This is the single
shared copy; ``tests/unit/test_rca_probe_parity.py`` pins it to production ``find_rca`` so it cannot drift.

Lookup order (mirrors find_rca): PEBRA_RCA_BIN override (launcher FILE or bin DIR) -> PATH (shutil.which).
A misconfigured override falls through to PATH. Pure stdlib.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

_ENGINE = "rust-code-analysis-cli"


def _launcher_names() -> tuple[str, ...]:
    return (f"{_ENGINE}.exe",) if os.name == "nt" else (_ENGINE,)


def find_rca() -> str | None:
    """Locate the rust-code-analysis-cli binary (full path) or None. Pure lookup — never spawns."""
    override = os.environ.get("PEBRA_RCA_BIN", "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        if p.is_dir():
            for name in _launcher_names():
                cand = p / name
                if cand.is_file():
                    return str(cand)
        # misconfigured override (missing file / dir without launcher) -> fall through to PATH
    return shutil.which(_ENGINE)


def _cargo_source_revision(exe: str) -> str | None:
    binary = Path(exe).resolve()
    try:
        installs = json.loads(
            (binary.parent.parent / ".crates2.json").read_text(encoding="utf-8")
        ).get("installs", {})
    except (OSError, ValueError, AttributeError):
        return None
    for descriptor, details in installs.items():
        bins = details.get("bins", []) if isinstance(details, dict) else []
        if (
            isinstance(descriptor, str)
            and descriptor.startswith("rust-code-analysis-cli ")
            and binary.name.lower() in {str(name).lower() for name in bins}
        ):
            match = re.search(r"#([0-9a-f]{40})\)$", descriptor)
            return match.group(1) if match else None
    return None


def fingerprint(*, accepted_version: str, required_source_revision: str) -> dict[str, object]:
    """Policy-aware reproducibility identity for the exact RCA executable used by an assay."""
    exe = find_rca()
    if exe is None:
        return {
            "status": "absent", "validation_mode": None, "version": None,
            "sha256": None, "source_revision": None, "required_sha256": None,
        }
    expected_hash = os.environ.get("PEBRA_RCA_SHA256", "").strip().lower() or None
    digest: str | None = None
    try:
        hasher = hashlib.sha256()
        with Path(exe).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
        if proc.returncode != 0:
            raise OSError("version probe failed")
        version_text = proc.stdout.strip()
        prefix = f"{_ENGINE} "
        version = version_text[len(prefix):].strip() if version_text.startswith(prefix) else None
    except (OSError, subprocess.SubprocessError):
        return {
            "status": "probe_error", "validation_mode": None, "version": None,
            "sha256": digest, "source_revision": None,
            "required_sha256": expected_hash,
        }
    source_revision = _cargo_source_revision(exe)
    hash_ok = expected_hash is not None and digest == expected_hash
    source_ok = source_revision == required_source_revision
    # An explicit hash is authoritative. Falling back to Cargo provenance after a
    # mismatch would make the assay accept a binary that production rejects.
    provenance_ok = hash_ok if expected_hash is not None else source_ok
    accepted = version == accepted_version and provenance_ok
    return {
        "status": "accepted" if accepted else "rejected",
        "validation_mode": (
            "sha256" if hash_ok
            else ("cargo_revision" if expected_hash is None and source_ok else None)
        ),
        "version": version,
        "sha256": digest,
        "source_revision": source_revision,
        "required_sha256": expected_hash,
    }
