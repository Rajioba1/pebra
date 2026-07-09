"""Risk Observatory server (Phase 3b/5c-D) — local FastAPI app + serve().

Security baseline (agentmemory viewer pattern, reimplemented stdlib/ASGI): loopback bind, Host
allowlist / DNS-rebinding guard, bearer-token auth on /api, per-request CSP nonce, self-hosted static
assets. The dashboard is a surface — it imports adapters + the web stack, never app/core.

Imported lazily by cli/dashboard.py so the dep-light CLI (and the golden) never pull FastAPI.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from pebra.dashboard import auth, ports
from pebra.dashboard.api import build_router

_HERE = Path(__file__).parent
_STATIC = _HERE / "static"
_TEMPLATES = _HERE / "templates"
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

_env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=select_autoescape(["html"]))


def _allowed_hosts(extra: Iterable[str] | None) -> frozenset[str]:
    env_hosts = (h for h in os.environ.get("PEBRA_ALLOWED_HOSTS", "").split(",") if h)
    return _LOOPBACK | set(extra or []) | set(env_hosts)


def _hostname(host: str) -> str:
    """Hostname from a Host header, stripping the port. Handles IPv6 bracket form ([::1], [::1]:p)
    explicitly so a bare IPv6 loopback isn't mangled by a naive rsplit on ':'."""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host.strip("[]")
    return host.rsplit(":", 1)[0] if ":" in host else host


def resolve_dashboard_token(
    host: str, auth_mode: str = "auto", explicit_token: str | None = None
) -> str | None:
    """Resolve the effective bearer for a bind host + auth mode. Returns None for the no-auth posture.

    - ``auto``  : loopback bind -> no token (convenience); any network bind -> a generated token.
    - ``token`` : always a token (generated if not supplied), even on loopback (paranoid local use).
    - ``none``  : no token, but ONLY on a loopback bind; a network bind raises (fail loudly), because
      serving the store to the LAN with no auth is never what the operator meant.
    """
    is_loopback = host in _LOOPBACK
    if auth_mode == "none":
        if not is_loopback:
            raise ValueError(
                f"--auth none is only allowed on a loopback host; --host {host} would expose the "
                "dashboard to the network without a token. Use --auth token (or drop --auth none)."
            )
        return None
    if auth_mode == "token":
        return explicit_token or auth.generate_token()
    if auth_mode == "auto":
        if is_loopback:
            return explicit_token or None  # normalize "" -> None so no-auth never becomes an empty token
        return explicit_token or auth.generate_token()
    raise ValueError(f"unknown auth mode {auth_mode!r} (expected auto|token|none)")


def _require_token_for_network_bind(host: str, token: str | None) -> None:
    """Bind-point invariant: a non-loopback bind MUST carry a token. Enforced here (not only in the CLI
    via resolve_dashboard_token) so a direct ``serve(host="0.0.0.0", token=None)`` can't silently expose
    the store unauthenticated. Empty string counts as no token."""
    if host not in _LOOPBACK and not token:
        raise ValueError(
            f"refusing to serve on non-loopback host {host!r} without a token; this would expose the "
            "assessment store to the network unauthenticated"
        )


def _startup_url(host: str, port: int, token: str | None, repo_id: str | None) -> str:
    """The URL printed at startup (and opened by --open). ``?token=`` appears only when auth is on;
    ``?repo=`` whenever a repo is bound. If neither is present, the URL is the bare loopback page."""
    parts: list[str] = []
    if token is not None:
        parts.append(f"token={token}")
    if repo_id:
        parts.append(f"repo={repo_id}")
    query = "&".join(parts)
    return f"http://{host}:{port}/?{query}" if query else f"http://{host}:{port}/"


def create_app(
    db_path: str,
    token: str | None,
    *,
    allowed_hosts: Iterable[str] | None = None,
    repo_id: str | None = None,
    repo_root: str | None = None,
    graph_reader: object | None = None,
) -> FastAPI:
    app = FastAPI(title="PEBRA Risk Observatory")
    app.state.db_path = db_path
    app.state.token = token or None
    app.state.repo_id = repo_id
    # repo_root binds the graph routes to a codebase on disk (the .codegraph index). None (e.g. a
    # replayed db from another machine) makes the graph routes fail-soft, never error.
    app.state.repo_root = repo_root
    # graph_reader is injectable for tests; the default reads codegraph's SQLite via the real gate.
    if graph_reader is None:
        from pebra.adapters.codegraph_graph_reader import CodeGraphReader

        graph_reader = CodeGraphReader()
    app.state.graph_reader = graph_reader
    allowed = _allowed_hosts(allowed_hosts)

    @app.middleware("http")
    async def _host_guard(request: Request, call_next):
        if _hostname(request.headers.get("host", "")) not in allowed:  # DNS-rebinding / non-loopback
            return PlainTextResponse("forbidden host", status_code=403)
        return await call_next(request)

    def require_bearer(request: Request) -> None:
        expected = request.app.state.token
        if expected is None:
            return  # no-auth posture (loopback default): the bearer gate is disabled entirely.
        header = request.headers.get("authorization", "")
        provided = header[7:] if header[:7].lower() == "bearer " else None
        if not auth.token_matches(provided, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    app.include_router(build_router(require_bearer))
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        nonce = auth.create_nonce()  # fresh per request
        html = _env.get_template("index.html").render(nonce=nonce)
        return HTMLResponse(html, headers={"Content-Security-Policy": auth.build_csp(nonce)})

    return app


def serve(
    db_path: str,
    *,
    host: str = "127.0.0.1",
    requested_port: int | None = None,
    instance: int = 0,
    token: str | None = None,
    repo_id: str | None = None,
    repo_root: str | None = None,
    open_browser: bool = False,
) -> None:
    import uvicorn

    # token is authoritative here: None means the no-auth posture (resolved by the caller via
    # resolve_dashboard_token). serve() no longer invents a token, so loopback-default stays token-free.
    token = token or None
    _require_token_for_network_bind(host, token)  # never expose the store to the network unauthenticated
    port = ports.allocate_port(host, requested=requested_port, instance=instance)
    app = create_app(db_path, token, repo_id=repo_id, repo_root=repo_root)
    url = _startup_url(host, port, token, repo_id)
    print(f"PEBRA Risk Observatory: {url}")
    if open_browser:
        import threading
        import webbrowser

        # Fire shortly AFTER uvicorn starts listening so the browser doesn't race a not-yet-bound port.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
