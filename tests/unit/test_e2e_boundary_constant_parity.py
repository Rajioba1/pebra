"""Parity guards for e2e helpers/constants that MUST track pebra internals but cannot import them
(boundary discipline forbids ``import pebra`` under e2e/). Each e2e copy is pinned here, on the
import-allowed ``tests/`` side, against production — so a future change to one side fails loudly instead
of silently drifting an experiment's evidence.

Two pins:
  * ``candidate_patch_hash`` — the wire convention binding a candidate verification to its patch. The
    e2e verifier recomputes it; both it and ``decision_engine`` must equal the documented digest
    (sha256 of the exact UTF-8 patch text, no normalization). Anchored to independently-computed
    ground-truth digests so the test fails if EITHER side drifts, not just if they drift together.
  * ``SEED_N`` — the compiler-scenario seed count is load-bearing: it is exactly
    ``MIN_CALIBRATION_SAMPLES - 1`` so the ONE real build cycle is the promotion-tipping 100th sample.
    If ``MIN_CALIBRATION_SAMPLES`` is recalibrated, that boundary proof silently stops testing the tip
    unless this pin trips.
"""

from __future__ import annotations

from e2e.experiments.agent_ab.tools.candidate_verifier import (
    candidate_patch_hash as e2e_candidate_patch_hash,
)
from e2e.external.utils.compiler_scenario import SEED_N as COMPILER_SEED_N
from e2e.utils.learning_scenario import SEED_N as LEARNING_SEED_N
from pebra.core.constants import MIN_CALIBRATION_SAMPLES
from pebra.core.decision_engine import candidate_patch_hash as prod_candidate_patch_hash

# Independently-computed sha256 hexdigests (external oracle) of the exact UTF-8 bytes — NOT produced by
# either function under test, so agreement here is not tautological.
_KNOWN_DIGESTS = {
    "": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n": (
        "66ef576c19e659f4c6003422a56f7486e9be6bdb6c0ac48b9514fdbc332b8576"
    ),
    "diff with unicode λ→β\n": "574e1589b9c391550f1554c72158b8d0e953cbd7ec4245e8d6ff4dd0fabded1e",
}


def test_candidate_patch_hash_matches_production_and_documented_digest() -> None:
    for patch, expected in _KNOWN_DIGESTS.items():
        assert e2e_candidate_patch_hash(patch) == expected
        assert prod_candidate_patch_hash(patch) == expected
        assert e2e_candidate_patch_hash(patch) == prod_candidate_patch_hash(patch)


def test_compiler_seed_n_is_the_promotion_tipping_boundary() -> None:
    # 99 seeded + 1 real cycle == 100 == MIN_CALIBRATION_SAMPLES: the real cycle is the tipping sample.
    assert COMPILER_SEED_N == MIN_CALIBRATION_SAMPLES - 1


def test_learning_seed_n_stays_above_the_calibration_floor() -> None:
    # the learning scenario deliberately seeds PAST the promotion floor (already-calibrated regime).
    assert LEARNING_SEED_N >= MIN_CALIBRATION_SAMPLES
