"""Validate PEBRA release archives and an installed wheel using only public package surfaces."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import importlib.metadata
import importlib.resources
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import fields
from pathlib import Path
from typing import AsyncContextManager, Mapping, Protocol, Sequence


_PACKAGE_ASSETS = (
    "pebra/dashboard/templates/index.html",
    "pebra/dashboard/static/app.js",
    "pebra/dashboard/static/style.css",
    "pebra/dashboard/static/vendor/uplot.iife.min.js",
    "pebra/dashboard/static/vendor/uplot.min.css",
    "pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    "pebra/tui/theme.tcss",
)
_SDIST_ROOT_FILES = (
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "RELEASING.md",
    "README.md",
    "pyproject.toml",
)
_TAG = re.compile(r"^v?(\d+\.\d+\.\d+(?:[a-zA-Z0-9.-]+)?)$")
_CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64})  ([^/\\]+)$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AGENT_HOST_FIELDS = (
    "skill_path",
    "instruction_paths",
    "hook_path",
    "hook_matcher",
    "declared_support",
)
_EXPECTED_AGENT_HOSTS: dict[str, dict[str, object]] = {
    "claude": {
        "skill_path": ".claude/skills/pebra-safe-edit/SKILL.md",
        "instruction_paths": (".claude/rules/pebra-safe-edit.md",),
        "hook_path": ".claude/settings.json",
        "hook_matcher": "Edit|Write|MultiEdit",
        "declared_support": "configured_enforcing",
    },
    "codex": {
        "skill_path": ".agents/skills/pebra-safe-edit/SKILL.md",
        "instruction_paths": ("AGENTS.md",),
        "hook_path": ".codex/hooks.json",
        "hook_matcher": "apply_patch",
        "declared_support": "best_effort",
    },
}
_AGENT_CHECK_KEYS = {
    "command",
    "target",
    "protocol_version",
    "gate_schema_version",
    "files",
    "hook",
    "declared_support",
    "effective_enforcement",
}
_EXPECTED_AGENT_SKILL_SHA256 = "5a9dcb6f560296ecfcba639a0782d8f59dcebef71544eedb8cdef78a8fe36d0b"
_CODEX_SENTINEL = "# Pre-existing Codex distribution-verifier sentinel\nPreserve this instruction.\n"
_MANAGED_BEGIN = "<!-- BEGIN pebra-safe-edit (managed by `pebra agent-init`) -->"
_MANAGED_END = "<!-- END pebra-safe-edit -->"
_AGENT_SEMANTIC_OBLIGATIONS = (
    "Understand — For a significant or unfamiliar edit",
    "Do not repeat equivalent exploration.",
    "it does not authorize an edit and is not trusted PEBRA scoring evidence.",
    "ordinary repository search/read tools",
    "Assess before every significant edit, rename, or delete",
    "Never treat either decision as permission to edit.",
    "exact assessed candidate;",
    "approval prompt yourself.",
    "pebra verify --assessment-id <id> --scope staged",
    "pebra record-outcome --assessment-id <id> --status completed",
)
_AGENT_SEMANTIC_RELATIONS = (
    ("Understand —", "**Assess (pre-edit).**"),
    ("**Assess (pre-edit).**", "**Revise when asked.**"),
    ("pebra accept-risk --apply", "apply_exact_candidate_then_verify"),
    ("apply_exact_candidate_then_verify", "pebra verify --assessment-id"),
    ("pebra verify --assessment-id", "pebra record-outcome --assessment-id"),
)
_CLAUDE_RULE_OBLIGATIONS = (
    "Assess before every significant edit, rename, or delete.",
    "Never apply a mismatched or incomplete candidate",
    "candidate hold or human review overrides an earlier advisory proceed",
    "Never create, claim, or answer your own human sanction.",
    "After application, verify and record the outcome.",
)


class DistributionVerificationError(RuntimeError):
    """A built or installed distribution does not satisfy the release contract."""


class _HeadlessTestApp(Protocol):
    def run_test(self) -> AsyncContextManager[object]: ...


def _missing_exact(members: set[str], required: Sequence[str]) -> list[str]:
    return [item for item in required if item not in members]


def verify_archives(wheel: Path, sdist: Path) -> None:
    """Require runtime assets and legal/release documents in the built archives."""
    with zipfile.ZipFile(wheel) as archive:
        wheel_info = archive.infolist()
    if any("\\" in member.orig_filename for member in wheel_info):
        raise DistributionVerificationError("wheel contains a non-portable backslash member")
    wheel_members = {member.filename for member in wheel_info}
    wheel_parts = wheel.name.removesuffix(".whl").split("-")
    if len(wheel_parts) < 2:
        raise DistributionVerificationError(f"invalid wheel filename: {wheel.name}")
    dist_info = f"{wheel_parts[0]}-{wheel_parts[1]}.dist-info"
    wheel_required = (
        *_PACKAGE_ASSETS,
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    )
    missing_wheel = _missing_exact(wheel_members, wheel_required)

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_info = archive.getmembers()
    if any("\\" in member.name for member in sdist_info):
        raise DistributionVerificationError("sdist contains a non-portable backslash member")
    sdist_members = {member.name for member in sdist_info if member.isfile()}
    sdist_root = sdist.name.removesuffix(".tar.gz")
    sdist_required = {
        f"{sdist_root}/{relative}" for relative in (*_SDIST_ROOT_FILES, *_PACKAGE_ASSETS)
    }
    missing_sdist = _missing_exact(sdist_members, sorted(sdist_required))

    problems = []
    if missing_wheel:
        problems.append("wheel missing: " + ", ".join(missing_wheel))
    if missing_sdist:
        problems.append("sdist missing: " + ", ".join(missing_sdist))
    if problems:
        raise DistributionVerificationError("; ".join(problems))


def release_version_from_tag(tag: str) -> str | None:
    match = _TAG.fullmatch(tag)
    return match.group(1) if match else None


def _run_cli(*args: str, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "pebra", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def _validate_agent_host_registry(registry: Mapping[str, object]) -> None:
    """Compare the installed registry with this verifier's independent release oracle."""
    if tuple(registry) != tuple(_EXPECTED_AGENT_HOSTS):
        raise DistributionVerificationError(
            "installed agent host registry mismatch: "
            f"expected targets {tuple(_EXPECTED_AGENT_HOSTS)}, got {tuple(registry)}"
        )
    for target, expected in _EXPECTED_AGENT_HOSTS.items():
        spec = registry[target]
        try:
            field_names = tuple(field.name for field in fields(spec))
            actual = {name: getattr(spec, name) for name in _AGENT_HOST_FIELDS}
        except (AttributeError, TypeError) as exc:
            raise DistributionVerificationError(
                f"installed agent host registry mismatch for {target}: malformed HostSpec"
            ) from exc
        if field_names != _AGENT_HOST_FIELDS or actual != expected:
            raise DistributionVerificationError(
                f"installed agent host registry mismatch for {target}: "
                f"expected {expected}, got {actual}"
            )


