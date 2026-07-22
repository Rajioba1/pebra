from __future__ import annotations

import ast
import json
import tarfile
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pebra.cli import agent_init
from scripts import verify_distribution as distribution_verifier

from scripts.verify_distribution import (
    DistributionVerificationError,
    _EXPECTED_AGENT_HOSTS,
    _validate_agent_host_registry,
    _validate_agent_init_check,
    _verify_installed_cli,
    _verify_tui_mount,
    release_version_from_tag,
    verify_candidate_manifest,
    verify_checksums,
    verify_index_digests,
    verify_archives,
    write_candidate_manifest,
    write_checksums,
)


_ASSETS = (
    "pebra/dashboard/templates/index.html",
    "pebra/dashboard/static/app.js",
    "pebra/dashboard/static/style.css",
    "pebra/dashboard/static/vendor/uplot.iife.min.js",
    "pebra/dashboard/static/vendor/uplot.min.css",
    "pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    "pebra/tui/theme.tcss",
)


def _agent_check_payload(target: str) -> dict[str, object]:
    spec = _EXPECTED_AGENT_HOSTS[target]
    return {
        "command": "agent-init",
        "target": target,
        "protocol_version": 4,
        "gate_schema_version": 2,
        "files": [
            {"path": path, "state": "current"}
            for path in (*spec["instruction_paths"], spec["skill_path"])
        ],
        "hook": {"path": spec["hook_path"], "state": "exact"},
        "declared_support": spec["declared_support"],
        "effective_enforcement": {
            "mode": "degraded_fail_open",
            "candidate_bound": False,
            "reasons": ["graph_unverified_read_only"],
        },
    }


def _write_wheel(
    path: Path,
    *,
    omit: str | None = None,
    prefix: str = "",
) -> None:
    members = [
        *_ASSETS,
        "pebra-0.1.0.dist-info/licenses/LICENSE",
        "pebra-0.1.0.dist-info/licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    ]
    with zipfile.ZipFile(path, "w") as archive:
        for member in members:
            if member != omit:
                archive.writestr(f"{prefix}{member}", "content")


def _write_sdist(
    path: Path,
    *,
    root: str = "pebra-0.1.0",
    extra_directory: str | None = None,
) -> None:
    members = [
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "RELEASING.md",
        "README.md",
        "pyproject.toml",
        *_ASSETS,
    ]
    staging = path.parent / "staging"
    staging.mkdir()
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            source = staging / member
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("content", encoding="utf-8")
            archive.add(source, arcname=f"{root}/{member}")
        if extra_directory is not None:
            info = tarfile.TarInfo(extra_directory)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)


