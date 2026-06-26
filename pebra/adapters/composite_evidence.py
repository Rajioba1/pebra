"""composite_evidence (Slice 5) — the live EvidenceProvider.

Orchestrates the Slice-4 adapters: request base + config + radon benefit + bandit security + the
architecture map, then composes them with the pure ``merge_evidence``. This is the only new
orchestrating adapter; ``cli/assess.py`` swaps ``RequestEvidenceProvider`` for this.

Dep-light safety: the local .venv has no yaml/radon/bandit, and the golden CLI runs there, so the
heavy adapters are imported LAZILY and a missing one DEGRADES (yaml -> default config; radon ->
projected empty benefit). A *missing* tool is inert by default — only ``strict_mode`` turns an
unavailable tool into an evidence-quality gap. Precedence (request wins; config only raises
criticality; radon fills only an empty projected gap; bandit appends one deduped event; architecture
evidence is carried) is enforced by ``merge_evidence``.
"""

from __future__ import annotations

from pathlib import Path

from pebra.adapters import git_adapter
from pebra.adapters._paths import safe_relative_files
from pebra.adapters.architecture_map import ArchitectureMapAdapter
from pebra.adapters.bandit_adapter import BanditAdapter  # safe: bandit runs via subprocess, no top import
from pebra.adapters.evidence_merge import merge_evidence
from pebra.adapters.request_evidence import RequestEvidenceProvider
from pebra.core.models import (
    AssessmentRequest,
    BenefitDeltaEvidence,
    CandidateAction,
    EvidenceBundle,
)
from pebra.ports.config_port import PebraConfig


class CompositeEvidenceProvider:
    """Live EvidenceProvider composing config + radon + bandit + architecture over the request base."""

    def __init__(self, graph_provider: object | None = None) -> None:
        self._request = RequestEvidenceProvider()
        # no criticality_globs: merge_evidence owns criticality. graph_provider (5c) is the build-once
        # memo for THIS adapter's arch call; the blast adapter is wired with the SAME instance by the
        # caller (cli/assess.py), so together they build the import graph once per assessment.
        self._arch = ArchitectureMapAdapter(graph_provider=graph_provider)
        self._bandit = BanditAdapter()

    def gather_evidence(
        self, request: AssessmentRequest, action: CandidateAction, repo_root: str
    ) -> EvidenceBundle:
        base = self._request.gather_evidence(request, action, repo_root)
        config = _load_config(repo_root)
        radon_benefit = _gather_radon(repo_root, action)

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
            radon_benefit=radon_benefit,
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


def _gather_radon(repo_root: str, action: CandidateAction) -> BenefitDeltaEvidence:
    try:
        from pebra.adapters.radon_adapter import RadonAdapter
    except ImportError as exc:
        if _missing_package(exc, "radon"):
            return BenefitDeltaEvidence(source_type="projected", deltas={})  # radon not installed
        raise  # an internal import bug in radon_adapter must not be silently degraded
    return RadonAdapter().gather_benefit_evidence(
        repo_root, action.expected_files, action.proposed_patch
    )
