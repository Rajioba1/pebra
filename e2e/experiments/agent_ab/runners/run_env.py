"""Small helpers for the live A/B nox entry point.

Direct orchestrator calls remain fail-closed and require the explicit gate variables. The nox session
is itself the explicit opt-in, so it can set the non-secret gates and default the known local external
repo path. The API key remains user-supplied.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

_REPO_ENV = "E2E_TEMPLATE_BLUEPRINT_REPO"
_KEY_ENV = "ANTHROPIC_API_KEY"
_MODEL_ENV = "E2E_AB_MODEL"
_LOCAL_ENV = Path(".pebra") / "agent_ab.env"


def default_external_repo(repo_root: Path | None = None) -> Path | None:
    root = repo_root or Path(__file__).resolve().parents[4]
    candidate = root.parent / "avalonia_template"
    return candidate if candidate.exists() else None


def local_env_file(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[4]
    return root / _LOCAL_ENV


def _read_local_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def live_env(base: Mapping[str, str], *, repo_root: Path | None = None) -> dict[str, str]:
    env = dict(base)
    local = _read_local_env(local_env_file(repo_root))
    if not env.get(_KEY_ENV) and local.get(_KEY_ENV):
        env[_KEY_ENV] = local[_KEY_ENV]
    if not env.get(_MODEL_ENV) and local.get(_MODEL_ENV):
        env[_MODEL_ENV] = local[_MODEL_ENV]
    env["E2E_AB_RUN"] = "1"
    env["E2E_EXTERNAL"] = "1"
    if not env.get(_REPO_ENV):
        default_repo = default_external_repo(repo_root)
        if default_repo is not None:
            env[_REPO_ENV] = str(default_repo)
    return env


def missing_for_live_env(env: Mapping[str, str]) -> list[str]:
    missing: list[str] = []
    if not env.get(_KEY_ENV):
        missing.append(f"{_KEY_ENV}=<key>")
    if not env.get(_REPO_ENV):
        missing.append(f"{_REPO_ENV}=<path>")
    return missing
