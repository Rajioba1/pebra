"""Deterministic single-repository fit for the provisional continuity prior."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable


CALIBRATION_TAG = "zod_single_repo_provisional_v1"
DEFAULT_MINIMUM_OWNER_CLUSTERS = 3


@dataclass(frozen=True)
class FitResult:
    schema_version: str
    calibration_tag: str
    evidence_scope: str
    action_type: str
    language: str
    language_tier: str
    graph_fact_kind: str
    sample_size: int
    successes: int
    owner_cluster_ids: tuple[str, ...]
    p_success: float
    p_success_variance: float
    p_success_aleatoric_variance: float


def _proof_rows_by_owner(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("calibration_fit_eligible") is not True:
            continue
        if row.get("proof_fired") is not True:
            continue
        owner = row.get("owner_cluster_id")
        if not isinstance(owner, str) or not owner:
            raise ValueError("every proof-fired row must identify an owner_cluster_id")
        if row.get("language_tier") != "full":
            raise ValueError(f"{owner}: proof-fired row must use measured full-tier evidence")
        if not isinstance(row.get("action_success"), bool):
            raise ValueError(f"{owner}: proof-fired row must carry an observed action_success")
        grouped.setdefault(owner, []).append(row)
    duplicates = sorted(owner for owner, owner_rows in grouped.items() if len(owner_rows) != 1)
    if duplicates:
        raise ValueError(
            "each independent owner must contribute exactly one proof-fired row: "
            + ", ".join(duplicates)
        )
    return {owner: owner_rows[0] for owner, owner_rows in grouped.items()}


def fit_rows(
    rows: Iterable[dict[str, Any]],
    *,
    minimum_owner_clusters: int = DEFAULT_MINIMUM_OWNER_CLUSTERS,
) -> FitResult:
    """Fit one Beta-smoothed success prior over independent proof-bearing owners.

    Parameter variance represents uncertainty in the owner-level success rate. Aleatoric variance is
    the posterior expected Bernoulli variance; the production resolver still applies its reviewed
    cold-start floor and cap before this value can affect a decision.
    """
    if minimum_owner_clusters < 2:
        raise ValueError("minimum_owner_clusters must be at least two")
    by_owner = _proof_rows_by_owner(rows)
    if len(by_owner) < minimum_owner_clusters:
        raise ValueError(
            f"fit requires at least {minimum_owner_clusters} independent owner clusters; "
            f"observed {len(by_owner)}"
        )
    owner_ids = tuple(sorted(by_owner))
    outcomes = [int(bool(by_owner[owner]["action_success"])) for owner in owner_ids]
    successes = sum(outcomes)
    alpha = 1.0 + successes
    beta = 1.0 + len(outcomes) - successes
    total = alpha + beta
    p_success = alpha / total
    parameter_variance = (alpha * beta) / ((total**2) * (total + 1.0))
    # Posterior expected Bernoulli variance. The raw sample variance would be zero for 3/3 and would
    # incorrectly present three deterministic reference cases as zero outcome uncertainty.
    aleatoric_variance = (alpha * beta) / (total * (total + 1.0))
    values = (p_success, parameter_variance, aleatoric_variance)
    if not all(math.isfinite(value) and value >= 0.0 for value in values):
        raise ValueError("fit produced a non-finite or negative value")
    return FitResult(
        schema_version="continuity-prior-fit-v1",
        calibration_tag=CALIBRATION_TAG,
        evidence_scope="single_repository_provisional",
        action_type="edit",
        language="typescript",
        language_tier="full",
        graph_fact_kind="exported_binding_continuity",
        sample_size=len(outcomes),
        successes=successes,
        owner_cluster_ids=owner_ids,
        p_success=p_success,
        p_success_variance=parameter_variance,
        p_success_aleatoric_variance=aleatoric_variance,
    )


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"line {line_number}: expected a JSON object")
        rows.append(row)
    return rows


def fit_file(
    path: Path,
    *,
    minimum_owner_clusters: int = DEFAULT_MINIMUM_OWNER_CLUSTERS,
) -> FitResult:
    return fit_rows(load_rows(path), minimum_owner_clusters=minimum_owner_clusters)


def to_json(result: FitResult) -> str:
    return json.dumps(asdict(result), sort_keys=True, separators=(",", ":"))


def verify_frozen_prior(result: FitResult) -> None:
    """Fail when reviewed production constants drift from the reproducible benchmark fit."""
    from pebra.core.calibrated_priors import CALIBRATED_PRIORS  # noqa: PLC0415

    cells = [cell for cell in CALIBRATED_PRIORS if cell.calibration_tag == result.calibration_tag]
    if len(cells) != 1:
        raise ValueError("frozen provisional prior does not match the reviewed fit")
    cell = cells[0]
    exact = (
        cell.sample_size == result.sample_size
        and cell.action_type == result.action_type
        and cell.language == result.language
        and cell.language_tier == result.language_tier
        and cell.graph_fact_kind == result.graph_fact_kind
    )
    fitted = (
        cell.p_success,
        cell.p_success_variance,
        cell.p_success_aleatoric_variance,
    )
    expected = (
        result.p_success,
        result.p_success_variance,
        result.p_success_aleatoric_variance,
    )
    if not exact or any(
        actual is None or not math.isclose(actual, target, rel_tol=0.0, abs_tol=1e-12)
        for actual, target in zip(fitted, expected, strict=True)
    ):
        raise ValueError("frozen provisional prior does not match the reviewed fit")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--minimum-owner-clusters", type=int, default=DEFAULT_MINIMUM_OWNER_CLUSTERS
    )
    parser.add_argument("--verify-frozen", action="store_true")
    args = parser.parse_args(argv)
    result = fit_file(args.input, minimum_owner_clusters=args.minimum_owner_clusters)
    if args.verify_frozen:
        verify_frozen_prior(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(to_json(result) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
