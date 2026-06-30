"""Shared true-e2e learning scenario helpers.

The scenario stays at the agent boundary: every PEBRA interaction goes through cli_harness subprocesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from e2e.utils import agent_harness as ah
from e2e.utils import cli_harness as ch

SEED_N = 105


@dataclass(frozen=True)
class SeededLearningState:
    repo_path: Path
    db_path: Path
    seed_request_path: Path
    future_request_path: Path
    baseline: dict
    promotion: dict
    scorecard: dict
    learned: dict


def build_seeded_learning_state(
    repo_path: Path, db_path: Path, seed_request_path: Path, future_request_path: Path,
    *, seed_n: int = SEED_N,
) -> SeededLearningState:
    """Run the seeded-history e2e scenario and return the observable boundary artifacts."""
    baseline = ch.assess(seed_request_path, repo_root=repo_path, db=db_path)
    ah.seed_failed_history(repo_path, db_path, seed_request_path, n=seed_n)

    promotion = ch.promote(repo_root=repo_path, db=db_path)
    scorecard = ch.scorecard(repo_root=repo_path, db=db_path)

    # Future assess is pre-edit again: clean working tree, a distinct similar proposed edit, learned
    # snapshot active.
    ah.reset_risky_edit(repo_path)
    learned = ch.assess(future_request_path, repo_root=repo_path, db=db_path)

    return SeededLearningState(
        repo_path=repo_path,
        db_path=db_path,
        seed_request_path=seed_request_path,
        future_request_path=future_request_path,
        baseline=baseline,
        promotion=promotion,
        scorecard=scorecard,
        learned=learned,
    )
