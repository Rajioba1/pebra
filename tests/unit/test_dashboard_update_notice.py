"""Dashboard renders cached update state passively."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from pebra.dashboard import auth  # noqa: E402
from pebra.dashboard.server import create_app  # noqa: E402


def test_dashboard_renders_update_chip_without_csp_change(tmp_path) -> None:
    app = create_app(str(tmp_path / "pebra.db"), None, update_notice="PEBRA 0.3.0 is available")

    response = TestClient(app).get("/", headers={"host": "127.0.0.1"})

    assert response.status_code == 200
    assert "PEBRA 0.3.0 is available" in response.text
    assert response.headers["Content-Security-Policy"] == auth.build_csp(
        response.text.split("nonce=\"", 1)[1].split("\"", 1)[0]
    )
