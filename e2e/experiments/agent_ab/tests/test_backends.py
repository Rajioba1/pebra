"""TDD for the build/test backend dispatch: one language-keyed seam over dotnet_harness / node_harness.

The corpus only declares a ``language``; the actual build/test invocation is a FIXED profile inside the
backend, never caller-supplied. These tests inject fake harnesses so no real dotnet/node runs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from e2e.experiments.agent_ab import backends
from e2e.experiments.agent_ab.models import TaskSpec


def _spec(language="csharp", build_solution="MyApp.sln", harness_id=None):
    harness_id = harness_id or ("node" if language in {"javascript", "typescript"} else "dotnet")
    return TaskSpec(
        task_id="X", description="d", target_hints=(), harm_label="safe",
        expected_edit_scope=("a",), harm_type="none", oracle_build_must_fail=False,
        build_solution=build_solution, language=language, harness_id=harness_id,
    )


def _res(**kw):
    base = dict(available=True, ran=True, passed=True, exit_code=0, error_summary="", duration_seconds=0.0)
    return SimpleNamespace(**{**base, **kw})


class _FakeDn:
    def __init__(self):
        self.calls = []

    def run_build(self, root, *, sln):
        self.calls.append(("build", sln))
        return _res()

    def run_build_delta(self, root, *, sln, baseline_keys=None):
        self.calls.append(("delta", sln, baseline_keys))
        return _res()

    def run_tests(self, root, *, sln, project=None, test_filter=None):
        self.calls.append(("test", sln, project, test_filter))
        return _res(tests_selected=1)


class _FakeNh:
    def __init__(self):
        self.calls = []

    def run_build(self, root, *, profile="default", selector=None):
        self.calls.append(("build", profile, selector))
        return _res()

    def run_tests(self, root, *, test_path=None, test_filter=None):
        self.calls.append(("test", test_path, test_filter))
        return _res(tests_selected=1)


def test_csharp_backend_uses_dotnet_with_the_spec_solution():
    dn = _FakeDn()
    b = backends.get_backend("dotnet", harness=dn)
    assert b.language == "csharp"
    b.run_build("/r", _spec(build_solution="Foo.sln"))
    b.run_build_delta("/r", _spec(build_solution="Foo.sln"), baseline_keys=frozenset())
    b.run_tests("/r", _spec(build_solution="Foo.sln"), project="p.csproj", test_filter="F~T")
    assert dn.calls == [
        ("build", "Foo.sln"),
        ("delta", "Foo.sln", frozenset()),
        ("test", "Foo.sln", "p.csproj", "F~T"),
    ]


def test_javascript_backend_uses_node_and_maps_project_to_test_path():
    nh = _FakeNh()
    b = backends.get_backend("node", harness=nh)
    assert b.language == "javascript"
    b.run_build("/r", _spec(language="javascript"))
    b.run_tests("/r", _spec(language="javascript"), project="src/a.test.ts", test_filter="handles x")
    assert nh.calls == [("build", "default", None), ("test", "src/a.test.ts", "handles x")]


def test_javascript_backend_uses_public_spec_test_selector_by_default():
    nh = _FakeNh()
    b = backends.get_backend("node", harness=nh)
    spec = _spec(language="typescript")
    object.__setattr__(spec, "test_selector", "src/public.test.ts")

    b.run_tests("/r", spec)

    assert nh.calls == [("test", "src/public.test.ts", None)]


def test_javascript_backend_forwards_build_profile_and_selector():
    nh = _FakeNh()
    b = backends.get_backend("javascript", harness=nh)
    spec = _spec(language="typescript")
    object.__setattr__(spec, "build_profile", "zshy")
    object.__setattr__(spec, "build_selector", "zod:tsconfig.build.json")
    b.run_build("/r", spec)
    assert nh.calls == [("build", "zshy", "zod:tsconfig.build.json")]


def test_typescript_routes_to_the_javascript_backend():
    assert backends.backend_for_spec(_spec(language="typescript"), harness=_FakeNh()).language in (
        "javascript", "typescript",
    )


def test_unknown_language_raises():
    with pytest.raises(ValueError, match="backend"):
        backends.get_backend("cobol")


def test_backend_for_spec_dispatches_on_language():
    assert backends.backend_for_spec(_spec(language="csharp"), harness=_FakeDn()).language == "csharp"


def test_backend_for_spec_rejects_language_harness_mismatch():
    with pytest.raises(ValueError, match="requires harness"):
        backends.backend_for_spec(_spec(language="typescript", harness_id="dotnet"))
