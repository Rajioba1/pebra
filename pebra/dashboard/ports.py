"""Risk Observatory port allocation (Phase 3b/5c-B, AD-25).

Base port 9473; `--instance N` maps to 9473 + N*100; a requested port is a fail-fast pin (0 means
OS-assigned); otherwise auto-increment from the base until a free port is found. Bind-test on the
loopback host so collisions are detected before the server starts.
"""

from __future__ import annotations

import socket

BASE_PORT = 9473
_INSTANCE_STRIDE = 100


def _family_for(host: str) -> int:
    # IPv6 literals contain ':'; IPv4 / hostnames don't. Pick the matching address family so an IPv6
    # loopback bind doesn't fail on an AF_INET socket.
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def _is_free(host: str, port: int) -> bool:
    with socket.socket(_family_for(host), socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def _os_assigned_port(host: str) -> int:
    """Bind port 0 and read back the concrete port the OS chose, so callers can print a usable URL."""
    with socket.socket(_family_for(host), socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def allocate_port(
    host: str = "127.0.0.1",
    *,
    requested: int | None = None,
    instance: int = 0,
    attempts: int = 100,
) -> int:
    """Resolve the concrete port to bind. Raises OSError if a pinned port is taken or none is free."""
    if requested is not None:
        if requested == 0:
            return _os_assigned_port(host)  # resolve to a concrete free port (usable URL)
        if not _is_free(host, requested):
            raise OSError(f"requested dashboard port {requested} is already in use")
        return requested
    start = BASE_PORT + instance * _INSTANCE_STRIDE
    for port in range(start, start + attempts):
        if _is_free(host, port):
            return port
    raise OSError(f"no free dashboard port in {start}..{start + attempts}")
