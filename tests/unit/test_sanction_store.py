from __future__ import annotations

from pebra.adapters.candidate_binding import binding_for_patch
from pebra.adapters.sanction_store import SanctionStore
from pebra.adapters.store.db import SqliteStore
from pebra.core.models import CandidateAction


_PATCH_A = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

_PATCH_B = _PATCH_A.replace("value = 2", "value = 3")


def test_sanction_is_exact_candidate_bound_and_single_use(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = "asm_1"
    binding = binding_for_patch(repo, _PATCH_A)
    store.create_sanction(
        "repo_x",
        {
            "valid": True,
            "assessment_id": assessment_id,
            "action_id": "a1",
            "risk_profile": {
                "assessment_id": assessment_id,
                "action_id": "a1",
                "candidate_binding": binding,
            },
        },
    )
    sanctions = SanctionStore(store, repo_root=repo)

    assert sanctions.active_sanction(
        "repo_x", CandidateAction("a1", "different", "edit", proposed_patch=_PATCH_B)
    ) is None
    assert sanctions.active_sanction(
        "repo_x", CandidateAction("a1", "exact", "edit", proposed_patch=_PATCH_A)
    ) is not None
    assert sanctions.active_sanction(
        "repo_x", CandidateAction("a1", "replay", "edit", proposed_patch=_PATCH_A)
    ) is None
    assert store.active_sanction_for_assessment(assessment_id) is not None
