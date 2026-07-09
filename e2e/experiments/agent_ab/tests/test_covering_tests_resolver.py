"""P4 — covering_tests_resolver: real SQL over a fixture CodeGraph DB + real .csproj on disk.

Verifies the graph caller-query + PATH heuristic finds a test project that references the edited owner,
returns (None, None) when nothing test-like calls it, and — structurally — never reads a TaskSpec (its
signature has no spec parameter, so it CANNOT leak the hidden evaluator grading fields)."""

from __future__ import annotations

import inspect
import sqlite3

from e2e.experiments.agent_ab.tools import covering_tests_resolver as ctr


def _make_db(repo, *, with_test_caller=True):
    cg = repo / ".codegraph"
    cg.mkdir(parents=True)
    con = sqlite3.connect(str(cg / "codegraph.db"))
    con.executescript(
        "CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT);"
        "CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, target TEXT, kind TEXT);"
    )
    # the edited owner (in the target file) + a test method that calls it
    con.execute("INSERT INTO nodes VALUES ('own', 'method', 'M', 'Ns.A.M', 'src/A.cs')")
    con.execute("INSERT INTO nodes VALUES ('other', 'method', 'N', 'Ns.B.N', 'src/B.cs')  ")
    if with_test_caller:
        con.execute(
            "INSERT INTO nodes VALUES ('t', 'method', 'T', 'Ns.ATests.T', 'src/A.Tests/ATests.cs')")
        con.execute("INSERT INTO edges (source, target, kind) VALUES ('t', 'own', 'calls')")
    # a non-test caller (must be ignored by the PATH heuristic)
    con.execute("INSERT INTO edges (source, target, kind) VALUES ('other', 'own', 'calls')")
    con.commit()
    con.close()


def _seed_csproj(repo):
    proj = repo / "src" / "A.Tests"
    proj.mkdir(parents=True)
    (proj / "A.Tests.csproj").write_text("<Project/>", encoding="utf-8")
    (proj / "ATests.cs").write_text("// tests", encoding="utf-8")


def test_finds_test_project_that_references_the_edited_owner(tmp_path):
    _make_db(tmp_path)
    _seed_csproj(tmp_path)
    project, test_filter = ctr.find_covering_tests(tmp_path, "src/A.cs", "diff x")
    assert project == "src/A.Tests/A.Tests.csproj"
    assert test_filter is None  # whole project, no filter


def test_javascript_returns_public_test_file_not_csproj(tmp_path):
    _make_db(tmp_path)
    project, test_filter = ctr.find_covering_tests(
        tmp_path, "src/A.cs", "diff x", language="typescript",
    )
    assert project == "src/A.Tests/ATests.cs"
    assert test_filter is None


def test_no_test_caller_returns_none(tmp_path):
    _make_db(tmp_path, with_test_caller=False)
    assert ctr.find_covering_tests(tmp_path, "src/A.cs", "diff x") == (None, None)


def test_absent_graph_returns_none(tmp_path):
    assert ctr.find_covering_tests(tmp_path, "src/A.cs", "diff x") == (None, None)


def test_resolver_cannot_receive_a_taskspec_by_construction():
    # NON-CONTAMINATION is structural: the function accepts only repo_path/target_file/patch_text, so it
    # has no path to the hidden evaluator_test_project/filter grading fields.
    params = set(inspect.signature(ctr.find_covering_tests).parameters)
    assert params == {"repo_path", "target_file", "patch_text", "language"}
    assert "spec" not in params
