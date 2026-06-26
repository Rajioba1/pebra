"""Phase 3b/5c-B — Risk Observatory auth/CSP primitives and port allocation."""

from __future__ import annotations

import socket

import pytest

from pebra.dashboard import auth, ports


def test_token_is_unique_and_urlsafe() -> None:
    t1, t2 = auth.generate_token(), auth.generate_token()
    assert t1 != t2
    assert all(c.isalnum() or c in "-_" for c in t1)


def test_nonce_is_unique() -> None:
    assert auth.create_nonce() != auth.create_nonce()


def test_csp_carries_the_nonce_and_locks_defaults() -> None:
    nonce = "abc123"
    csp = auth.build_csp(nonce)
    assert f"script-src 'nonce-{nonce}'" in csp
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "style-src 'self'" in csp
    assert "'unsafe-inline'" not in csp  # no inline styles -> no CSS-exfil gap


def test_token_matches_constant_time() -> None:
    tok = auth.generate_token()
    assert auth.token_matches(tok, tok) is True
    assert auth.token_matches("wrong", tok) is False
    assert auth.token_matches(None, tok) is False  # missing header -> no crash, no match


def _ipv6_loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


def test_allocate_requested_zero_resolves_to_concrete_port() -> None:
    # 0 must resolve to a real OS-assigned port (not literal 0) so the printed URL is usable
    port = ports.allocate_port(requested=0)
    assert port > 0


@pytest.mark.skipif(not _ipv6_loopback_available(), reason="no IPv6 loopback")
def test_allocate_ipv6_host() -> None:
    assert ports.allocate_port(host="::1") >= ports.BASE_PORT
    assert ports.allocate_port(host="::1", requested=0) > 0


def test_allocate_requested_in_use_raises() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        taken = occupied.getsockname()[1]
        with pytest.raises(OSError):
            ports.allocate_port(requested=taken)


def test_allocate_default_returns_free_port_at_or_above_base() -> None:
    port = ports.allocate_port()
    assert port >= ports.BASE_PORT


def test_allocate_instance_offsets_the_base() -> None:
    port = ports.allocate_port(instance=1)
    assert port >= ports.BASE_PORT + 100
