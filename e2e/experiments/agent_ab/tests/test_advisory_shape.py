"""The blinding-critical invariant: the treatment backend's AGENT-FACING output is structurally
identical to the sham's and carries no engine-identifying vocabulary. Uses a fixture PEBRA payload —
no real CLI call — by testing the pure `_shape_output`.
"""

from __future__ import annotations

import json

import pytest

from e2e.experiments.agent_ab.tools import advisory_check_real as real
from e2e.experiments.agent_ab.tools import advisory_check_sham as sham
from e2e.experiments.agent_ab.tools import advisory_contract

# Vocabulary that would reveal the engine / unblind the treatment arm.
_FORBIDDEN_VOCAB = ("graph", "fan-in", "fanin", "percentile", "pebra", "codegraph", "blast")

# A representative PEBRA assess result whose raw content is deliberately leaky (summary + provenance).
_LEAKY_PEBRA_RESULT = {
    "recommended_decision": "reject",
    "scores": {"expected_loss": 0.5336, "rau": -0.12},
    "graph_provenance": {"symbol_fanin": {"caller_count": 13, "percentile": 0.99}},
    "model_guidance_packet": {
        "summary": "High fan-in symbol; CodeGraph shows 13 callers; the blast radius is large."
    },
}


def _all_strings(obj) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            out.extend(_all_strings(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_all_strings(v))
    return out


def test_real_output_shape_matches_sham_and_is_vocab_clean():
    out = real._shape_output(_LEAKY_PEBRA_RESULT)
    sham_out = sham.advise({})

    # 1. Identical key set (top-level) and identical detail sub-shape (both empty) — no structural tell.
    assert set(out) == set(sham_out)
    assert out["detail"] == sham_out["detail"] == {}

    # 2. No engine vocabulary anywhere in the real output's strings (keys or values).
    blob = " ".join(_all_strings(out)).lower()
    for term in _FORBIDDEN_VOCAB:
        assert term not in blob, f"engine vocab leaked into agent-facing output: {term!r}"

    # 3. The decision/risk VALUES may differ from the sham — that is the whole point of the treatment arm.
    assert out["recommended_decision"] == "reject"
    assert out["risk_level"] == "high"
    assert sham_out["recommended_decision"] is None


def test_host_receipt_is_not_serialized_into_agent_facing_output():
    payload = real._shape_output(_LEAKY_PEBRA_RESULT)
    out = real.AdvisoryOutput(payload, assessment_id="asm_7")

    assert tuple(out) == advisory_contract.OUTPUT_KEYS
    assert "assessment_id" not in out
    assert "assessment_id" not in json.loads(json.dumps(out))
    assert out.assessment_id == "asm_7"


def test_real_output_is_vocab_clean_for_every_decision():
    for decision in ("proceed", "inspect_first", "test_first", "revise_safer",
                     "ask_human", "reject", None):
        out = real._shape_output({"recommended_decision": decision, "scores": {}})
        blob = " ".join(_all_strings(out)).lower()
        for term in _FORBIDDEN_VOCAB:
            assert term not in blob, f"{decision}: vocab leaked: {term!r}"


def test_reject_advisory_is_actionable_without_engine_vocab():
    out = real._shape_output({"recommended_decision": "reject", "scores": {"expected_loss": 1.0}})
    advisory = out["advisory"].lower()
    assert "do not edit" in advisory
    assert "stop" in advisory


def test_revise_safer_advisory_blocks_current_patch_with_forward_path():
    out = real._shape_output({"recommended_decision": "revise_safer", "scores": {"expected_loss": 1.0}})
    advisory = out["advisory"].lower()
    assert "do not apply" in advisory
    assert "narrower" in advisory


def test_revise_safer_advisory_surfaces_blinded_safer_route_constraints():
    out = real._shape_output({
        "recommended_decision": "revise_safer",
        "scores": {"expected_loss": 1.0},
        "model_guidance_packet": {
            "advisory": {
                "safer_route": {
                    "summary": "Revise to a safer, narrower route and resubmit before editing.",
                    "constraints": [
                        "Keep the next candidate inside the assessed file scope: src/Numerics/SpecialFunctions/Gamma.cs.",
                        "Inspect dependent code before changing this route (28 dependent callers).",
                    ],
                }
            }
        },
    })

    advisory = out["advisory"]
    assert "src/Numerics/SpecialFunctions/Gamma.cs" in advisory
    assert "28 dependent callers" in advisory
    assert out["detail"] == {}
    blob = " ".join(_all_strings(out)).lower()
    for term in _FORBIDDEN_VOCAB:
        assert term not in blob, f"engine vocab leaked into agent-facing output: {term!r}"


def test_advisory_contract_accepts_patch_or_structured_candidate_edits():
    assert "proposed_patch" not in advisory_contract.INPUT_SCHEMA["required"]
    assert "candidate_edits" in advisory_contract.INPUT_SCHEMA["properties"]
    with pytest.raises(ValueError, match="requires proposed_patch"):
        real._build_request({"target_file": "x.cs", "change_summary": "change x"})


def test_build_request_carries_revise_safer_attempt():
    req = real._build_request({
        "target_file": "x.cs",
        "change_summary": "change x",
        "proposed_patch": "diff --git a/x.cs b/x.cs",
    }, revise_safer_attempt=2)

    assert req["thresholds"]["revise_safer_attempt"] == 2
    assert req["thresholds"]["max_revise_safer_attempts"] == 1
    assert req["thresholds"]["codegraph_semantic_diff_enabled"] == 1.0


def test_build_request_leaves_maintainability_evidence_open_for_production_measurement():
    req = real._build_request({
        "target_file": "x.ts",
        "change_summary": "change x",
        "proposed_patch": "diff --git a/x.ts b/x.ts",
    })

    assert req["evidence"]["immediate_benefit"] == 0.5
    assert "benefit_delta_evidence" not in req["evidence"]


def test_build_request_declares_every_file_in_multifile_patch():
    patch = (
        "diff --git a/src/a.ts b/src/a.ts\n"
        "diff --git a/src/b.ts b/src/b.ts\n"
    )
    req = real._build_request({
        "target_file": "src/a.ts", "change_summary": "change", "proposed_patch": patch,
    })
    assert req["candidate_actions"][0]["expected_files"] == ["src/a.ts", "src/b.ts"]


def test_build_request_does_not_carry_candidate_verification_in_untrusted_evidence():
    req = real._build_request({
        "target_file": "src/Numerics/SpecialFunctions/Gamma.cs",
        "change_summary": "safer gamma refactor",
        "proposed_patch": "diff --git a/src/Numerics/SpecialFunctions/Gamma.cs b/src/Numerics/SpecialFunctions/Gamma.cs\n",
        "candidate_verification": {
            "status": "passed",
            "checks": {"GammaTests": "passed", "numeric_equivalence_gamma": "passed"},
            "required_checks": ["GammaTests", "numeric_equivalence_gamma"],
            "domain": "numeric_equivalence",
        },
    })
    assert "candidate_verification" not in req["evidence"]


def test_real_advise_enables_semantic_diff_in_subprocess_env(monkeypatch, tmp_path):
    seen = {}

    def _assess(
        req_path, *, repo_root, db, trusted_candidate_verification_path=None,
        include_host_metadata=False, extra_env=None,
    ):
        seen["extra_env"] = dict(extra_env or {})
        seen["include_host_metadata"] = include_host_metadata
        return {"recommended_decision": "proceed", "scores": {"expected_loss": 0.0}}

    monkeypatch.setattr(real.cli_harness, "assess", _assess)
    out = real.advise({
        "target_file": "x.ts",
        "change_summary": "change x",
        "proposed_patch": "diff --git a/x.ts b/x.ts\n",
    }, repo_root=tmp_path, db=tmp_path / "pebra.db")

    assert out["recommended_decision"] == "proceed"
    assert seen["extra_env"] == {"PEBRA_CODEGRAPH_SEMANTIC_DIFF": "1"}
    assert seen["include_host_metadata"] is True


def test_real_advise_forwards_remaining_timeout_to_cli(monkeypatch, tmp_path):
    seen = {}

    def _assess(req_path, **kwargs):
        seen.update(kwargs)
        return {"recommended_decision": "proceed", "scores": {"expected_loss": 0.0}}

    monkeypatch.setattr(real.cli_harness, "assess", _assess)

    real.advise(
        {
            "target_file": "x.ts",
            "change_summary": "change x",
            "proposed_patch": "diff --git a/x.ts b/x.ts\n",
        },
        repo_root=tmp_path,
        db=tmp_path / "pebra.db",
        timeout_seconds=9.8,
    )

    assert seen["timeout"] == 9
