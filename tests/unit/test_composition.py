"""Phase 3c — composition root: the single wiring point shared by the CLI and MCP surfaces.

These are pure-ish wiring assertions (no git needed): RepositoryRegistry.resolve and the assess
adapters work in a plain temp dir, exactly as the worked-example golden runs in an empty cwd.
"""

from __future__ import annotations

from pebra import composition
from pebra.core import candidate_parser


def test_resolve_defaults_db_under_dot_pebra(tmp_path) -> None:
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        assert ctx.db_path.endswith("pebra.db")
        assert ".pebra" in ctx.db_path
        assert ctx.repo.repo_root
    finally:
        ctx.store.close()


def test_resolve_honors_explicit_db(tmp_path) -> None:
    db = str(tmp_path / "custom.db")
    ctx = composition.resolve_repo_and_db(str(tmp_path), db)
    try:
        assert ctx.db_path == db
    finally:
        ctx.store.close()


def test_build_assess_ports_has_the_controller_keys(tmp_path) -> None:
    req = candidate_parser.parse({"task": "t", "candidate_actions": [{"id": "a1"}]})
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        ports = composition.build_assess_ports(req, ctx)
    finally:
        ctx.store.close()
    assert set(ports) >= {
        "evidence_provider", "symbol_diff_provider", "blast_provider",
        "sanction_port", "repository_registry", "store", "assessed_commit",
        "codegraph_provider",
    }


def test_build_verify_ports_has_the_controller_keys() -> None:
    ports = composition.build_verify_ports()
    assert set(ports) == {"change_verifier", "contract_surface"}