def test_archive_verifier_accepts_complete_wheel_and_sdist(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    _write_wheel(wheel)
    _write_sdist(sdist)

    verify_archives(wheel, sdist)


def test_archive_verifier_names_a_missing_runtime_asset(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    missing = "pebra/dashboard/static/vendor/uplot.LICENSE.txt"
    _write_wheel(wheel, omit=missing)
    _write_sdist(sdist)

    with pytest.raises(DistributionVerificationError, match="uplot.LICENSE.txt"):
        verify_archives(wheel, sdist)


def test_archive_verifier_rejects_wheel_members_under_wrong_prefix(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    _write_wheel(wheel, prefix="wrong-prefix/")
    _write_sdist(sdist)

    with pytest.raises(DistributionVerificationError, match="missing"):
        verify_archives(wheel, sdist)


def test_archive_verifier_rejects_sdist_members_under_nested_root(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    _write_wheel(wheel)
    _write_sdist(sdist, root="pebra-0.1.0/wrong-prefix")

    with pytest.raises(DistributionVerificationError, match="missing"):
        verify_archives(wheel, sdist)


def test_archive_verifier_rejects_backslash_wheel_members(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    members = [
        *_ASSETS,
        "pebra-0.1.0.dist-info/licenses/LICENSE",
        "pebra-0.1.0.dist-info/licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    ]
    with zipfile.ZipFile(wheel, "w") as archive:
        for member in members:
            archive.writestr(member, "content")
    raw_wheel = wheel.read_bytes()
    for member in members:
        raw_wheel = raw_wheel.replace(
            member.encode("ascii"),
            member.replace("/", "\\").encode("ascii"),
        )
    wheel.write_bytes(raw_wheel)
    _write_sdist(sdist)

    with pytest.raises(DistributionVerificationError, match="backslash"):
        verify_archives(wheel, sdist)


def test_archive_verifier_requires_uplot_metadata_license(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    missing = "pebra-0.1.0.dist-info/licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt"
    _write_wheel(wheel, omit=missing)
    _write_sdist(sdist)

    with pytest.raises(DistributionVerificationError, match="uplot.LICENSE.txt"):
        verify_archives(wheel, sdist)


def test_archive_verifier_rejects_backslash_tar_directory(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    _write_wheel(wheel)
    _write_sdist(sdist, extra_directory="pebra-0.1.0/bad\\directory")

    with pytest.raises(DistributionVerificationError, match="backslash"):
        verify_archives(wheel, sdist)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v0.1.0", "0.1.0"), ("0.1.0", "0.1.0"), ("release-0.1.0", None)],
)
def test_release_version_from_tag_is_strict(tag: str, expected: str | None) -> None:
    assert release_version_from_tag(tag) == expected


def test_installed_verifier_exercises_console_script() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "verify_distribution.py").read_text(
        encoding="utf-8"
    )

    assert "shutil.which(\"pebra\")" in source
    assert "installed console script" in source
    assert 'importlib.metadata.version("pebra")' in source
    assert '_run_cli("--version", cwd=cwd)' in source
    assert 'stdout.startswith(f"PEBRA {installed_version} ")' in source
    assert '"agent-init"' in source
    assert '"--check"' in source


def test_installed_cli_verifier_checks_all_explore_help_entry_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_calls: list[tuple[str, ...]] = []
    console_calls: list[tuple[str, ...]] = []

    def completed(*args: str, cwd: Path):
        del cwd
        module_calls.append(args)
        text = "PEBRA 9.8.7 installed" if args == ("--version",) else "usage: pebra explore"
        return SimpleNamespace(returncode=0, stdout=text, stderr="")

    def console(argv, **kwargs):
        del kwargs
        console_calls.append(tuple(argv[1:]))
        return SimpleNamespace(returncode=0, stdout="usage: pebra explore", stderr="")

    monkeypatch.setattr(distribution_verifier, "_run_cli", completed)
    monkeypatch.setattr(distribution_verifier.shutil, "which", lambda _name: "pebra")
    monkeypatch.setattr(distribution_verifier.subprocess, "run", console)

    _verify_installed_cli(tmp_path, installed_version="9.8.7")

    assert ("help", "explore") in module_calls
    assert ("help", "explore") in console_calls
    assert ("explore", "--help") in console_calls
    assert ("help", "dashboard") in module_calls
    assert ("help", "tui") in module_calls


def test_installed_cli_verifier_rejects_missing_explore_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def completed(*args: str, cwd: Path):
        del cwd
        text = "PEBRA 9.8.7 installed" if args == ("--version",) else "pebra"
        return SimpleNamespace(returncode=0, stdout=text, stderr="")

    monkeypatch.setattr(distribution_verifier, "_run_cli", completed)
    monkeypatch.setattr(distribution_verifier.shutil, "which", lambda _name: "pebra")
    monkeypatch.setattr(
        distribution_verifier.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout="pebra", stderr=""),
    )

    with pytest.raises(DistributionVerificationError, match="help explore"):
        _verify_installed_cli(tmp_path, installed_version="9.8.7")


def test_installed_verifier_imports_exploration_contract_without_adapter() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "verify_distribution.py").read_text(
        encoding="utf-8"
    )

    assert "from pebra.core.graph_snapshot import GraphSnapshot" in source
    assert "from pebra.core.exploration import ExplorationResult" in source
    assert "from pebra.ports.repository_explorer_port import RepositoryExplorer" in source
    assert "from pebra.cli.main import build_parser" in source
    imports = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(module.startswith("pebra.adapters.codegraph") for module in imports)


def test_installed_verifier_measures_cli_module_imports_for_eager_adapters() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "verify_distribution.py").read_text(
        encoding="utf-8"
    )

    assert source.index("before_parser = set(sys.modules)") < source.index(
        "from pebra.cli.main import build_parser"
    )
    assert "before parser import" in source
    assert "after parser construction" in source


def test_installed_registry_matches_independent_five_field_oracle() -> None:
    from pebra.core.agent_hosts import AGENT_HOSTS

    _validate_agent_host_registry(AGENT_HOSTS)


@pytest.mark.parametrize("mutation", ("missing", "extra", "drift"))
def test_installed_registry_validator_rejects_missing_extra_and_drift(mutation: str) -> None:
    from pebra.core.agent_hosts import AGENT_HOSTS, HostSpec

    registry = dict(AGENT_HOSTS)
    if mutation == "missing":
        del registry["codex"]
    elif mutation == "extra":
        registry["other"] = HostSpec(
            skill_path="other/SKILL.md",
            instruction_paths=("OTHER.md",),
            hook_path="other/hooks.json",
            hook_matcher="edit",
            declared_support="best_effort",
        )
    else:
        registry["codex"] = replace(registry["codex"], hook_matcher="Write")

    with pytest.raises(DistributionVerificationError, match="agent host registry mismatch"):
        _validate_agent_host_registry(registry)


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_installed_agent_check_validator_accepts_exact_payload(target: str) -> None:
    payload = _agent_check_payload(target)

    assert _validate_agent_init_check(json.dumps(payload), target=target) == payload


@pytest.mark.parametrize("field", ("protocol_version", "gate_schema_version"))
@pytest.mark.parametrize("value", (True, 1.0))
def test_installed_agent_check_validator_requires_integer_version_fields(
    field: str, value: object,
) -> None:
    payload = _agent_check_payload("claude")
    payload[field] = value

    with pytest.raises(DistributionVerificationError, match="protocol mismatch"):
        _validate_agent_init_check(json.dumps(payload), target="claude")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("not-json", "malformed JSON"),
        ("missing-key", "schema mismatch"),
        ("stale-protocol", "protocol mismatch"),
        ("stale-files", "file state mismatch"),
        ("wrong-hook", "hook state mismatch"),
        ("wrong-support", "support mismatch"),
        ("stale-enforcement", "enforcement mismatch"),
        ("malformed-files", "malformed payload"),
    ),
)
def test_installed_agent_check_validator_normalizes_malformed_and_stale_output(
    mutation: str, message: str,
) -> None:
    payload = _agent_check_payload("claude")
    if mutation == "not-json":
        raw = "not json"
    else:
        if mutation == "missing-key":
            del payload["gate_schema_version"]
        elif mutation == "stale-protocol":
            payload["protocol_version"] = 1
        elif mutation == "stale-files":
            payload["files"][0]["state"] = "modified"
        elif mutation == "wrong-hook":
            payload["hook"]["path"] = ".claude/wrong.json"
        elif mutation == "wrong-support":
            payload["declared_support"] = "best_effort"
        elif mutation == "stale-enforcement":
            payload["effective_enforcement"]["candidate_bound"] = True
        else:
            payload["files"] = None
        raw = json.dumps(payload)

    with pytest.raises(DistributionVerificationError, match=message):
        _validate_agent_init_check(raw, target="claude")


def _write_agent_init_artifacts(root: Path, target: str) -> None:
    spec = _EXPECTED_AGENT_HOSTS[target]
    skill = root / str(spec["skill_path"])
    skill.parent.mkdir(parents=True)
    skill.write_text(agent_init._SKILL_MD, encoding="utf-8", newline="")
    if target == "claude":
        rule = root / str(spec["instruction_paths"][0])
        rule.parent.mkdir(parents=True)
        rule.write_text(agent_init._CLAUDE_RULE_MD, encoding="utf-8", newline="")
    else:
        (root / "AGENTS.md").write_text(
            distribution_verifier._CODEX_SENTINEL
            + "\n"
            + agent_init._managed_block()
            + "\n",
            encoding="utf-8",
            newline="",
        )


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_installed_artifact_verifier_accepts_independent_expected_files(
    tmp_path, target,
) -> None:
    _write_agent_init_artifacts(tmp_path, target)

    distribution_verifier._verify_agent_init_artifacts(tmp_path, target)


def test_distribution_oracle_requires_human_review_reassessment_id() -> None:
    assert (
        "After `pebra accept-risk --apply`, use its returned `reassessment_id` for Verify "
        "and Record; never use the original held assessment ID."
        in distribution_verifier._AGENT_SEMANTIC_OBLIGATIONS
    )


def test_distribution_oracle_requires_exact_apply_result_staging() -> None:
    assert (
        "For both apply paths, stage exactly the returned `changed_files` and no other paths "
        "before Verify. Use only `git --literal-pathspecs add -- <changed_file>...`, passing each "
        "returned path as a separate, safely quoted argument. The `--` delimiter alone ends "
        "options but does not make wildcard pathspecs literal; never concatenate or evaluate path "
        "text as shell code, and use no other staging method. Do not run "
        "`pebra verify --scope staged` unless the staged path set exactly equals `changed_files`."
        in distribution_verifier._AGENT_SEMANTIC_OBLIGATIONS
    )


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_installed_artifact_verifier_rejects_skill_byte_drift(tmp_path, target) -> None:
    _write_agent_init_artifacts(tmp_path, target)
    skill = tmp_path / str(_EXPECTED_AGENT_HOSTS[target]["skill_path"])
    skill.write_bytes(skill.read_bytes() + b"\nchanged")

    with pytest.raises(DistributionVerificationError, match="skill bytes"):
        distribution_verifier._verify_agent_init_artifacts(tmp_path, target)


def test_installed_artifact_verifier_checks_skill_semantic_relations(
    tmp_path, monkeypatch,
) -> None:
    _write_agent_init_artifacts(tmp_path, "claude")
    skill = tmp_path / str(_EXPECTED_AGENT_HOSTS["claude"]["skill_path"])
    changed = skill.read_text(encoding="utf-8").replace(
        "Never treat a held candidate as permission to edit.",
        "Treat a held candidate as permission to edit.",
    )
    skill.write_text(changed, encoding="utf-8", newline="")
    monkeypatch.setattr(
        distribution_verifier,
        "_EXPECTED_AGENT_SKILL_SHA256",
        distribution_verifier.hashlib.sha256(skill.read_bytes()).hexdigest(),
    )

    with pytest.raises(DistributionVerificationError, match="semantic obligation"):
        distribution_verifier._verify_agent_init_artifacts(tmp_path, "claude")


def test_installed_artifact_verifier_rejects_provider_specific_agent_instructions(
    tmp_path, monkeypatch,
) -> None:
    _write_agent_init_artifacts(tmp_path, "claude")
    skill = tmp_path / str(_EXPECTED_AGENT_HOSTS["claude"]["skill_path"])
    skill.write_bytes(skill.read_bytes() + b"\nUse CodeGraph directly.\n")
    monkeypatch.setattr(
        distribution_verifier,
        "_EXPECTED_AGENT_SKILL_SHA256",
        distribution_verifier.hashlib.sha256(skill.read_bytes()).hexdigest(),
    )

    with pytest.raises(DistributionVerificationError, match="provider-specific"):
        distribution_verifier._verify_agent_init_artifacts(tmp_path, "claude")


def test_installed_artifact_verifier_checks_claude_rule_obligations(
    tmp_path, monkeypatch,
) -> None:
    _write_agent_init_artifacts(tmp_path, "claude")
    rule = tmp_path / str(_EXPECTED_AGENT_HOSTS["claude"]["instruction_paths"][0])
    rule.write_text("# PEBRA safe-edit non-negotiables\n", encoding="utf-8")
    monkeypatch.setattr(
        distribution_verifier,
        "_EXPECTED_CLAUDE_RULE_SHA256",
        distribution_verifier.hashlib.sha256(rule.read_bytes()).hexdigest(),
    )

    with pytest.raises(DistributionVerificationError, match="Claude rule obligation"):
        distribution_verifier._verify_agent_init_artifacts(tmp_path, "claude")


def test_installed_artifact_verifier_rejects_claude_rule_byte_drift(tmp_path) -> None:
    _write_agent_init_artifacts(tmp_path, "claude")
    rule = tmp_path / str(_EXPECTED_AGENT_HOSTS["claude"]["instruction_paths"][0])
    rule.write_bytes(rule.read_bytes() + b"\n")

    with pytest.raises(DistributionVerificationError, match="Claude rule bytes"):
        distribution_verifier._verify_agent_init_artifacts(tmp_path, "claude")


def test_installed_artifact_verifier_requires_codex_sentinel_outside_managed_block(
    tmp_path,
) -> None:
    _write_agent_init_artifacts(tmp_path, "codex")
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        agent_init._managed_block().replace(
            agent_init._MARK_END,
            distribution_verifier._CODEX_SENTINEL + "\n" + agent_init._MARK_END,
        ),
        encoding="utf-8",
    )

    with pytest.raises(DistributionVerificationError, match="Codex sentinel"):
        distribution_verifier._verify_agent_init_artifacts(tmp_path, "codex")


