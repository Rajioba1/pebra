from pebra.adapters.request_evidence import RequestEvidenceProvider
from pebra.core.models import AssessmentRequest, CandidateAction


def test_request_evidence_parses_candidate_verification() -> None:
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
            }
        },
    )

    bundle = RequestEvidenceProvider().gather_evidence(
        request, request.candidate_actions[0], repo_root="."
    )

    assert bundle.candidate_verification.status == "passed"
    assert bundle.candidate_verification.checks["GammaTests"] == "passed"
    assert bundle.candidate_verification.required_checks == ["GammaTests"]
    assert bundle.candidate_verification.domain == "numeric_equivalence"
    assert bundle.candidate_verification.reason == "all sampled values stayed within tolerance"
