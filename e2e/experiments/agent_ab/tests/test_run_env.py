from __future__ import annotations

from e2e.experiments.agent_ab.runners import run_env


def test_live_env_sets_non_secret_run_gates(tmp_path):
    repo = tmp_path / "avalonia_template"
    repo.mkdir()
    env = run_env.live_env({"ANTHROPIC_API_KEY": "sk-test"}, repo_root=tmp_path / "pebra")
    assert env["E2E_AB_RUN"] == "1"
    assert env["E2E_EXTERNAL"] == "1"
    assert env["E2E_TEMPLATE_BLUEPRINT_REPO"] == str(repo)


def test_live_env_preserves_explicit_repo_path(tmp_path):
    explicit = tmp_path / "my_repo"
    env = run_env.live_env({"E2E_TEMPLATE_BLUEPRINT_REPO": str(explicit)}, repo_root=tmp_path / "pebra")
    assert env["E2E_TEMPLATE_BLUEPRINT_REPO"] == str(explicit)


def test_live_env_reads_gitignored_local_key_file(tmp_path):
    repo_root = tmp_path / "pebra"
    secret_dir = repo_root / ".pebra"
    secret_dir.mkdir(parents=True)
    (secret_dir / "agent_ab.env").write_text(
        "# local only\nANTHROPIC_API_KEY=sk-test-from-file\n",
        encoding="utf-8",
    )

    env = run_env.live_env({}, repo_root=repo_root)

    assert env["ANTHROPIC_API_KEY"] == "sk-test-from-file"


def test_live_env_prefers_process_key_over_local_file(tmp_path):
    repo_root = tmp_path / "pebra"
    secret_dir = repo_root / ".pebra"
    secret_dir.mkdir(parents=True)
    (secret_dir / "agent_ab.env").write_text("ANTHROPIC_API_KEY=sk-test-from-file\n", encoding="utf-8")

    env = run_env.live_env({"ANTHROPIC_API_KEY": "sk-test-process"}, repo_root=repo_root)

    assert env["ANTHROPIC_API_KEY"] == "sk-test-process"


def test_live_env_reads_model_override_from_local_file(tmp_path):
    repo_root = tmp_path / "pebra"
    secret_dir = repo_root / ".pebra"
    secret_dir.mkdir(parents=True)
    (secret_dir / "agent_ab.env").write_text(
        "ANTHROPIC_API_KEY=sk-test\nE2E_AB_MODEL=claude-haiku-4-5-20251001\n",
        encoding="utf-8",
    )

    env = run_env.live_env({}, repo_root=repo_root)

    assert env["E2E_AB_MODEL"] == "claude-haiku-4-5-20251001"


def test_missing_for_live_env_only_requires_secret_and_repo_path():
    missing = run_env.missing_for_live_env({"E2E_AB_RUN": "1", "E2E_EXTERNAL": "1"})
    assert missing == ["ANTHROPIC_API_KEY=<key>", "E2E_TEMPLATE_BLUEPRINT_REPO=<path>"]
