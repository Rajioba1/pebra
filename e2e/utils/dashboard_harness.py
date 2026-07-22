"""Dashboard harness — launch `pebra dashboard` on an OS-assigned port and discover its URL/token.

The dashboard prints ``PEBRA Risk Observatory: http://127.0.0.1:<port>/`` with optional query
parameters once uvicorn is serving; we parse that line (the only liveness signal — there is no
healthcheck route).
A reader thread + queue avoids a stdout-readline deadlock; stderr is drained concurrently.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from e2e.utils import cli_harness as ch

_URL_RE = re.compile(r"(http://[^/\s]+:(\d+)/(?:\?\S*)?)")


@dataclass
class DashboardInfo:
    url: str
    port: int
    token: str
    repo_id: str


def _pump(stream, q: queue.Queue) -> None:
    for line in stream:
        q.put(line)
    q.put(None)


def _wait_until_ready(port: int, token: str, *, timeout: float) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    headers = {"Authorization": f"Bearer {token}"} if token else {}  # token-free on loopback default
    while time.time() < deadline:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/chain-status", headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=1):  # noqa: S310 - loopback only
                return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"dashboard URL printed but API was not ready: {last_error}")


@contextmanager
def running_dashboard(
    repo_root: Path | str,
    db: Path | str,
    *,
    timeout: float = 30.0,
    auth: str | None = None,
    dev: bool = False,
):
    proc = ch.dashboard_proc(repo_root=repo_root, db=db, port=0, auth=auth, dev=dev)
    q: queue.Queue = queue.Queue()
    stderr_lines: list[str] = []
    threading.Thread(target=_pump, args=(proc.stdout, q), daemon=True).start()

    def _drain_stderr() -> None:
        for line in proc.stderr:
            stderr_lines.append(line)

    threading.Thread(target=_drain_stderr, daemon=True).start()

    info: DashboardInfo | None = None
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            try:
                line = q.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if line is None:
                break
            match = _URL_RE.search(line)
            if match:
                url = match.group(1)
                params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                info = DashboardInfo(
                    url=url, port=int(match.group(2)),
                    token=params.get("token", [""])[0], repo_id=params.get("repo", [""])[0],
                )
                break
        if info is None:
            stderr = "".join(stderr_lines)
            raise RuntimeError(
                "dashboard did not print its URL line within the timeout"
                + (f"\n--- stderr ---\n{stderr}" if stderr else "")
            )
        _wait_until_ready(info.port, info.token, timeout=max(0.0, deadline - time.time()))
        yield info
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - best-effort teardown
            proc.kill()
