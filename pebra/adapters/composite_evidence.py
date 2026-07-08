"""composite_evidence (Slice 5) — the live EvidenceProvider.

Orchestrates the Slice-4 adapters: request base + config + RCA benefit + bandit security + the
architecture map, then composes them with the pure ``merge_evidence``. This is the only new
orchestrating adapter; ``cli/assess.py`` swaps ``RequestEvidenceProvider`` for this.

Dep-light safety: yaml is imported LAZILY and a missing one DEGRADES (yaml -> default config). RCA and
bandit run via SUBPROCESS (no third-party import to fail), so they're imported at the top and degrade
on BINARY absence instead. RCA absence always means projected/empty benefit (no maintainability
credit); only Bandit run-failure can become an evidence-quality penalty under ``strict_mode``.
Precedence (request wins; config only raises criticality; the benefit provider fills only an empty
projected gap; bandit appends one deduped event; architecture evidence is carried) is enforced by
``merge_evidence``.
"""

from __future__ import annotations

from pathlib import Path

from pebra.adapters import git_adapter
from pebra.adapters._paths import safe_relative_files
from pebra.adapters.architecture_map import ArchitectureMapAdapter
from pebra.adapters.bandit_adapter import BanditAdapter  # safe: bandit runs via subprocess, no top import
from pebra.adapters.evidence_merge import merge_evidence
from pebra.adapters.rca_adapter import RustCodeAnalysisAdapter  # subprocess-based, no top-level dep
from pebra.adapters.request_evidence import RequestEvidenceProvider
from pebra.core.models import AssessmentRequest, CandidateAction, EvidenceBundle
from pebra.ports.config_port import PebraConfig


class CompositeEvidenceProvider:
    """Live EvidenceProvider composing config + RCA benefit + bandit + architecture over the request."""

    def __init__(self, graph_provider: object | None = None) -> None:
        self._request = RequestEvidenceProvider()
        # no criticality_globs: merge_evidence owns criticality. graph_provider (5c) is the build-once
        # memo for THIS adapter's arch call; the blast adapter is wired with the SAME instance by the
        # caller (cli/assess.py), so together they build the import graph once per assessment.
        self._arch = ArchitectureMapAdapter(graph_provider=graph_provider)
        self._bandit = BanditAdapter()
        self._rca = RustCodeAnalysisAdapter()

    def gather_evidence(
        self, request: AssessmentRequest, action: CandidateAction, repo_root: str
    ) -> EvidenceBundle:
        base = self._request.gather_evidence(request, action, repo_root)
        config = _load_config(repo_root)
        provider_benefit = self._rca.gather_benefit_evidence(
            repo_root, action.expected_files, action.proposed_patch
        )

        # validate paths BEFORE the is_file() probe (no oracle for files outside the repo), then keep
        # only those that exist (a planned-but-absent file is not a bandit run-failure).
        safe = safe_relative_files(repo_root, action.expected_files)
        existing = [f for f in safe if (Path(repo_root) / f).is_file()]
        bandit_events, raw_penalty = self._bandit.gather_security_events(existing, repo_root)
        # a missing/unavailable tool is inert by default; only strict_mode treats it as a gap
        penalty = raw_penalty if config.strict_mode else 0.0

        # provenance only (5b): record the repo HEAD on the architecture evidence. Freshness stays
        # content-hash based (a non-git tree -> None head, still FRESH/REBUILT, never UNKNOWN).
        current_head = git_adapter.head_commit(repo_root)
        arch = self._arch.gather_architecture(repo_root, action.expected_files, current_head=current_head)

        return merge_evidence(
            base,
            config=config,
            architecture_evidence=arch,
            provider_benefit=provider_benefit,
            bandit_events=bandit_events,
            evidence_quality_penalty=penalty,
            affected_files=action.expected_files,
        )


def _missing_package(exc: ImportError, package: str) -> bool:
    """True iff the ImportError is the EXTERNAL ``package`` being absent — not an internal import bug
    inside the adapter. We degrade only for the former; the latter must surface."""
    return (exc.name or "").split(".")[0] == package


def _load_config(repo_root: str) -> PebraConfig:
    try:
        from pebra.adapters.yaml_config import YamlConfigAdapter
    except ImportError as exc:
        if _missing_package(exc, "yaml"):
            return PebraConfig()  # pyyaml not installed -> defaults
        raise  # an internal import bug in yaml_config must not be silently degraded
    return YamlConfigAdapter().load_config(repo_root)  # a malformed .pebra.yml still raises ValueError
