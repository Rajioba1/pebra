"""Stdlib http.server for the run observatory. Read-only over the run dir; NEVER imports pebra.

Routes:
  GET /                  -> the self-contained HTML shell (static/index.html)
  GET /static/<file>     -> static/app.js, static/style.css (allowlisted; no traversal)
  GET /api/runs          -> aggregate.list_runs()
  GET /api/run/<run_id>  -> aggregate.build_run_view()   (?mode=<mode> optional)

All aggregation is server-side (aggregate.py); the front-end only renders the JSON.
"""

from __future__ import annotations

import json
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from e2e.experiments.agent_ab.runners import launch_dashboard
from e2e.experiments.agent_ab.runners.observatory import aggregate, launch

_STATIC = Path(__file__).resolve().parent / "static"
_STATIC_FILES = {
    "/static/app.js": "text/javascript; charset=utf-8",
    "/static/style.css": "text/css; charset=utf-8",
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # keep the dev console quiet
        pass

    def _send_json(self, obj: object, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name: str, content_type: str) -> None:
        try:
            body = (_STATIC / name).read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if content_type.startswith("text/html"):
            # Self-contained shell: everything is same-origin, so lock it down.
            self.send_header("Content-Security-Policy", "default-src 'self'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        ab_out = self.server.ab_out  # type: ignore[attr-defined]

        if path == "/":
            self._send_file("index.html", "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self.send_response(204)  # no icon; suppress the browser's default 404 request
            self.end_headers()
            return
        if path in _STATIC_FILES:
            self._send_file(Path(path).name, _STATIC_FILES[path])
            return
        if path == "/api/runs":
            self._send_json({"runs": aggregate.list_runs(ab_out=ab_out)})
            return
        if path.startswith("/api/run/"):
            run_id = urllib.parse.unquote(path[len("/api/run/"):])
            # The bare regex allows "." / ".." (dot is a valid char); reject them too, matching
            # launch_dashboard._run_root's full guard, so they can't resolve to ab_out / its parent.
            if not launch_dashboard._RUN_ID_RE.fullmatch(run_id) or run_id in (".", ".."):  # noqa: SLF001
                self._send_json({"error": "invalid run-id"}, 400)
                return
            mode = urllib.parse.parse_qs(parsed.query).get("mode", [None])[0]
            try:
                view = aggregate.build_run_view(run_id, ab_out=ab_out, mode=mode)
            except aggregate.RunNotFound:
                self._send_json({"error": "unknown run-id"}, 404)
                return
            self._send_json(view)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/launch":
            self._send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json({"error": "invalid content-length"}, 400)
            return
        raw_body = self.rfile.read(length) if length else b"{}"
        if self.headers.get("X-PEBRA-Observatory") != "1":
            self._send_json({"error": "missing observatory launch header"}, 403)
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json({"error": "content-type must be application/json"}, 415)
            return
        try:
            body = json.loads(raw_body or b"{}")
        except ValueError:
            self._send_json({"error": "invalid json"}, 400)
            return
        if not isinstance(body, dict):
            self._send_json({"error": "json body must be an object"}, 400)
            return
        run_id = str(body.get("run_id", ""))
        clone = str(body.get("clone", ""))
        # clone is matched by exact string against discovered stores (never used as a path), but reject
        # path-y input and bad run-ids up front — nothing is spawned for invalid input.
        if (not launch_dashboard._RUN_ID_RE.fullmatch(run_id) or run_id in (".", "..")  # noqa: SLF001
                or not clone or "/" in clone or "\\" in clone):
            self._send_json({"error": "invalid run-id or clone"}, 400)
            return
        result = self.server.registry.launch(run_id, clone, ab_out=self.server.ab_out)  # type: ignore[attr-defined]
        ok = result.get("status") in ("launched", "already_running")
        status = 200 if ok else _launch_error_status(result)
        self._send_json(result, status)


def _launch_error_status(result: dict) -> int:
    reason = str(result.get("reason") or "")
    if reason.startswith("no such store"):
        return 404
    if "shutting down" in reason:
        return 503
    return 502


class _ObservatoryServer(ThreadingHTTPServer):
    def server_close(self) -> None:
        registry = getattr(self, "registry", None)
        if registry is not None:
            registry.shutdown_all()
        super().server_close()


def build_server(*, ab_out: Path, host: str = "127.0.0.1", port: int = 0,
                 registry: "launch.DashboardRegistry | None" = None) -> ThreadingHTTPServer:
    """Create (but do not start) the observatory server bound to ``ab_out``. port=0 => OS-assigned."""
    server = _ObservatoryServer((host, port), _Handler)
    server.ab_out = Path(ab_out)  # type: ignore[attr-defined]
    server.registry = registry if registry is not None else launch.DashboardRegistry()  # type: ignore[attr-defined]
    return server


def serve(*, ab_out: Path, host: str = "127.0.0.1", port: int, open_browser: bool = False,
          open_hash: str = "") -> int:
    server = build_server(ab_out=ab_out, host=host, port=port)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/{open_hash}"
    print(f"PEBRA Experiment Run Observatory: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0
