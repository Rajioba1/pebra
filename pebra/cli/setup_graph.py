"""`pebra setup-graph` / `pebra doctor` — graph-engine maintenance (Architecture §3, M5c.5).

EXPLICIT operator actions, deliberately separate from `pebra assess` (which never installs and only does
a safe repair-sync of an existing index). Product language is "graph engine"; default provider is
codegraph. Stdlib + subprocess only; imports neither the assess path nor the codegraph adapter.

Install policy (ratified):
  - Default install = an EXACT pinned version (core.graph_version.CODEGRAPH_DEFAULT_VERSION), never
    floating-latest, so fan-in stays reproducible.
  - `--version V` is refused if V is outside CODEGRAPH_ACCEPTED_RANGE unless `--allow-unsupported`
    (and even then the assess-path range check marks the graph untrusted -> Gate 13).
  - Standalone-primary: download the per-OS/arch release bundle (no Node needed) and VERIFY its SHA256
    against the published SHA256SUMS before extracting. `--via npm` (or auto-fallback) installs the same
    pinned version via npm (registry provenance; needs Node). musl/Alpine + unsupported arch fail clear.

Security note: SHA256SUMS is unsigned (integrity, not authenticity). That is acceptable here because
this is explicit operator intent (`setup-graph --fix`) and `assess` never downloads a binary; security-
locked environments can use `--via npm` for registry provenance.

Worktree rule (strict one-worktree-one-index): a `worktreeMismatch` means the resolved index belongs to
another worktree; the fix is a worktree-local index (`codegraph init <worktree>`), NOT a sync.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.engine_paths import find_engine, managed_install_root
from pebra.core.graph_version import (
    CODEGRAPH_ACCEPTED_RANGE,
    CODEGRAPH_DEFAULT_VERSION,
    in_accepted_range,
)

_ENGINE = "codegraph"
_RELEASES = "https://github.com/colbymchenry/codegraph/releases/download"
_SUPPORTED_TARGETS = {
    "darwin-arm64", "darwin-x64", "linux-x64", "linux-arm64", "win32-x64", "win32-arm64",
}
_MANUAL_HINT = (
    "install Node.js + npm and run: npm install -g @colbymchenry/codegraph@"
    f"{CODEGRAPH_DEFAULT_VERSION}"
)


def register(subparsers: Any) -> None:
    sg = subparsers.add_parser(
        "setup-graph", help="Install/initialize the graph engine index for this repo/worktree.",
        epilog="Env: PEBRA_CODEGRAPH_BIN overrides where PEBRA looks for codegraph "
               "(a bin directory or the launcher path) — takes precedence over PATH and the "
               "managed install.",
    )
    sg.add_argument("--repo-root", default=".")
    sg.add_argument("--fix", action="store_true",
                    help="Repair a worktree mismatch by building a worktree-local index.")
    sg.add_argument("--version", default=None,
                    help=f"codegraph version to install (default: pinned {CODEGRAPH_DEFAULT_VERSION}).")
    sg.add_argument("--allow-unsupported", action="store_true",
                    help="Permit a --version outside the accepted range (graph then marked untrusted).")
    sg.add_argument("--via", choices=("auto", "standalone", "npm"), default="auto",
                    help="Install route: auto (standalone, npm fallback), standalone-only, or npm-only.")
    sg.add_argument("--json", action="store_true", dest="as_json")
    sg.set_defaults(func=run_setup_graph)

    dr = subparsers.add_parser(
        "doctor", help="Diagnose (read-only) the graph engine; --fix-graph to repair this worktree."
    )
    dr.add_argument("--repo-root", default=".")
    dr.add_argument("--fix-graph", action="store_true", help="Repair the graph index for this worktree.")
    dr.add_argument("--json", action="store_true", dest="as_json")
    dr.set_defaults(func=run_doctor)


# --- engine shell helpers (stdlib only) ---

def _installed() -> bool:
    return find_engine() is not None  # PEBRA_CODEGRAPH_BIN -> PATH -> managed install


def _engine_exe() -> str:
    """The resolved engine launcher (full path) for codegraph invocations; bare name if unresolved
    (subprocess then fails naturally). Ensures off-PATH managed installs are still invoked."""
    return find_engine() or _ENGINE


def _run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    # resolve_engine_argv handles the Windows .cmd shim (codegraph/npm have no .exe) — a bare
    # ["codegraph", ...] FileNotFoundErrors on Windows even when on PATH. Also handles full launcher paths.
    try:
        argv = resolve_engine_argv(cmd[0], cmd[1:])
        # force UTF-8 decode: codegraph emits UTF-8 progress output; the Windows default cp1252 raises
        # UnicodeDecodeError on it (A2 finding).
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, check=False)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found on PATH"
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - defensive
        return 1, "", str(exc)


def _installed_version() -> str | None:
    exe = find_engine()
    if exe is None:
        return None
    rc, out, _ = _run([exe, "--version"], timeout=30)
    return out.strip() if rc == 0 and out.strip() else None


def _status(repo_root: str) -> dict[str, Any] | None:
    rc, out, _ = _run([_engine_exe(), "status", repo_root, "--json"], timeout=30)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _healthy(status: dict[str, Any] | None) -> bool:
    if not status or status.get("initialized") is False or status.get("worktreeMismatch"):
        return False
    pending = status.get("pendingChanges") or {}
    has_pending = any(pending.get(k) for k in ("added", "modified", "removed"))
    reindex = bool((status.get("index") or {}).get("reindexRecommended"))
    return not has_pending and not reindex


def _diagnosis(status: dict[str, Any] | None) -> dict[str, Any]:
    if status is None:
        return {"initialized": False, "worktree_mismatch": False, "healthy": False,
                "detail": "no status (engine errored or repo not initialized)"}
    return {
        "initialized": status.get("initialized") is not False,
        "worktree_mismatch": bool(status.get("worktreeMismatch")),
        "pending_changes": status.get("pendingChanges"),
        "reindex_recommended": bool((status.get("index") or {}).get("reindexRecommended")),
        "healthy": _healthy(status),
    }


def _emit(payload: dict[str, Any], as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("\n".join(lines))


# --- platform + standalone install (stdlib only) ---

def _target() -> str | None:
    """Return codegraph's release target triple for this host (e.g. 'darwin-arm64'), or None if
    there is no published standalone asset for this OS/arch."""
    sysname = {"Darwin": "darwin", "Linux": "linux", "Windows": "win32"}.get(platform.system())
    arch = {"x86_64": "x64", "amd64": "x64", "AMD64": "x64",
            "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine())
    if not sysname or not arch:
        return None
    target = f"{sysname}-{arch}"
    return target if target in _SUPPORTED_TARGETS else None


def _is_musl() -> bool:
    """True on a musl libc system (Alpine) — codegraph publishes only glibc standalone bundles."""
    if platform.system() != "Linux":
        return False
    try:
        if "alpine" in Path("/etc/os-release").read_text(encoding="utf-8").lower():
            return True
    except OSError:
        pass
    try:
        return "musl" in (platform.libc_ver()[0] or "").lower()
    except Exception:  # pragma: no cover - defensive
        return False


def _download(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # https GitHub release asset
        return resp.read()


def _expected_sha(sums_text: str, asset_name: str) -> str | None:
    """Find the SHA256 for asset_name in a 'hash  name' SHA256SUMS file (basename match)."""
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == asset_name:
            return parts[0].lower()
    return None


def _install_root(version: str) -> Path:
    return managed_install_root(version)  # single source of truth (shared with find_engine)


def _release_tag(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def _extract(data: bytes, asset_name: str, dest: Path) -> None:
    """Extract the archive into dest, stripping codegraph's leading 'codegraph-<target>/' dir, with a
    path-traversal guard (never write outside dest)."""
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()

    def _safe(rel: str) -> Path | None:
        rel = rel.split("/", 1)[1] if "/" in rel else ""  # strip top-level dir
        if not rel:
            return None
        out = (dest / rel).resolve()
        if dest_resolved not in out.parents and out != dest_resolved:
            return None  # traversal attempt -> skip
        return out

    if asset_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for m in zf.infolist():
                out = _safe(m.filename)
                if out is None:
                    continue
                if m.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                else:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(zf.read(m))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for m in tf.getmembers():
                out = _safe(m.name)
                if out is None:
                    continue
                if m.isdir():
                    out.mkdir(parents=True, exist_ok=True)
                elif m.isfile():
                    out.parent.mkdir(parents=True, exist_ok=True)
                    f = tf.extractfile(m)
                    if f is not None:
                        out.write_bytes(f.read())
                        out.chmod(out.stat().st_mode | 0o755)


def _install_standalone(as_json: bool, version: str, target: str) -> bool:
    """Download the pinned standalone bundle, VERIFY its SHA256, extract, and link it onto PATH.
    Returns True on success; False (with a message) lets the caller fall back to npm."""
    ext = ".zip" if target.startswith("win32") else ".tar.gz"
    asset = f"{_ENGINE}-{target}{ext}"
    base = f"{_RELEASES}/{_release_tag(version)}"
    try:
        sums = _download(f"{base}/SHA256SUMS").decode("utf-8", "replace")
        expected = _expected_sha(sums, asset)
        if not expected:
            _emit({"ok": False, "step": "checksum", "asset": asset}, as_json,
                  [f"no SHA256 entry for {asset} in the release SHA256SUMS; not installing."])
            return False
        data = _download(f"{base}/{asset}")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _emit({"ok": False, "step": "download", "error": str(exc)}, as_json,
              [f"standalone download failed ({exc}); falling back to npm."])
        return False
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        _emit({"ok": False, "step": "checksum", "expected": expected, "actual": actual}, as_json,
              [f"checksum mismatch for {asset}; refusing to install (integrity check failed)."])
        return False  # never extract/run an unverified binary

    dest = _install_root(version)
    try:
        _extract(data, asset, dest)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        _emit({"ok": False, "step": "extract", "error": str(exc)}, as_json,
              [f"could not extract {asset} ({exc}); falling back to npm."])
        return False

    if target.startswith("win32"):
        # the real win32 bundle ships bin/codegraph.cmd (a node.exe shim), NOT a .exe (A2 finding).
        launcher = next(
            (dest / "bin" / n for n in (f"{_ENGINE}.cmd", f"{_ENGINE}.exe")
             if (dest / "bin" / n).is_file()),
            dest / "bin" / f"{_ENGINE}.cmd",
        )
    else:
        launcher = dest / "bin" / _ENGINE
    rc, out, _ = _run([str(launcher), "--version"], timeout=30)  # verify the extracted binary runs
    if rc != 0:
        _emit({"ok": False, "step": "verify", "launcher": str(launcher)}, as_json,
              [f"extracted codegraph did not run from {launcher}; falling back to npm."])
        return False
    _link_onto_path(launcher, as_json, version)
    return True


def _link_onto_path(launcher: Path, as_json: bool, version: str) -> None:
    """Best-effort: symlink the launcher into a per-user bin dir on POSIX; on Windows, advise the PATH
    addition (we do not edit the registry). The current process PATH is updated in all cases so the
    same `pebra setup-graph` invocation can immediately run `codegraph init/sync`."""
    if platform.system() == "Windows":  # .cmd or .exe launcher — no POSIX symlink, advise PATH
        os.environ["PATH"] = f"{launcher.parent}{os.pathsep}{os.environ.get('PATH', '')}"
        _emit({"ok": True, "step": "path", "bin": str(launcher.parent), "version": version}, as_json,
              [f"codegraph {version} installed at {launcher}.",
               f"add this to PATH:  {launcher.parent}"])
        return
    bindir = Path.home() / ".local" / "bin"
    try:
        bindir.mkdir(parents=True, exist_ok=True)
        link = bindir / _ENGINE
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(launcher)
    except OSError:
        os.environ["PATH"] = f"{launcher.parent}{os.pathsep}{os.environ.get('PATH', '')}"
        _emit({"ok": True, "step": "path", "bin": str(launcher.parent), "version": version}, as_json,
              [f"codegraph {version} installed at {launcher}.",
               f"add this to PATH:  export PATH=\"{launcher.parent}:$PATH\""])
        return
    os.environ["PATH"] = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
    on_path = shutil.which(_ENGINE) is not None
    lines = [f"codegraph {version} installed; linked into {bindir}."]
    if not on_path:
        lines.append(f'ensure it is on PATH:  export PATH="{bindir}:$PATH"')
    _emit({"ok": True, "step": "path", "bin": str(bindir), "version": version, "on_path": on_path},
          as_json, lines)


def _install_npm(as_json: bool, version: str) -> int | None:
    if shutil.which("npm") is None:
        _emit({"ok": False, "step": "npm", "error": "npm not found"}, as_json,
              [f"graph engine '{_ENGINE}' not found and npm is unavailable; {_MANUAL_HINT}"])
        return 1
    spec = f"@colbymchenry/{_ENGINE}@{version}"  # PINNED, never floating-latest
    rc, _, err = _run(["npm", "install", "-g", spec], timeout=600)
    if rc == 0 and _installed():
        return None
    _emit({"ok": False, "step": "npm", "error": err.strip(), "spec": spec}, as_json,
          [f"npm install of {spec} failed; run manually: npm install -g {spec}", err.strip()])
    return 1


def _resolve_version(requested: str | None, allow_unsupported: bool, as_json: bool) -> tuple[str | None, int | None]:
    """Apply the version policy. Returns (version, error_code). A --version outside the accepted range is
    refused unless --allow-unsupported."""
    if not requested:
        return CODEGRAPH_DEFAULT_VERSION, None
    if in_accepted_range(requested) or allow_unsupported:
        return requested, None
    _emit({"ok": False, "step": "version", "version": requested,
           "accepted_range": CODEGRAPH_ACCEPTED_RANGE}, as_json,
          [f"requested codegraph {requested} is outside the accepted range {CODEGRAPH_ACCEPTED_RANGE}.",
           "re-run with --allow-unsupported to install it anyway "
           "(graph evidence will then be marked untrusted)."])
    return None, 2


def _ensure_installed(
    as_json: bool, *, version: str, via: str, explicit_version: bool
) -> int | None:
    """Install/repair the engine to the pinned version. Returns an error code or None on success."""
    if _installed() and via == "auto":
        cur = _installed_version()
        if cur and ((explicit_version and cur == version) or (not explicit_version and in_accepted_range(cur))):
            return None  # already have a supported version; nothing to do
    if via == "npm":
        return _install_npm(as_json, version)
    target = _target()
    if target and not _is_musl():
        if _install_standalone(as_json, version, target):
            return None
        if via == "standalone":
            return 1  # standalone-only and it failed
        return _install_npm(as_json, version)  # auto -> npm fallback
    # no standalone asset for this platform
    reason = ("musl/Alpine has no glibc standalone bundle" if _is_musl()
              else f"no standalone bundle for {platform.system()}/{platform.machine()}")
    if via == "standalone":
        _emit({"ok": False, "step": "platform", "error": reason}, as_json,
              [f"{reason}; {_MANUAL_HINT}"])
        return 1
    _emit({"ok": True, "step": "platform", "note": reason}, as_json, [f"{reason}; trying npm."])
    return _install_npm(as_json, version)


def _build_worktree_local_index(repo_root: str) -> dict[str, Any] | None:
    """init (worktree-local; indexes by default) + sync, then return the fresh status."""
    exe = _engine_exe()
    _run([exe, "init", repo_root], timeout=600)
    _run([exe, "sync", repo_root], timeout=300)
    return _status(repo_root)


def run_setup_graph(args: Any) -> int:
    repo = args.repo_root
    version, verr = _resolve_version(args.version, args.allow_unsupported, args.as_json)
    if verr is not None:
        return verr
    install_err = _ensure_installed(
        args.as_json, version=version, via=args.via, explicit_version=args.version is not None
    )
    if install_err is not None:
        return install_err
    status = _build_worktree_local_index(repo)
    diag = _diagnosis(status)
    ok = diag["healthy"]
    intent = "repair worktree-local index" if args.fix else "initialize graph index"
    lines = [f"setup-graph ({intent}) — repo: {repo}, codegraph {version}",
             f"  initialized:       {diag.get('initialized')}",
             f"  worktree_mismatch: {diag.get('worktree_mismatch')}",
             f"  healthy:           {ok}"]
    if not ok and diag.get("worktree_mismatch"):
        lines.append("  worktree mismatch persists — ensure you run this inside the target worktree.")
    _emit({"ok": ok, "command": "setup-graph", "repo_root": repo, "version": version,
           "diagnosis": diag}, args.as_json, lines)
    return 0 if ok else 1


def run_doctor(args: Any) -> int:
    repo = args.repo_root
    if not _installed():
        _emit({"ok": False, "step": "engine", "error": "not found"}, args.as_json,
              [f"graph engine '{_ENGINE}' not found; {_MANUAL_HINT} (or run: pebra setup-graph)"])
        return 1
    runtime_ver = _installed_version()
    in_range = bool(runtime_ver) and in_accepted_range(runtime_ver)
    status = _status(repo)
    diag = _diagnosis(status)
    repaired = False
    if args.fix_graph and not diag["healthy"]:
        status = _build_worktree_local_index(repo)
        diag = _diagnosis(status)
        repaired = True
    ok = diag["healthy"] and in_range
    lines = [f"doctor — repo: {repo}",
             f"  codegraph version: {runtime_ver or 'unknown'} (accepted {CODEGRAPH_ACCEPTED_RANGE})",
             f"  version_in_range:  {in_range}",
             f"  initialized:       {diag.get('initialized')}",
             f"  worktree_mismatch: {diag.get('worktree_mismatch')}",
             f"  reindex_needed:    {diag.get('reindex_recommended')}",
             f"  healthy:           {ok}"]
    if not in_range:
        lines.append("  version outside accepted range — run: pebra setup-graph --fix")
    elif not ok and not args.fix_graph:
        lines.append("  run `pebra doctor --fix-graph` (or `pebra setup-graph --fix`) to repair.")
    _emit({"ok": ok, "command": "doctor", "repo_root": repo, "repaired": repaired,
           "codegraph_version": runtime_ver, "version_in_range": in_range,
           "accepted_range": CODEGRAPH_ACCEPTED_RANGE, "diagnosis": diag}, args.as_json, lines)
    return 0 if ok else 1
