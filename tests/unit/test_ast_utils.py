"""Shared AST import resolver (adapters/_ast_utils.py) — used by both the architecture map and the
blast-radius walker so import-graph resolution is consistent. Handles absolute + relative imports and
classifies edge kind (static/relative/wildcard/dynamic) for edge-confidence scoring."""

from __future__ import annotations

from pebra.adapters._ast_utils import iter_import_edges, python_files, source_is_entrypoint


def _kinds_targets(edges):
    return {(e.kind, e.target) for e in edges}


def test_absolute_import_resolved() -> None:
    edges = iter_import_edges("a.py", "import b\n", {"a.py", "b.py"})
    assert _kinds_targets(edges) == {("static", "b.py")}


def test_external_unresolved_import_is_classified_external() -> None:
    # 3c: `import os` points at no repo top-level name -> external (a benign library), NOT a
    # resolution failure. (Was previously lumped in as ("static", None).)
    edges = iter_import_edges("a.py", "import os\n", {"a.py"})
    assert _kinds_targets(edges) == {("external", None)}


def test_from_import_resolves_dotted_module() -> None:
    edges = iter_import_edges("a.py", "from pkg.mod import x\n", {"a.py", "pkg/mod.py"})
    assert ("static", "pkg/mod.py") in _kinds_targets(edges)


def test_from_import_resolves_package_submodule_alias() -> None:
    edges = iter_import_edges(
        "a.py",
        "from pkg import core\n",
        {"a.py", "pkg/__init__.py", "pkg/core.py"},
    )
    assert ("static", "pkg/core.py") in _kinds_targets(edges)


def test_wildcard_import_is_classified_wildcard() -> None:
    edges = iter_import_edges("a.py", "from b import *\n", {"a.py", "b.py"})
    assert ("wildcard", "b.py") in _kinds_targets(edges)


def test_relative_import_level1_bare_name() -> None:
    edges = iter_import_edges("pkg/a.py", "from . import b\n", {"pkg/a.py", "pkg/b.py"})
    assert ("relative", "pkg/b.py") in _kinds_targets(edges)


def test_relative_import_level1_with_module() -> None:
    edges = iter_import_edges("pkg/a.py", "from .util import x\n", {"pkg/a.py", "pkg/util.py"})
    assert ("relative", "pkg/util.py") in _kinds_targets(edges)


def test_relative_import_level2_parent_package() -> None:
    edges = iter_import_edges("pkg/sub/c.py", "from ..mod import x\n", {"pkg/sub/c.py", "pkg/mod.py"})
    assert ("relative", "pkg/mod.py") in _kinds_targets(edges)


def test_dynamic_importlib_call_is_classified_dynamic() -> None:
    src = "import importlib\nm = importlib.import_module('x')\n"
    edges = iter_import_edges("a.py", src, {"a.py"})
    assert any(e.kind == "dynamic" for e in edges)


def test_syntax_error_yields_no_edges() -> None:
    assert iter_import_edges("a.py", "def broken(:\n", {"a.py"}) == []


def test_from_package_import_resolves_to_submodule_not_init() -> None:
    # `from pkg import core` must resolve to pkg/core.py (the submodule), not pkg/__init__.py
    edges = iter_import_edges(
        "a.py", "from pkg import core\n", {"a.py", "pkg/__init__.py", "pkg/core.py"}
    )
    assert ("static", "pkg/core.py") in _kinds_targets(edges)


def test_from_package_import_falls_back_to_init_for_a_name() -> None:
    # `from pkg import thing` where thing is a name in __init__ (no pkg/thing.py) -> pkg/__init__.py
    edges = iter_import_edges("a.py", "from pkg import thing\n", {"a.py", "pkg/__init__.py"})
    assert ("static", "pkg/__init__.py") in _kinds_targets(edges)


def test_relative_from_subpackage_import_submodule() -> None:
    edges = iter_import_edges(
        "pkg/a.py", "from .sub import core\n", {"pkg/a.py", "pkg/sub/__init__.py", "pkg/sub/core.py"}
    )
    assert ("relative", "pkg/sub/core.py") in _kinds_targets(edges)


# --- 3b: resolver completeness ---

def test_pyi_stub_resolves_when_no_py_present() -> None:
    # a module that ships only a .pyi stub still resolves (in-degree shouldn't silently drop it)
    edges = iter_import_edges("a.py", "import b\n", {"a.py", "b.pyi"})
    assert ("static", "b.pyi") in _kinds_targets(edges)