def _validate_agent_init_check(raw: str, *, target: str) -> dict[str, object]:
    """Validate installed check output against the independent host oracle."""
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DistributionVerificationError(
            f"installed agent-init check returned malformed JSON for {target}"
        ) from exc
    if not isinstance(payload, dict):
        raise DistributionVerificationError(
            f"installed agent-init check returned malformed payload for {target}"
        )
    if set(payload) != _AGENT_CHECK_KEYS:
        raise DistributionVerificationError(
            f"installed agent-init check schema mismatch for {target}: got {sorted(payload)}"
        )
    expected = _EXPECTED_AGENT_HOSTS.get(target)
    if expected is None:
        raise DistributionVerificationError(f"unknown installed agent target: {target}")
    if payload["command"] != "agent-init" or payload["target"] != target:
        raise DistributionVerificationError(
            f"installed agent-init check target mismatch for {target}"
        )
    if (
        type(payload["protocol_version"]) is not int
        or payload["protocol_version"] != 2
        or type(payload["gate_schema_version"]) is not int
        or payload["gate_schema_version"] != 1
    ):
        raise DistributionVerificationError(
            f"installed agent-init check protocol mismatch for {target}"
        )

    files_payload = payload["files"]
    expected_paths = [*expected["instruction_paths"], expected["skill_path"]]
    if not isinstance(files_payload, list) or any(
        not isinstance(item, dict) or set(item) != {"path", "state"}
        for item in files_payload
    ):
        raise DistributionVerificationError(
            f"installed agent-init check returned malformed payload for {target}"
        )
    actual_paths = [item["path"] for item in files_payload]
    if actual_paths != expected_paths or any(
        item["state"] != "current" for item in files_payload
    ):
        raise DistributionVerificationError(
            f"installed agent-init check file state mismatch for {target}"
        )

    hook = payload["hook"]
    if not isinstance(hook, dict) or set(hook) != {"path", "state"}:
        raise DistributionVerificationError(
            f"installed agent-init check returned malformed payload for {target}"
        )
    if hook["path"] != expected["hook_path"] or hook["state"] != "exact":
        raise DistributionVerificationError(
            f"installed agent-init check hook state mismatch for {target}"
        )
    if payload["declared_support"] != expected["declared_support"]:
        raise DistributionVerificationError(
            f"installed agent-init check support mismatch for {target}"
        )
    enforcement = payload["effective_enforcement"]
    if (
        not isinstance(enforcement, dict)
        or set(enforcement) != {"mode", "candidate_bound", "reasons"}
        or not isinstance(enforcement["mode"], str)
        or not isinstance(enforcement["candidate_bound"], bool)
        or not isinstance(enforcement["reasons"], list)
        or any(not isinstance(reason, str) for reason in enforcement["reasons"])
    ):
        raise DistributionVerificationError(
            f"installed agent-init check returned malformed payload for {target}"
        )
    if (
        enforcement["mode"] != "degraded_fail_open"
        or enforcement["candidate_bound"] is not False
        or "graph_unverified_read_only" not in enforcement["reasons"]
    ):
        raise DistributionVerificationError(
            f"installed agent-init check enforcement mismatch for {target}"
        )
    return payload


