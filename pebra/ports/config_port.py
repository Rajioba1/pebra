"""ConfigPort (Architecture §10) + the PebraConfig data contract.

PebraConfig lives here (next to its port, the RepoMetadata pattern) rather than in core/: the pure
engine never consumes config — adapters resolve config into evidence (criticality stage, thresholds)
that reaches the engine as plain values inside AssessmentInput.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CriticalityGlob:
    """A `.pebra.yml` criticality mapping, e.g. ``src/payments/** -> C4``."""

    pattern: str
    stage: str


@dataclass(frozen=True)
class PolicyRule:
    """A configured hard policy rule, e.g. ``src/secrets/** -> forbidden_path_edit``."""

    pattern: str
    violation: str


@dataclass(frozen=True)
class EditConfidenceWeights:
    """Per-factor weights (parsed from `N/M` fractions in `.pebra.yml`); equal by default."""

    p_success: float = 1.0
    evidence_quality: float = 1.0
    testability: float = 1.0
    reversibility: float = 1.0
    source_reliability: float = 1.0
    scope_control: float = 1.0


@dataclass(frozen=True)
class PebraConfig:
    criticality_globs: list[CriticalityGlob] = field(default_factory=list)
    policy_rules: list[PolicyRule] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    edit_confidence_weights: EditConfidenceWeights = field(default_factory=EditConfidenceWeights)
    # AD-6: `medium_auto_proceed_requires` is v1.5-reserved — the loader WARNS if present and records
    # only this flag; the value is never evaluated.
    has_medium_auto_proceed_requires: bool = False
    # Slice 4: when True, Bandit run-failures cap evidence_quality harder (strict). RCA absence is
    # benefit-only and remains projected/no-credit, not an evidence-quality penalty.
    strict_mode: bool = False


class ConfigPort(Protocol):
    def load_config(self, repo_root: str) -> PebraConfig: ...
