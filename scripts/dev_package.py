"""Build and exercise a fresh local PEBRA wheel without publishing it."""

from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


_DASHBOARD_PREFIX = "PEBRA Risk Observatory: "


class DevPackageError(RuntimeError):
    """The local packaged-development workflow failed."""


def find_single_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise DevPackageError(
            f"expected exactly one wheel under {dist_dir}, found {len(wheels)}"
        )
    return wheels[0]


def find_single_sdist(dist_dir: Path) -> Path:
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(sdists) != 1:
        raise DevPackageError(
            f"expected exactly one sdist under {dist_dir}, found {len(sdists)}"
        )
    return sdists[0]


def runtime_python(venv: Path, *, platform: str = sys.platform) -> Path:
    if platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def dashboard_url_from_line(line: str) -> str | None:
    if not line.startswith(_DASHBOARD_PREFIX):
        return None
    return line.removeprefix(_DASHBOARD_PREFIX).strip()


def stage_source_tree(root: Path, destination: Path) -> None:
    """Copy current contents of Git-tracked paths without ignored or stale build artifacts."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        raise DevPackageError("git could not enumerate the packaged-development source tree")
    destination.mkdir(parents=True, exist_ok=False)
    for raw_path in listed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(os.fsdecode(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise DevPackageError(f"git returned an unsafe source path: {relative}")
        source = root / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def _run_checked(argv: Sequence[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(list(argv), cwd=cwd, env=env, check=False)
    if completed.returncode != 0:
        raise DevPackageError(
            f"command failed with exit code {completed.returncode}: {' '.join(argv)}"
        )


def _runtime_env(runtime: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(runtime.parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    return env


def _read_dashboard_url(process: subprocess.Popen[str], timeout: float = 30.0) -> str:
    if process.stdout is None:
        raise DevPackageError("dashboard stdout was not captured")
    lines: queue.Queue[str | None] = queue.Queue()

    def read_lines() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read_lines, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            if process.poll() is not None:
                break
            continue
        if line is None:
            break
        url = dashboard_url_from_line(line.strip())
        if url is not None:
            return url
    raise DevPackageError("installed dashboard did not print a startup URL")


def _wait_for_dashboard(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - loopback URL
                if response.status == 200 and b"PEBRA" in response.read():
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise DevPackageError(f"installed dashboard did not become ready: {last_error}")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def run(*, open_browser: bool = False) -> None:
    root = Path(__file__).resolve().parents[1]
    verifier = root / "scripts" / "verify_distribution.py"
    with tempfile.TemporaryDirectory(prefix="pebra-dev-package-") as raw:
        workspace = Path(raw)
        dist_dir = workspace / "dist"
        runtime_dir = workspace / "runtime"
        repo = workspace / "repo"
        source_dir = workspace / "source"
        dist_dir.mkdir()
        repo.mkdir()
        stage_source_tree(root, source_dir)

        _run_checked(
            [sys.executable, "-m", "build", "--outdir", str(dist_dir)],
            cwd=source_dir,
        )
        wheel = find_single_wheel(dist_dir)
        find_single_sdist(dist_dir)
        _run_checked(
            [sys.executable, "-I", str(verifier), "archives", str(dist_dir)],
            cwd=workspace,
        )
        _run_checked([sys.executable, "-m", "venv", str(runtime_dir)], cwd=workspace)
        python = runtime_python(runtime_dir)
        env = _runtime_env(python)
        _run_checked([str(python), "-m", "pip", "install", str(wheel)], cwd=workspace, env=env)
        _run_checked([str(python), "-I", str(verifier), "installed"], cwd=workspace, env=env)

        command = [
            str(python),
            "-I",
            "-u",
            "-m",
            "pebra",
            "dashboard",
            "--repo-root",
            str(repo),
            "--port",
            "0",
        ]
        if open_browser:
            command.append("--open")
        process = subprocess.Popen(
            command,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            url = _read_dashboard_url(process)
            _wait_for_dashboard(url)
            print(f"Packaged development wheel: {wheel.name}")
            print(f"Installed dashboard ready: {url}")
            if open_browser:
                print("Press Ctrl+C to stop the packaged dashboard.")
                process.wait()
        except KeyboardInterrupt:
            pass
        finally:
            _stop(process)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the installed dashboard and keep it running until interrupted.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(open_browser=args.open)
    except (DevPackageError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