def _read_agent_artifact(path: Path, *, label: str) -> tuple[bytes, str]:
    try:
        raw = path.read_bytes()
        return raw, raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DistributionVerificationError(
            f"installed {label} is missing or unreadable: {path}"
        ) from exc


def _verify_agent_semantics(text: str, *, label: str) -> None:
    if any(obligation not in text for obligation in _AGENT_SEMANTIC_OBLIGATIONS):
        raise DistributionVerificationError(
            f"installed {label} is missing a semantic obligation"
        )
    for before, after in _AGENT_SEMANTIC_RELATIONS:
        if before not in text or after not in text or text.index(before) >= text.index(after):
            raise DistributionVerificationError(
                f"installed {label} has an invalid semantic obligation relation"
            )


def _verify_agent_init_artifacts(repo_root: Path, target: str) -> None:
    """Inspect installed files directly using verifier-owned expectations."""
    expected = _EXPECTED_AGENT_HOSTS[target]
    skill_path = repo_root / str(expected["skill_path"])
    skill_bytes, skill_text = _read_agent_artifact(skill_path, label=f"{target} skill")
    if hashlib.sha256(skill_bytes).hexdigest() != _EXPECTED_AGENT_SKILL_SHA256:
        raise DistributionVerificationError(
            f"installed {target} skill bytes differ from the release oracle"
        )
    _verify_agent_semantics(skill_text, label=f"{target} skill")

    instruction_path = repo_root / str(expected["instruction_paths"][0])
    _raw, instruction_text = _read_agent_artifact(
        instruction_path, label=f"{target} instructions"
    )
    if target == "claude":
        if any(item not in instruction_text for item in _CLAUDE_RULE_OBLIGATIONS):
            raise DistributionVerificationError(
                "installed Claude rule obligation is missing"
            )
        return

    if not instruction_text.startswith(_CODEX_SENTINEL):
        raise DistributionVerificationError(
            "installed Codex sentinel was not preserved outside the managed block"
        )
    if (
        instruction_text.count(_MANAGED_BEGIN) != 1
        or instruction_text.count(_MANAGED_END) != 1
    ):
        raise DistributionVerificationError("installed Codex managed block is malformed")
    start = instruction_text.index(_MANAGED_BEGIN)
    end = instruction_text.index(_MANAGED_END, start) + len(_MANAGED_END)
    managed = instruction_text[start:end]
    if _CODEX_SENTINEL.strip() in managed:
        raise DistributionVerificationError(
            "installed Codex sentinel was not preserved outside the managed block"
        )
    _verify_agent_semantics(managed, label="Codex managed block")


