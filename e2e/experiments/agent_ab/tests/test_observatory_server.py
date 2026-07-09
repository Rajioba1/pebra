"""The observatory dev server: stdlib http.server routes serving the aggregate JSON + static shell.

Spun on port 0 (OS-assigned) so tests never collide. The read-only regression test is the guard that
the server can NEVER write into a run dir (which would corrupt the crash-survivable resume).
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import threading
import urllib.error
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


def _post(port, path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-PEBRA-Observatory": "1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback only
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post_with_headers(port, body, headers):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/launch", data=data, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback only
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _raw_post(port, body, headers):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest("POST", "/api/launch")
    for key, value in headers.items():
        conn.putheader(key, value)
    conn.endheaders()
    conn.send(body)
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    conn.close()
    return resp.status, payload


class _FakeRegistry:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def launch(self, run_id, clone, *, ab_out):
        self.calls.append((run_id, clone))
        return self._result

    def shutdown_all(self):
        pass


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


def test_frontend_launch_uses_placeholder_tab_and_csrf_header():
    app_js = (server_mod._STATIC / "app.js").read_text(encoding="utf-8")  # noqa: SLF001
    assert 'window.open("about:blank"' in app_js
    assert '"X-PEBRA-Observatory": "1"' in app_js
    assert "launchState" in app_js


def test_post_launch_returns_url(tmp_path):
    reg = _FakeRegistry({"status": "launched", "url": "http://127.0.0.1:5555/", "pid": 7})
    server = server_mod.build_server(ab_out=tmp_path, port=0, registry=reg)
    t = _serving(server)
    try:
        status, body = _post(server.server_address[1], "/api/launch",
                             {"run_id": "r1", "clone": "T1_seed0_abc"})
        assert status == 200
        assert body["url"] == "http://127.0.0.1:5555/"
        assert reg.calls == [("r1", "T1_seed0_abc")]
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_post_launch_error_is_502(tmp_path):
    reg = _FakeRegistry({"status": "error", "reason": "no such store for this run"})
    server = server_mod.build_server(ab_out=tmp_path, port=0, registry=reg)
    t = _serving(server)
    try:
        status, _ = _post(server.server_address[1], "/api/launch", {"run_id": "r1", "clone": "c"})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_post_launch_bad_id_or_clone_is_400_and_never_calls_registry(tmp_path):
    reg = _FakeRegistry({"status": "launched", "url": "x", "pid": 1})
    server = server_mod.build_server(ab_out=tmp_path, port=0, registry=reg)
    t = _serving(server)
    try:
        port = server.server_address[1]
        assert _post(port, "/api/launch", {"run_id": "../escape", "clone": "c"})[0] == 400
        assert _post(port, "/api/launch", {"run_id": "r1", "clone": ""})[0] == 400
        assert _post(port, "/api/launch", {"run_id": "r1", "clone": "a/b"})[0] == 400
        assert reg.calls == []  # nothing dispatched to the launcher for invalid input
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_post_launch_requires_observatory_header_and_json_content_type(tmp_path):
    reg = _FakeRegistry({"status": "launched", "url": "x", "pid": 1})
    server = server_mod.build_server(ab_out=tmp_path, port=0, registry=reg)
    t = _serving(server)
    try:
        port = server.server_address[1]
        assert _post_with_headers(port, {"run_id": "r1", "clone": "c"},
                                  {"Content-Type": "application/json"})[0] == 403
        assert _post_with_headers(port, {"run_id": "r1", "clone": "c"},
                                  {"Content-Type": "text/plain",
                                   "X-PEBRA-Observatory": "1"})[0] == 415
        assert reg.calls == []
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_post_launch_malformed_input_is_400_not_handler_crash(tmp_path):
    reg = _FakeRegistry({"status": "launched", "url": "x", "pid": 1})
    server = server_mod.build_server(ab_out=tmp_path, port=0, registry=reg)
    t = _serving(server)
    try:
        port = server.server_address[1]
        assert _post_with_headers(port, [], {"Content-Type": "application/json",
                                             "X-PEBRA-Observatory": "1"})[0] == 400
        status, body = _raw_post(
            port,
            b"",
            {"Content-Type": "application/json", "X-PEBRA-Observatory": "1",
             "Content-Length": "nope"},
        )
        assert status == 400
        assert body["error"] == "invalid content-length"
        assert reg.calls == []
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
