"""The observatory dev server: stdlib http.server routes serving the aggregate JSON + static shell.

Spun on port 0 (OS-assigned) so tests never collide. The read-only regression test is the guard that
the server can NEVER write into a run dir (which would corrupt the crash-survivable resume).
"""

from __future__ import annotations

import dataclasses
import json
import threading
import urllib.request

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.runners.observatory import server as server_mod


def _oc(task_id, arm, seed):
    return models.RunOutcome(
        task_id=task_id, arm=arm, seed=seed, harm_label="risky", harm_materialized=False,
        task_completed=True, over_cautious=False, quality_failure=False, scope_drift=False,
        build_failed=False, test_failed=False, edit_cycle_count=1, advisory_called=False,
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_DID_NOT_CALL,
        blinding_leak=False, blinding_terms=(), timed_out=False,
    )


def _write_run(ab_out, run_id, outcomes):
    run_dir = ab_out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "outcomes": [dataclasses.asdict(o) for o in outcomes]}
    (run_dir / "outcomes.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback only
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _serving(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def test_api_runs_and_run_view(tmp_path):
    _write_run(tmp_path, "r1", [_oc("T1", models.ARM_CONTROL, 0), _oc("T1", models.ARM_TREATMENT, 0)])
    server = server_mod.build_server(ab_out=tmp_path, port=0)
    t = _serving(server)
    try:
        port = server.server_address[1]
        status, body = _get(port, "/api/runs")
        assert status == 200
        assert any(r["run_id"] == "r1" for r in json.loads(body)["runs"])

        status, body = _get(port, "/api/run/r1")
        assert status == 200
        assert json.loads(body)["run_id"] == "r1"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_unknown_run_is_404_and_bad_id_is_400(tmp_path):
    server = server_mod.build_server(ab_out=tmp_path, port=0)
    t = _serving(server)
    try:
        port = server.server_address[1]
        assert _get(port, "/api/run/nope")[0] == 404
        assert _get(port, "/api/run/..%2Fescape")[0] == 400
        # dot run-ids pass the bare regex but resolve to ab_out itself / its parent -> must be 400,
        # not an uncaught crash (%2E == '.', %2E%2E == '..'; encoded so the client can't normalize them).
        assert _get(port, "/api/run/%2E")[0] == 400
        assert _get(port, "/api/run/%2E%2E")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_index_html_served_with_csp(tmp_path):
    server = server_mod.build_server(ab_out=tmp_path, port=0)
    t = _serving(server)
    try:
        port = server.server_address[1]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback only
            assert resp.status == 200
            assert "text/html" in resp.headers.get("Content-Type", "")
            assert resp.headers.get("Content-Security-Policy")  # dev-tool hardening
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_favicon_is_204_not_404(tmp_path):
    server = server_mod.build_server(ab_out=tmp_path, port=0)
    t = _serving(server)
    try:
        assert _get(server.server_address[1], "/favicon.ico")[0] == 204
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_server_never_writes_into_run_dir(tmp_path):
    run_dir = _write_run(tmp_path, "r1", [_oc("T1", models.ARM_CONTROL, 0),
                                          _oc("T1", models.ARM_TREATMENT, 0)])
    before = {p: p.stat().st_mtime_ns for p in run_dir.rglob("*")}
    server = server_mod.build_server(ab_out=tmp_path, port=0)
    t = _serving(server)
    try:
        port = server.server_address[1]
        _get(port, "/api/runs")
        _get(port, "/api/run/r1")
        _get(port, "/api/run/r1")  # repeated poll
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)
    after = {p: p.stat().st_mtime_ns for p in run_dir.rglob("*")}
    assert before == after  # observatory is strictly read-only over the run dir