def _verify_tui_mount(app: _HeadlessTestApp) -> None:
    """Enter Textual's headless lifecycle so packaged styles are parsed and loaded."""

    async def mount() -> None:
        async with app.run_test():
            pass

    try:
        asyncio.run(mount())
    except Exception as exc:
        raise DistributionVerificationError("installed TUI failed to mount") from exc


def _verify_installed_cli(cwd: Path, *, installed_version: str) -> None:
    """Exercise both installed entry paths, including the exploration help contract."""
    version_result = _run_cli("--version", cwd=cwd)
    if (
        version_result.returncode != 0
        or not version_result.stdout.startswith(f"PEBRA {installed_version} ")
    ):
        raise DistributionVerificationError(
            f"installed CLI reported the wrong version: {version_result.stdout.strip()}"
        )
    module_commands = (
        ("--help",),
        ("help", "dashboard"),
        ("help", "tui"),
        ("help", "explore"),
    )
    for args in module_commands:
        result = _run_cli(*args, cwd=cwd)
        required = "usage: pebra explore" if args == ("help", "explore") else "pebra"
        if result.returncode != 0 or required not in result.stdout.lower():
            raise DistributionVerificationError(
                f"installed CLI failed for {' '.join(args)}: {result.stderr.strip()}"
            )

    launcher = shutil.which("pebra")
    if launcher is None:
        raise DistributionVerificationError("installed console script was not found on PATH")
    for args in (("--help",), ("help", "explore"), ("explore", "--help")):
        console = subprocess.run(
            [launcher, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )
        required = "usage: pebra explore" if "explore" in args else "pebra"
        if console.returncode != 0 or required not in console.stdout.lower():
            raise DistributionVerificationError(
                f"installed console script failed for {' '.join(args)}: "
                f"{console.stderr.strip()}"
            )


def verify_installed() -> None:
    """Check the installed wheel without importing PEBRA from its source checkout."""
    dashboard = importlib.resources.files("pebra.dashboard")
    for relative in (
        "templates/index.html",
        "static/app.js",
        "static/style.css",
        "static/vendor/uplot.iife.min.js",
        "static/vendor/uplot.min.css",
        "static/vendor/uplot.LICENSE.txt",
    ):
        if not dashboard.joinpath(relative).is_file():
            raise DistributionVerificationError(f"installed package missing {relative}")

    if not importlib.resources.files("pebra.tui").joinpath("theme.tcss").is_file():
        raise DistributionVerificationError("installed package missing pebra/tui/theme.tcss")

    from pebra.core.agent_hosts import AGENT_HOSTS
    from pebra.core.exploration import ExplorationResult
    from pebra.core.graph_snapshot import GraphSnapshot
    from pebra.observatory_context import ObservatoryContext
    from pebra.ports.repository_explorer_port import RepositoryExplorer
    from pebra.tui.app import ObservatoryApp

    _validate_agent_host_registry(AGENT_HOSTS)

    before_parser = set(sys.modules)
    eager_codegraph = sorted(
        name for name in before_parser if name.startswith("pebra.adapters.codegraph")
    )
    if eager_codegraph:
        raise DistributionVerificationError(
            "installed package loaded CodeGraph adapters before parser import: "
            + ", ".join(eager_codegraph)
        )
    from pebra.cli.main import build_parser

    parser = build_parser()
    eager_codegraph = sorted(
        name for name in sys.modules if name.startswith("pebra.adapters.codegraph")
    )
    if eager_codegraph:
        raise DistributionVerificationError(
            "installed package loaded CodeGraph adapters after parser construction: "
            + ", ".join(eager_codegraph)
        )
    if "explore" not in parser._subparsers._group_actions[0].choices:
        raise DistributionVerificationError("installed parser is missing pebra explore")
    snapshot = GraphSnapshot(
        status="unavailable",
        provider=None,
        provider_version=None,
        index_version=None,
        repo_head=None,
        config_digest=None,
        graph_scope_digest=None,
        sync_performed=False,
        fallback_reason="installed contract smoke",
    )
    result = ExplorationResult(
        status="unavailable",
        snapshot=snapshot,
        context="",
        dependent_files=(),
        affected_tests=(),
        warnings=(),
        fallback_reason="installed contract smoke",
        truncated=False,
    )
    if result.snapshot is not snapshot or not callable(RepositoryExplorer.explore):
        raise DistributionVerificationError("installed repository exploration contract is malformed")

    app = ObservatoryApp(ObservatoryContext(
        db_path="installed-wheel-smoke.db",
        repo_id="installed-wheel-smoke",
        repo_root=None,
        read_only=True,
    ))
    if app.observatory_context.repo_id != "installed-wheel-smoke":
        raise DistributionVerificationError("installed TUI app construction failed")
    _verify_tui_mount(app)

    metadata_files = {str(path).replace("\\", "/") for path in importlib.metadata.files("pebra") or ()}
    for suffix in ("licenses/LICENSE", "licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt"):
        if not any(path.endswith(suffix) for path in metadata_files):
            raise DistributionVerificationError(f"installed metadata missing {suffix}")

    with tempfile.TemporaryDirectory(prefix="pebra-wheel-smoke-") as raw:
        cwd = Path(raw)
        installed_version = importlib.metadata.version("pebra")
        _verify_installed_cli(cwd, installed_version=installed_version)
        for target in _EXPECTED_AGENT_HOSTS:
            repo_root = cwd / f"agent-{target}"
            if target == "codex":
                repo_root.mkdir(parents=True)
                (repo_root / "AGENTS.md").write_text(
                    _CODEX_SENTINEL, encoding="utf-8", newline=""
                )
            installed = _run_cli(
                "agent-init", "--target", target, "--repo-root", str(repo_root), "--with-hook",
                cwd=cwd,
            )
            if installed.returncode != 0:
                raise DistributionVerificationError(
                    f"installed agent-init failed for {target}: {installed.stderr.strip()}"
                )
            _verify_agent_init_artifacts(repo_root, target)
            before = {
                path.relative_to(repo_root).as_posix(): path.read_bytes()
                for path in repo_root.rglob("*")
                if path.is_file()
            }
            checked = _run_cli(
                "agent-init", "--target", target, "--repo-root", str(repo_root),
                "--check", "--json", cwd=cwd,
            )
            if checked.returncode != 0:
                raise DistributionVerificationError(
                    f"installed agent-init check failed for {target}: {checked.stderr.strip()}"
                )
            _validate_agent_init_check(checked.stdout, target=target)
            after = {
                path.relative_to(repo_root).as_posix(): path.read_bytes()
                for path in repo_root.rglob("*")
                if path.is_file()
            }
            if after != before:
                raise DistributionVerificationError(
                    "installed agent-init check mutated repository state"
                )

    old_path = os.environ.get("PATH")
    old_override = os.environ.get("PEBRA_RCA_BIN")
    try:
        os.environ["PATH"] = ""
        os.environ["PEBRA_RCA_BIN"] = str(Path.cwd() / "missing-rca")
        from pebra.adapters.rca_adapter import RustCodeAnalysisAdapter

        if RustCodeAnalysisAdapter().measure_delta("sample.py", "x = 1\n", "x = 2\n") is not None:
            raise DistributionVerificationError("missing RCA engine did not degrade safely")
    finally:
        if old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old_path
        if old_override is None:
            os.environ.pop("PEBRA_RCA_BIN", None)
        else:
            os.environ["PEBRA_RCA_BIN"] = old_override


def verify_codegraph_setup() -> None:
    """Install/index the pinned CodeGraph release against a tiny repository, then run doctor."""
    with tempfile.TemporaryDirectory(prefix="pebra-codegraph-smoke-") as raw:
        repo = Path(raw)
        (repo / "sample.ts").write_text(
            "export function add(left: number, right: number): number { return left + right; }\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "add", "sample.ts"], check=True, timeout=30)
        subprocess.run(
            [
                "git", "-c", "user.name=PEBRA verifier",
                "-c", "user.email=pebra-verifier@users.noreply.github.com",
                "-C", str(repo), "commit", "-q", "-m", "fixture",
            ],
            check=True,
            timeout=30,
        )
        setup = _run_cli(
            "setup-graph", "--fix", "--via", "standalone", "--repo-root", str(repo), "--json",
            cwd=repo,
            timeout=900,
        )
        if setup.returncode != 0:
            raise DistributionVerificationError(
                f"CodeGraph setup failed: {(setup.stderr or setup.stdout).strip()}"
            )
        doctor = _run_cli("doctor", "--repo-root", str(repo), "--json", cwd=repo)
        try:
            payload = json.loads(doctor.stdout)
        except json.JSONDecodeError as exc:
            raise DistributionVerificationError("doctor did not emit JSON") from exc
        if doctor.returncode != 0 or payload.get("ok") is not True:
            raise DistributionVerificationError(f"CodeGraph doctor failed: {payload}")


def _distribution_artifacts(dist_dir: Path) -> list[Path]:
    return sorted(
        path for path in dist_dir.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )


def write_checksums(dist_dir: Path) -> Path:
    artifacts = _distribution_artifacts(dist_dir)
    if not artifacts:
        raise DistributionVerificationError(f"no distributions found under {dist_dir}")
    output = dist_dir / "SHA256SUMS"
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in artifacts]
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    return output


def _artifact_digests(dist_dir: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in _distribution_artifacts(dist_dir)
    }


def write_candidate_manifest(dist_dir: Path, output: Path, tag: str, commit: str) -> Path:
    """Bind one candidate's tag and source commit to its exact distribution bytes."""
    if release_version_from_tag(tag) is None:
        raise DistributionVerificationError(f"invalid candidate tag: {tag!r}")
    if _COMMIT.fullmatch(commit) is None:
        raise DistributionVerificationError(f"invalid candidate commit: {commit!r}")
    artifacts = _artifact_digests(dist_dir)
    if not artifacts:
        raise DistributionVerificationError(f"no distributions found under {dist_dir}")
    payload = {
        "schema": "pebra.release-candidate.v1",
        "tag": tag,
        "commit": commit,
        "artifacts": artifacts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def verify_candidate_manifest(
    dist_dir: Path,
    manifest: Path,
    *,
    expected_tag: str,
    expected_commit: str,
) -> None:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DistributionVerificationError("candidate manifest is not valid JSON") from exc
    if payload.get("schema") != "pebra.release-candidate.v1":
        raise DistributionVerificationError("candidate manifest schema mismatch")
    if payload.get("tag") != expected_tag:
        raise DistributionVerificationError("candidate tag mismatch")
    if payload.get("commit") != expected_commit:
        raise DistributionVerificationError("candidate commit mismatch")
    if payload.get("artifacts") != _artifact_digests(dist_dir):
        raise DistributionVerificationError("candidate artifact mismatch")


def verify_index_digests(dist_dir: Path, index_json: Path) -> None:
    """Require an index release to contain exactly the candidate distribution bytes."""
    try:
        payload = json.loads(index_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DistributionVerificationError("index response is not valid JSON") from exc
    urls = payload.get("urls") if isinstance(payload, dict) else None
    if not isinstance(urls, list):
        raise DistributionVerificationError("index response has no artifact list")

    indexed: dict[str, str] = {}
    for entry in urls:
        if not isinstance(entry, dict):
            raise DistributionVerificationError("invalid index artifact entry")
        name = entry.get("filename")
        digests = entry.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(name, str) or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise DistributionVerificationError("invalid index artifact digest")
        if name in indexed:
            raise DistributionVerificationError(f"duplicate index artifact: {name}")
        indexed[name] = digest

    if indexed != _artifact_digests(dist_dir):
        raise DistributionVerificationError("index artifact mismatch")


def verify_checksums(dist_dir: Path, manifest: Path) -> None:
    """Verify that the manifest covers exactly the wheel and sdist in ``dist_dir``."""
    expected: dict[str, str] = {}
    for line in manifest.read_text(encoding="ascii").splitlines():
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise DistributionVerificationError(f"invalid checksum line: {line!r}")
        digest, name = match.groups()
        if name in expected:
            raise DistributionVerificationError(f"duplicate checksum entry: {name}")
        expected[name] = digest

    artifacts = {
        path.name: path
        for path in dist_dir.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    }
    if set(expected) != set(artifacts):
        raise DistributionVerificationError(
            "checksum manifest does not exactly match distributions: "
            f"manifest={sorted(expected)}, artifacts={sorted(artifacts)}"
        )
    for name, path in artifacts.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if not hmac.compare_digest(expected[name], actual):
            raise DistributionVerificationError(f"checksum mismatch: {name}")


def _one(dist_dir: Path, pattern: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise DistributionVerificationError(
            f"expected exactly one {pattern} under {dist_dir}, found {len(matches)}"
        )
    return matches[0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    archives = subparsers.add_parser("archives")
    archives.add_argument("dist_dir", type=Path)
    subparsers.add_parser("installed")
    subparsers.add_parser("codegraph")
    checksums = subparsers.add_parser("checksums")
    checksums.add_argument("dist_dir", type=Path)
    verify_manifest = subparsers.add_parser("verify-checksums")
    verify_manifest.add_argument("dist_dir", type=Path)
    verify_manifest.add_argument("manifest", type=Path)
    candidate = subparsers.add_parser("candidate-manifest")
    candidate.add_argument("dist_dir", type=Path)
    candidate.add_argument("output", type=Path)
    candidate.add_argument("--tag", required=True)
    candidate.add_argument("--commit", required=True)
    verify_candidate = subparsers.add_parser("verify-candidate")
    verify_candidate.add_argument("dist_dir", type=Path)
    verify_candidate.add_argument("manifest", type=Path)
    verify_candidate.add_argument("--tag", required=True)
    verify_candidate.add_argument("--commit", required=True)
    index_digests = subparsers.add_parser("index-digests")
    index_digests.add_argument("dist_dir", type=Path)
    index_digests.add_argument("index_json", type=Path)
    release_tag = subparsers.add_parser("release-tag")
    release_tag.add_argument("tag")
    release_tag.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "archives":
            verify_archives(_one(args.dist_dir, "*.whl"), _one(args.dist_dir, "*.tar.gz"))
        elif args.command == "installed":
            verify_installed()
        elif args.command == "codegraph":
            verify_codegraph_setup()
        elif args.command == "checksums":
            print(write_checksums(args.dist_dir))
        elif args.command == "verify-checksums":
            verify_checksums(args.dist_dir, args.manifest)
        elif args.command == "candidate-manifest":
            print(write_candidate_manifest(args.dist_dir, args.output, args.tag, args.commit))
        elif args.command == "verify-candidate":
            verify_candidate_manifest(
                args.dist_dir,
                args.manifest,
                expected_tag=args.tag,
                expected_commit=args.commit,
            )
        elif args.command == "index-digests":
            verify_index_digests(args.dist_dir, args.index_json)
        else:
            tag_version = release_version_from_tag(args.tag)
            project = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"]
            if tag_version is None or tag_version != project["version"]:
                raise DistributionVerificationError(
                    f"release tag {args.tag!r} does not match project version {project['version']!r}"
                )
    except (DistributionVerificationError, OSError, subprocess.SubprocessError) as exc:
        print(f"distribution verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"distribution verification passed: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
