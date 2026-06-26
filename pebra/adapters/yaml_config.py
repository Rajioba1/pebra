"""yaml_config (ConfigPort, Architecture §10 / Slice 4a) — load .pebra.yml into PebraConfig.

Adapter layer: pyyaml is allowed here (the import-linter forbids it in core/). The pure engine never
consumes config — this adapter resolves .pebra.yml into a PebraConfig that later (Slice 5) the
composite evidence provider turns into thresholds/criticality the engine sees as plain values.

Behavior: missing file -> defaults; empty file -> defaults; malformed YAML -> ValueError naming the
file; non-mapping top level -> ValueError. The reserved `medium_auto_proceed_requires` key (AD-6) is
WARNED about and recorded as a flag only — its value is never evaluated.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Any

import yaml

from pebra.core.constants import STAGE_MAP
from pebra.ports.config_port import CriticalityGlob, EditConfidenceWeights, PebraConfig

_CONFIG_NAME = ".pebra.yml"
_RESERVED_WARN_KEYS = ("medium_auto_proceed_requires",)


def _parse_fraction(value: Any) -> float:
    """`"3/4"` -> 0.75; plain numbers pass through. Used for edit-confidence weights."""
    if isinstance(value, str) and "/" in value:
        num, _, den = value.partition("/")
        try:
            return float(num) / float(den)
        except ZeroDivisionError:
            raise ValueError(f"invalid fraction {value!r}: division by zero") from None
        except ValueError:
            raise ValueError(f"invalid fraction {value!r}") from None
    return float(value)


def _globs(raw: Any) -> list[CriticalityGlob]:
    if not isinstance(raw, dict):
        return []
    globs: list[CriticalityGlob] = []
    for pattern, stage in raw.items():
        s = str(stage)
        if s not in STAGE_MAP:  # fail clearly on a typo'd stage rather than silently dropping the rule
            raise ValueError(
                f"unknown criticality stage {s!r} for {str(pattern)!r} "
                f"(expected one of {sorted(STAGE_MAP)})"
            )
        globs.append(CriticalityGlob(pattern=str(pattern), stage=s))
    return globs


def _thresholds(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for k, v in raw.items():
        try:
            val = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"threshold {str(k)!r} must be a number, got {v!r}") from None
        # finite & positive: .nan/.inf would make gate comparisons fail open once config is live, and
        # a 0 threshold is degenerate (and a divisor in risk_budget_used).
        if not math.isfinite(val) or val <= 0:
            raise ValueError(f"threshold {str(k)!r} must be a finite positive number, got {v!r}")
        result[str(k)] = val
    return result


def _strict_mode(value: Any) -> bool:
    # Must be a real YAML boolean. Reject quoted strings like "false" (bool("false") is True) so a
    # config can never silently enable strict mode when the author meant to disable it.
    if not isinstance(value, bool):
        raise ValueError(f"'strict_mode' must be a boolean (true/false), got {value!r}")
    return value


def _weights(raw: Any) -> EditConfidenceWeights:
    if not isinstance(raw, dict):
        return EditConfidenceWeights()
    fields = EditConfidenceWeights.__dataclass_fields__
    kwargs: dict[str, float] = {}
    for k, v in raw.items():
        if k not in fields:  # unknown keys ignored
            continue
        w = _parse_fraction(v)
        # finite & non-negative: 0 legitimately disables a factor; .nan/.inf/negative break the mean.
        if not math.isfinite(w) or w < 0:
            raise ValueError(
                f"edit_confidence weight {k!r} must be a finite, non-negative number, got {v!r}"
            )
        kwargs[k] = w
    return EditConfidenceWeights(**kwargs)


def _warn_reserved(raw: dict[str, Any], path: Path) -> bool:
    present = any(k in raw for k in _RESERVED_WARN_KEYS)
    if present:
        warnings.warn(
            f"{_CONFIG_NAME} at {path}: 'medium_auto_proceed_requires' is reserved (v1.5) and is "
            "NOT evaluated; its value is ignored.",
            UserWarning,
            stacklevel=2,
        )
    return present


class YamlConfigAdapter:
    """Concrete ConfigPort: reads ``<repo_root>/.pebra.yml``."""

    def load_config(self, repo_root: str) -> PebraConfig:
        path = Path(repo_root) / _CONFIG_NAME
        if not path.is_file():
            return PebraConfig()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:  # deleted between stat and read, permissions, etc.
            raise ValueError(f"Cannot read {_CONFIG_NAME} at {path}: {exc}") from exc
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"Malformed {_CONFIG_NAME} at {path}: {exc}") from exc
        if raw is None:  # empty file
            return PebraConfig()
        if not isinstance(raw, dict):
            raise ValueError(f"Malformed {_CONFIG_NAME} at {path}: expected a mapping at the top level")
        # Resolve the sections; any field-level problem is re-raised with the file path so the user
        # always gets a clear "Malformed .pebra.yml at <path>: ..." rather than a bare TypeError.
        try:
            return PebraConfig(
                criticality_globs=_globs(raw.get("criticality", {})),
                thresholds=_thresholds(raw.get("thresholds", {})),
                edit_confidence_weights=_weights(raw.get("edit_confidence_weights", {})),
                has_medium_auto_proceed_requires=_warn_reserved(raw, path),
                strict_mode=_strict_mode(raw.get("strict_mode", False)),
            )
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            raise ValueError(f"Malformed {_CONFIG_NAME} at {path}: {exc}") from exc
