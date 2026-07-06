"""The blinding-critical invariant: the treatment backend's AGENT-FACING output is structurally
identical to the sham's and carries no engine-identifying vocabulary. Uses a fixture PEBRA payload —
no real CLI call — by testing the pure `_shape_output`.
"""

from __future__ import annotations

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


def test_advisory_contract_requires_patch_evidence():
    assert "proposed_patch" in advisory_contract.INPUT_SCHEMA["required"]
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
