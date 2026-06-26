"""Risk Observatory auth/CSP primitives (Phase 3b/5c-B) — agentmemory viewer pattern, reimplemented
in Python stdlib. Pure helpers (no fastapi import) so they're testable anywhere; the server wires the
bearer dependency + Host allowlist + CSP header around them.
"""

from __future__ import annotations

import hmac
import secrets


def generate_token() -> str:
    """A fresh session bearer token, printed in the startup URL. Thin MVP: session-scoped only — not
    persisted (a .pebra/dashboard.json with 0o600 perms for reuse/PID-stale is a later enhancement)."""
    return secrets.token_urlsafe(32)


def create_nonce() -> str:
    """A per-request CSP nonce for inline <script nonce=...> tags."""
    return secrets.token_urlsafe(16)


def build_csp(nonce: str) -> str:
    """Strict CSP: no external origins, scripts only via this request's nonce, self-hosted assets."""
    return (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'self'; "  # only the bundled /static/style.css; no inline styles
        "connect-src 'self'; img-src 'self'; font-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )


def token_matches(provided: str | None, expected: str) -> bool:
    """Constant-time bearer comparison (no early-out on length/prefix)."""
    return hmac.compare_digest(provided or "", expected)