def test_installed_artifact_verifier_rejects_codex_managed_block_byte_drift(
    tmp_path,
) -> None:
    _write_agent_init_artifacts(tmp_path, "codex")
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(
        agents.read_bytes().replace(
            distribution_verifier._MANAGED_END.encode("utf-8"),
            b"\n" + distribution_verifier._MANAGED_END.encode("utf-8"),
        )
    )

    with pytest.raises(DistributionVerificationError, match="Codex managed block bytes"):
        distribution_verifier._verify_agent_init_artifacts(tmp_path, "codex")


def test_installed_verifier_mounts_tui_headlessly() -> None:
    class RunTestContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeApp:
        mounted = False

        def run_test(self) -> RunTestContext:
            self.mounted = True
            return RunTestContext()

    app = FakeApp()

    _verify_tui_mount(app)

    assert app.mounted is True


def test_installed_verifier_reports_tui_mount_failure() -> None:
    class BrokenApp:
        def run_test(self) -> object:
            raise ValueError("invalid packaged theme")

    with pytest.raises(DistributionVerificationError, match="installed TUI failed to mount"):
        _verify_tui_mount(BrokenApp())


def test_installed_verifier_constructs_tui_app() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "verify_distribution.py").read_text(
        encoding="utf-8"
    )

    assert "from pebra.tui.app import ObservatoryApp" in source
    assert "ObservatoryApp(ObservatoryContext(" in source