def test_py_preferred_over_pyi_when_both_present() -> None:
    # the real module wins over its stub — never resolve to the .pyi when the .py exists
    edges = iter_import_edges("a.py", "import b\n", {"a.py", "b.py", "b.pyi"})
    assert ("static", "b.py") in _kinds_targets(edges)
    assert ("static", "b.pyi") not in _kinds_targets(edges)


def test_pyi_package_init_resolves() -> None:
    edges = iter_import_edges("a.py", "import pkg\n", {"a.py", "pkg/__init__.pyi"})
    assert ("static", "pkg/__init__.pyi") in _kinds_targets(edges)


def test_import_alias_resolves_to_real_module() -> None:
    # `import b as c` — the alias is a local binding; the target is still b.py
    edges = iter_import_edges("a.py", "import b as c\n", {"a.py", "b.py"})
    assert ("static", "b.py") in _kinds_targets(edges)


def test_from_import_alias_resolves_to_submodule() -> None:
    edges = iter_import_edges(
        "a.py", "from pkg import core as c\n", {"a.py", "pkg/__init__.py", "pkg/core.py"}
    )
    assert ("static", "pkg/core.py") in _kinds_targets(edges)


def test_relative_level3_grandparent_package() -> None:
    edges = iter_import_edges(
        "pkg/sub/deep/m.py", "from ... import x\n", {"pkg/sub/deep/m.py", "pkg/x.py"}
    )
    assert ("relative", "pkg/x.py") in _kinds_targets(edges)


def test_overdeep_relative_import_is_unresolved_not_leaked_to_toplevel() -> None:
    # `from .... import x` from pkg/a.py escapes the repo root: it must NOT resolve to top-level x.py.
    edges = iter_import_edges("pkg/a.py", "from .... import x\n", {"pkg/a.py", "x.py"})
    assert ("relative", "x.py") not in _kinds_targets(edges)
    assert ("relative", None) in _kinds_targets(edges)


def test_python_files_includes_pyi_stub(tmp_path) -> None:
    (tmp_path / "b.pyi").write_text("x: int\n", encoding="utf-8")
    assert "b.pyi" in python_files(tmp_path)


def test_python_files_excludes_pyi_shadowed_by_py_sibling(tmp_path) -> None:
    (tmp_path / "b.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.pyi").write_text("x: int\n", encoding="utf-8")
    files = python_files(tmp_path)
    assert "b.py" in files
    assert "b.pyi" not in files  # the real module shadows its stub


# --- 3c-1: external vs internal-unresolved classification ---

def test_internal_unresolved_import_stays_static() -> None:
    # `import pkg.missing` where pkg/ exists but the submodule doesn't -> a real resolution FAILURE
    # (internal), kept as ("static", None) so 3c can count it as incompleteness.
    edges = iter_import_edges("m.py", "import pkg.missing\n", {"m.py", "pkg/__init__.py"})
    assert ("static", None) in _kinds_targets(edges)
    assert ("external", None) not in _kinds_targets(edges)


def test_from_external_package_import_is_external() -> None:
    edges = iter_import_edges("m.py", "from requests import get\n", {"m.py"})
    assert ("external", None) in _kinds_targets(edges)


def test_from_internal_missing_submodule_stays_static() -> None:
    # `from pkg import missing` where pkg exists but neither pkg/missing.py nor the name resolves;
    # pkg is a repo top-level so this is an internal failure, not external.
    edges = iter_import_edges("m.py", "from pkg import missing\n", {"m.py", "pkg/__init__.py"})
    # falls back to pkg/__init__.py (the name may live there) -> resolved, not unresolved
    assert ("static", "pkg/__init__.py") in _kinds_targets(edges)


def test_from_internal_unresolved_top_level_stays_static() -> None:
    # `from pkg.gone import x` where pkg exists but pkg/gone is absent and pkg has no __init__ to
    # fall back to -> internal unresolved, classified static (not external).
    edges = iter_import_edges("m.py", "from pkg.gone import x\n", {"m.py", "pkg/core.py"})
    assert ("static", None) in _kinds_targets(edges)
    assert ("external", None) not in _kinds_targets(edges)


def test_relative_unresolved_is_relative_never_external() -> None:
    edges = iter_import_edges("pkg/a.py", "from . import missing\n", {"pkg/a.py"})
    assert ("relative", None) in _kinds_targets(edges)
    assert ("external", None) not in _kinds_targets(edges)


