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


def create_app(db_path: str, token: str, *, allowed_hosts: Iterable[str] | None = None) -> FastAPI:
    app = FastAPI(title="PEBRA Risk Observatory")
    app.state.db_path = db_path
    app.state.token = token
    allowed = _allowed_hosts(allowed_hosts)

    @app.middleware("http")
    async def _host_guard(request: Request, call_next):
        if _hostname(request.headers.get("host", "")) not in allowed:  # DNS-rebinding / non-loopback
            return PlainTextResponse("forbidden host", status_code=403)
        return await call_next(request)

    def require_bearer(request: Request) -> None:
        header = request.headers.get("authorization", "")
        provided = header[7:] if header[:7].lower() == "bearer " else None
        if not auth.token_matches(provided, request.app.state.token):
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
) -> None:
    import uvicorn

    token = token or auth.generate_token()
    port = ports.allocate_port(host, requested=requested_port, instance=instance)
    app = create_app(db_path, token)
    repo_q = f"&repo={repo_id}" if repo_id else ""
    print(f"PEBRA Risk Observatory: http://{host}:{port}/?token={token}{repo_q}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
