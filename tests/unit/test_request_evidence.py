from pebra.adapters.request_evidence import RequestEvidenceProvider
from pebra.core.models import AssessmentRequest, CandidateAction


def test_request_evidence_ignores_candidate_verification_from_request() -> None:
    request = AssessmentRequest(
        task="verify safer route",
        candidate_actions=[CandidateAction(id="a1", label="edit", action_type="edit")],
        evidence={
            "candidate_verification": {
                "status": "passed",
                "checks": {"GammaTests": "passed", "numeric_equivalence_gamma": "passed"},
                "required_checks": ["GammaTests"],
                "domain": "numeric_equivalence",
                "reason": "all sampled values stayed within tolerance",
                "verified_patch_hash": "a" * 64,
            }
        },
    )

    bundle = RequestEvidenceProvider().gather_evidence(
        request, request.candidate_actions[0], repo_root="."
    )

    assert bundle.candidate_verification.status == "not_applicable"
    assert bundle.candidate_verification.checks == {}
    assert bundle.candidate_verification.required_checks == []
    assert bundle.candidate_verification.domain is None
    assert bundle.candidate_verification.reason is None
    assert bundle.candidate_verification.verified_patch_hash is None


def test_request_evidence_ignores_non_string_verified_patch_hash() -> None:
    request = AssessmentRequest(
        task="verify safer route",
        candidate_actions=[CandidateAction(id="a1", label="edit", action_type="edit")],
        evidence={"candidate_verification": {"status": "passed", "verified_patch_hash": 12345}},
    )

    bundle = RequestEvidenceProvider().gather_evidence(
        request, request.candidate_actions[0], repo_root="."
    )

    # Candidate verification is host/controller evidence, not request evidence. Malformed blobs must
    # be ignored, never interpreted.
    assert bundle.candidate_verification.verified_patch_hash is None
    assert bundle.candidate_verification.status == "not_applicable"