# --- 3d: capture the import target NAME on unresolved/dynamic edges (for model guidance) ---

def test_external_import_captures_module_name() -> None:
    edges = iter_import_edges("m.py", "import billing.legacy\n", {"m.py"})
    ext = next(e for e in edges if e.kind == "external")
    assert ext.name == "billing.legacy"


def test_internal_unresolved_from_import_captures_dotted_name() -> None:
    edges = iter_import_edges("m.py", "from pkg import missing\n", {"m.py", "pkg/core.py"})
    bad = next(e for e in edges if e.kind == "static" and e.target is None)
    assert bad.name == "pkg.missing"


def test_relative_unresolved_captures_dotted_name() -> None:
    edges = iter_import_edges("pkg/a.py", "from .sub import gone\n", {"pkg/a.py"})
    rel = next(e for e in edges if e.kind == "relative" and e.target is None)
    assert rel.name == ".sub.gone"


def test_dynamic_import_captures_literal_name() -> None:
    src = "import importlib\nimportlib.import_module('plugins.x')\n"
    edges = iter_import_edges("m.py", src, {"m.py"})
    dyn = next(e for e in edges if e.kind == "dynamic")
    assert dyn.name == "plugins.x"


def test_dynamic_import_without_literal_has_no_name() -> None:
    src = "import importlib\nname = 'x'\nimportlib.import_module(name)\n"
    edges = iter_import_edges("m.py", src, {"m.py"})
    dyn = next(e for e in edges if e.kind == "dynamic")
    assert dyn.name is None


# --- 3e: framework/entrypoint decorator detection ---

def test_flask_route_is_entrypoint() -> None:
    assert source_is_entrypoint("@app.route('/x')\ndef h():\n    pass\n")


def test_fastapi_router_method_is_entrypoint() -> None:
    assert source_is_entrypoint("@router.get('/x')\ndef h():\n    pass\n")


def test_async_route_is_entrypoint() -> None:
    assert source_is_entrypoint("@router.post('/x')\nasync def h():\n    pass\n")


def test_click_command_is_entrypoint() -> None:
    assert source_is_entrypoint("@click.command()\ndef cli():\n    pass\n")


def test_typer_command_is_entrypoint() -> None:
    assert source_is_entrypoint("@app.command()\ndef cli():\n    pass\n")


def test_mcp_tool_is_entrypoint() -> None:
    assert source_is_entrypoint("@mcp.tool()\ndef t():\n    pass\n")


def test_bare_tool_decorator_is_entrypoint() -> None:
    assert source_is_entrypoint("@tool\ndef t():\n    pass\n")


def test_pytest_fixture_is_entrypoint() -> None:
    assert source_is_entrypoint("@pytest.fixture\ndef f():\n    pass\n")


def test_test_function_name_is_entrypoint() -> None:
    assert source_is_entrypoint("def test_login():\n    assert True\n")


def test_plain_function_is_not_entrypoint() -> None:
    assert not source_is_entrypoint("def helper():\n    return 1\n")


def test_property_and_staticmethod_are_not_entrypoints() -> None:
    src = "class C:\n    @property\n    def x(self):\n        return 1\n"
    assert not source_is_entrypoint(src)


def test_unparseable_source_is_not_entrypoint() -> None:
    assert not source_is_entrypoint("def broken(:\n")


def test_bare_http_verb_decorator_is_not_entrypoint() -> None:
    # `@get` alone (a custom decorator named get) must NOT be treated as a web route — too broad.
    assert not source_is_entrypoint("@get\ndef f():\n    return 1\n")


def test_qualified_http_verb_decorator_is_entrypoint() -> None:
    # `@routes.get(...)` is unambiguously a route -> entrypoint.
    assert source_is_entrypoint("@routes.get('/x')\ndef f():\n    pass\n")


def test_bare_specific_decorator_still_entrypoint() -> None:
    # specific leaves (tool/command/fixture/route) stay bare-OK; only HTTP verbs need qualification.
    assert source_is_entrypoint("@command\ndef cli():\n    pass\n")


def test_python_files_excludes_init_pyi_shadowed_by_init_py(tmp_path) -> None:
    # the dedup must also hold at package-init level: __init__.py shadows __init__.pyi
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.pyi").write_text("x: int\n", encoding="utf-8")
    files = python_files(tmp_path)
    assert "pkg/__init__.py" in files
    assert "pkg/__init__.pyi" not in files
