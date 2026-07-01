"""Guard the invariant the compiler-outcome learning proof rests on (review C1).

The proof compares a baseline assess (signature request) to a post-learning reassess (follow-up request)
and asserts the RAU dropped. That is only sound if the two requests are SCORING-IDENTICAL — they must
differ ONLY in identity (task / action id), never in evidence or thresholds. Otherwise a harsher
follow-up would lower RAU on its own and the test would pass for the wrong reason. Fast unit test (a
stub interface file is enough); no real repo, no pebra import.
"""

from __future__ import annotations

from e2e.external.utils import signature_edit as se

_STUB_IWORKSPACE = "namespace X;\npublic interface IWorkspace\n{\n    Task<bool> CanCloseAsync();\n}\n"


def _stub_copy(tmp_path):
    f = tmp_path / se.IWORKSPACE_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_STUB_IWORKSPACE, encoding="utf-8")
    return tmp_path


def test_signature_and_followup_requests_are_scoring_equivalent(tmp_path):
    copy = _stub_copy(tmp_path)
    sig = se.build_signature_request(copy)
    fol = se.build_followup_request(copy)
    # identical in everything that feeds scoring (incl. the same proposed patch / code location) ...
    assert sig["evidence"] == fol["evidence"]
    assert sig["thresholds"] == fol["thresholds"]
    assert sig["candidate_actions"][0]["proposed_patch"] == fol["candidate_actions"][0]["proposed_patch"]
    # ... but a DISTINCT future proposal in identity
    assert sig["candidate_actions"][0]["id"] != fol["candidate_actions"][0]["id"]
    assert sig["task"] != fol["task"]