def test_codegraph_smoke_commits_fixture_before_setup(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def run_cli(*args: str, cwd: Path, **_kwargs: object) -> SimpleNamespace:
        distribution_verifier.subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(distribution_verifier, "_run_cli", run_cli)

    distribution_verifier.verify_codegraph_setup()

    assert [args[0] for args in calls] == ["setup-graph", "doctor"]


def test_checksums_detect_artifact_tampering(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    manifest = write_checksums(tmp_path)

    verify_checksums(tmp_path, manifest)
    wheel.write_bytes(b"tampered")

    with pytest.raises(DistributionVerificationError, match="checksum mismatch"):
        verify_checksums(tmp_path, manifest)


def test_candidate_manifest_binds_tag_commit_and_artifacts(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    commit = "a" * 40
    manifest = write_candidate_manifest(tmp_path, tmp_path / "CANDIDATE.json", "v0.1.0", commit)

    verify_candidate_manifest(tmp_path, manifest, expected_tag="v0.1.0", expected_commit=commit)

    with pytest.raises(DistributionVerificationError, match="candidate tag mismatch"):
        verify_candidate_manifest(tmp_path, manifest, expected_tag="v0.1.1", expected_commit=commit)
    with pytest.raises(DistributionVerificationError, match="candidate commit mismatch"):
        verify_candidate_manifest(
            tmp_path,
            manifest,
            expected_tag="v0.1.0",
            expected_commit="b" * 40,
        )

    wheel.write_bytes(b"tampered")
    with pytest.raises(DistributionVerificationError, match="candidate artifact mismatch"):
        verify_candidate_manifest(tmp_path, manifest, expected_tag="v0.1.0", expected_commit=commit)


def test_index_digest_verifier_requires_the_exact_candidate_bytes(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    manifest = write_candidate_manifest(
        tmp_path,
        tmp_path / "CANDIDATE.json",
        "v0.1.0",
        "a" * 40,
    )
    candidate = json.loads(manifest.read_text(encoding="utf-8"))["artifacts"]
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps(
            {
                "urls": [
                    {"filename": name, "digests": {"sha256": digest}}
                    for name, digest in candidate.items()
                ]
            }
        ),
        encoding="utf-8",
    )

    verify_index_digests(tmp_path, index)

    payload = json.loads(index.read_text(encoding="utf-8"))
    payload["urls"][0]["digests"]["sha256"] = "0" * 64
    index.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DistributionVerificationError, match="index artifact mismatch"):
        verify_index_digests(tmp_path, index)


def test_index_digest_verifier_rejects_duplicate_files(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    digest = "0" * 64
    index = tmp_path / "index.json"
    duplicate = {"filename": wheel.name, "digests": {"sha256": digest}}
    index.write_text(json.dumps({"urls": [duplicate, duplicate]}), encoding="utf-8")

    with pytest.raises(DistributionVerificationError, match="duplicate index artifact"):
        verify_index_digests(tmp_path, index)
