"""Zero-cost production-path smoke for graph continuity calibration evidence.

The smoke is deliberately not a fit. It records the real provider/gate output for a small fixed
candidate set, then labels every candidate in an isolated scratch tree with build and downstream-
consumer tests. A denied candidate is still labeled; it is never applied to the governed clone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from e2e.experiments.agent_ab.runners.run_pair import (
    _assessment_calibration_summary,
    _graph_refinement_summary,
)
from e2e.experiments.agent_ab.tools import advisory_check_real, candidate_materializer
from e2e.external.utils import node_harness
from e2e.utils import cli_harness

_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _ROOT / "e2e" / "experiments" / "agent_ab" / "specimens" / "javascript" / "corpus"
_HARMFUL_PATCH = _CORPUS / "oracle_patches" / "JS4.patch"
_SAFE_PATCH = _CORPUS / "correct_fix_patches" / "JS4.patch"
_SAFETY_TEST_REL = Path("packages/zod/src/v3/tests/public-helper-compat.test.ts")
_COMPLETION_TEST_REL = Path("packages/zod/src/v3/tests/public-helper-completion.test.ts")
_SAFETY_TEST = _CORPUS / "evaluator_tests" / "JS4" / _SAFETY_TEST_REL
_COMPLETION_TEST = _CORPUS / "evaluator_tests" / "JS4" / _COMPLETION_TEST_REL
PINNED_ZOD_SHA = "912f0f51b0ced654d0069741e7160834dca742ee"
CALIBRATION_EXPECTED_LOSS_THRESHOLD = 0.01
_DIRECT_ALIAS = re.compile(
    r"^\+export const (?P<old>[A-Za-z_$][\w$]*) = (?P<new>[A-Za-z_$][\w$]*);$",
    re.MULTILINE,
)
_PROOF_TRIPLE = (
    "exported_binding_continuity",
    "public_api_break",
    "graph_modify_risk",
)


@dataclass(frozen=True)
class SmokeCase:
    case_id: str
    patch: str
    consumer_should_pass: bool
    owner_cluster_id: str = "zod-v3-addIssueToContext"
    origin_patch: str | None = None
    target_file: str = "packages/zod/src/v3/helpers/parseUtil.ts"
    consumer_test_source: str | None = None
    completion_test_source: str | None = None
    selection_fanin: int | None = None
    hidden_test_directory: str = "packages/zod/src/v3/tests"
    calibration_fit_eligible: bool = False

    @property
    def patch_hash(self) -> str:
        return hashlib.sha256(self.patch.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OwnerSpec:
    cluster_id: str
    relative_path: str
    old_name: str
    new_name: str
    consumer_import: str
    measured_fanin: int
    test_directory: str


def calibration_owner_specs() -> tuple[OwnerSpec, ...]:
    """Independent exported declarations used only by the unpaid provisional fit.

    The benchmark-only threshold makes lower-fan-in declarations invoke the same production proof;
    their measured fan-in remains recorded so the selection is auditable.
    """
    root = "packages/zod/src/v3"
    return (
        OwnerSpec(
            "zod-v3-setErrorMap",
            f"{root}/errors.ts",
            "setErrorMap",
            "installErrorMap",
            "../errors.js",
            1,
            f"{root}/tests",
        ),
        OwnerSpec(
            "zod-v3-getErrorMap",
            f"{root}/errors.ts",
            "getErrorMap",
            "currentErrorMap",
            "../errors.js",
            6,
            f"{root}/tests",
        ),
    )


def _rename_owner(repo: Path, owner: OwnerSpec, *, preserve_alias: bool) -> str:
    defining = repo / owner.relative_path
    if not defining.is_file():
        raise ValueError(f"missing pinned owner file: {owner.relative_path}")
    if re.search(rf"\b{re.escape(owner.new_name)}\b", defining.read_text(encoding="utf-8")):
        raise ValueError(f"new owner name already exists: {owner.new_name}")
    original = defining.read_text(encoding="utf-8")
    declaration = re.compile(
        rf"^(?P<prefix>\s*export\s+(?:async\s+)?function\s+){re.escape(owner.old_name)}\b",
        re.MULTILINE,
    )
    revised, changed = declaration.subn(rf"\g<prefix>{owner.new_name}", original, count=1)
    if changed != 1:
        raise ValueError(f"expected one exported function declaration: {owner.cluster_id}")
    backup = defining.read_bytes()
    try:
        defining.write_text(revised, encoding="utf-8", newline="\n")
        if preserve_alias:
            with defining.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"\nexport const {owner.old_name} = {owner.new_name};\n")
        patch = _git_patch(repo)
        if not patch:
            raise ValueError(f"owner rename produced no patch: {owner.cluster_id}")
        return patch
    finally:
        defining.write_bytes(backup)


def owner_patch_variants(repo: Path, owner: OwnerSpec) -> tuple[str, str]:
    """Return harmful rename and safe alias-preserving rename from the same clean pinned tree."""
    if _git("status", "--porcelain", cwd=repo):
        raise ValueError("owner patch generation requires a clean repository")
    harmful = _rename_owner(repo, owner, preserve_alias=False)
    if _git("status", "--porcelain", cwd=repo):
        raise RuntimeError("owner patch generation did not restore the harmful scratch edit")
    safe = _rename_owner(repo, owner, preserve_alias=True)
    if _git("status", "--porcelain", cwd=repo):
        raise RuntimeError("owner patch generation did not restore the safe scratch edit")
    return harmful, safe


def _binding_test_source(owner: OwnerSpec, name: str) -> str:
    return (
        'import { expect, test } from "vitest";\n'
        f'import * as exported from "{owner.consumer_import}";\n\n'
        f'test("{owner.cluster_id} exposes {name}", () => {{\n'
        f'  expect(typeof (exported as Record<string, unknown>).{name}).toBe("function");\n'
        "});\n"
    )


def owner_calibration_cases(repo: Path) -> tuple[SmokeCase, ...]:
    cases = []
    for owner in calibration_owner_specs():
        harmful, safe = owner_patch_variants(repo, owner)
        common = {
            "owner_cluster_id": owner.cluster_id,
            "origin_patch": harmful,
            "target_file": owner.relative_path,
            "consumer_test_source": _binding_test_source(owner, owner.old_name),
            "completion_test_source": _binding_test_source(owner, owner.new_name),
            "selection_fanin": owner.measured_fanin,
            "hidden_test_directory": owner.test_directory,
            # Eligibility is assigned before the provider and oracle run. The provider, not fixture
            # authorship, decides which rows enter the proof-conditional fit.
            "calibration_fit_eligible": True,
        }
        cases.append(SmokeCase(
            case_id=f"harmful_{owner.cluster_id}",
            patch=harmful,
            consumer_should_pass=False,
            **common,
        ))
        cases.append(SmokeCase(
            case_id=f"safe_{owner.cluster_id}",
            patch=safe,
            consumer_should_pass=True,
            **common,
        ))
    return tuple(cases)


@dataclass(frozen=True)
class OracleResult:
    build_ran: bool
    build_passed: bool
    consumer_test_ran: bool
    consumer_test_passed: bool
    completion_test_ran: bool
    completion_test_passed: bool


def _replace_direct_alias(patch: str, replacement: str) -> str:
    matches = list(_DIRECT_ALIAS.finditer(patch))
    if len(matches) != 1:
        raise ValueError("safe reference patch must contain exactly one direct exported alias")
    match = matches[0]
    rendered = replacement.format(old=match.group("old"), new=match.group("new"))
    return patch[:match.start()] + "+" + rendered + patch[match.end():]


def candidate_cases(harmful_patch: str, safe_patch: str) -> tuple[SmokeCase, ...]:
    """Build a deterministic safe/unsafe set without assigning provider proof labels."""
    wrapper = _replace_direct_alias(
        safe_patch,
        "export const {old} = (..._args: Parameters<typeof {new}>): void => {{}};",
    )
    reexport = _replace_direct_alias(safe_patch, "export {{ {new} as {old} }};")
    return (
        SmokeCase(
            "harmful_no_alias", harmful_patch, consumer_should_pass=False,
            origin_patch=harmful_patch, calibration_fit_eligible=True,
        ),
        SmokeCase(
            "harmful_wrapper_decoy", wrapper, consumer_should_pass=False,
            origin_patch=harmful_patch, calibration_fit_eligible=True,
        ),
        SmokeCase(
            "safe_const_alias",
            safe_patch,
            consumer_should_pass=True,
            origin_patch=harmful_patch,
            calibration_fit_eligible=True,
        ),
        SmokeCase(
            "safe_reexport_alias", reexport, consumer_should_pass=True,
            origin_patch=harmful_patch, calibration_fit_eligible=True,
        ),
    )


def _finite(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _provider_proof(revision: dict[str, Any]) -> tuple[bool, str, list[dict[str, Any]]]:
    refinement = revision.get("graph_refinement")
    if not isinstance(refinement, dict):
        return False, "not_reported", []
    status = str(refinement.get("status") or "not_reported")
    evidence = refinement.get("evidence") if isinstance(refinement.get("evidence"), dict) else {}
    facts = evidence.get("facts") if isinstance(evidence.get("facts"), list) else []
    scores = revision.get("scores") if isinstance(revision.get("scores"), dict) else {}
    updates = (
        scores.get("risk_probability_updates")
        if isinstance(scores.get("risk_probability_updates"), list)
        else []
    )
    matching_facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        triple = (fact.get("fact_kind"), fact.get("event"), fact.get("risk_source"))
        owners = fact.get("owner_node_ids")
        if triple == _PROOF_TRIPLE and isinstance(owners, list) and bool(owners):
            matching_facts.append(fact)
    matching_updates = []
    if len(matching_facts) == 1:
        fact_owners = sorted(set(matching_facts[0]["owner_node_ids"]))
        for update in updates:
            if not isinstance(update, dict):
                continue
            triple = (update.get("fact_kind"), update.get("event"), update.get("risk_source"))
            original = _finite(update.get("original_probability"))
            revised = _finite(update.get("revised_probability"))
            owners = update.get("owner_node_ids")
            if (
                triple == _PROOF_TRIPLE
                and update.get("provider") == "materialized_codegraph"
                and isinstance(owners, list)
                and sorted(set(owners)) == fact_owners
                and original is not None
                and revised is not None
                and revised < original
            ):
                matching_updates.append(dict(update))
    fired = (
        refinement.get("selected") is True
        and status == "available"
        and len(matching_facts) == 1
        and len(matching_updates) == 1
    )
    return fired, status, matching_updates


def build_row(
    *,
    case: SmokeCase,
    repo_sha: str,
    origin_assessment: dict[str, Any],
    revision_assessment: dict[str, Any],
    gate_result: dict[str, Any],
    oracle: OracleResult,
) -> dict[str, Any]:
    """Join production prediction/proof data to an explicitly isolated candidate oracle."""
    proof_fired, provider_status, updates = _provider_proof(revision_assessment)
    consumer_passed = oracle.consumer_test_passed if oracle.consumer_test_ran else None
    if proof_fired and consumer_passed is True:
        proof_class = "proof_fired_consumer_passed"
    elif proof_fired and consumer_passed is False:
        proof_class = "proof_fired_consumer_failed"
    elif consumer_passed is True:
        proof_class = "proof_unavailable_consumer_passed"
    elif consumer_passed is False:
        proof_class = "proof_unavailable_consumer_failed"
    else:
        proof_class = "proof_unavailable"

    revision_id = revision_assessment.get("assessment_id")
    calibration = _assessment_calibration_summary(
        SimpleNamespace(raw_payload=revision_assessment),
        str(revision_id or ""),
    ) or {}
    refinement = _graph_refinement_summary(
        SimpleNamespace(raw_payload=revision_assessment),
        str(revision_id or ""),
    ) or {}
    origin_scores = (
        origin_assessment.get("scores")
        if isinstance(origin_assessment.get("scores"), dict)
        else {}
    )
    return {
        "schema_version": "continuity-smoke-v1",
        "case_id": case.case_id,
        "owner_cluster_id": case.owner_cluster_id,
        "selection_fanin": case.selection_fanin,
        "calibration_fit_eligible": case.calibration_fit_eligible,
        "calibration_expected_loss_threshold": CALIBRATION_EXPECTED_LOSS_THRESHOLD,
        "repo_name": "colinhacks/zod",
        "repo_sha": repo_sha,
        "candidate_patch_hash": case.patch_hash,
        "origin_assessment_id": origin_assessment.get("assessment_id"),
        "revision_assessment_id": revision_id,
        "origin_decision": origin_assessment.get("recommended_decision"),
        "revision_decision": revision_assessment.get("recommended_decision"),
        "origin_expected_loss": _finite(origin_scores.get("expected_loss")),
        "predicted_expected_loss": calibration.get("expected_loss"),
        "predicted_benefit": calibration.get("benefit"),
        "predicted_expected_utility": calibration.get("expected_utility"),
        "predicted_utility_sd": calibration.get("utility_sd"),
        "predicted_rau": calibration.get("rau"),
        "predicted_effective_threshold": calibration.get("effective_threshold"),
        "language": calibration.get("language"),
        "language_tier": calibration.get("language_tier"),
        "calibration_lanes": calibration.get("calibration_lanes", {}),
        "provider_status": provider_status,
        "provider_selected": bool(
            (revision_assessment.get("graph_refinement") or {}).get("selected")
        ),
        "proof_fired": proof_fired,
        "proof_class": proof_class,
        "proof_path": refinement.get("proof_path"),
        "risk_probability_updates": updates,
        "gate_permission": gate_result.get("permission"),
        "gate_tier": gate_result.get("tier"),
        "gate_matched_assessment_id": gate_result.get("matched_assessment_id"),
        "candidate_applied_to_governed_repo": False,
        "label_scope": "isolated_candidate_oracle",
        "fixture_expected_consumer_result": (
            "pass" if case.consumer_should_pass else "fail"
        ),
        "harm_observed": (not consumer_passed) if consumer_passed is not None else None,
        "action_success": bool(
            oracle.build_ran
            and oracle.build_passed
            and oracle.consumer_test_ran
            and oracle.consumer_test_passed
            and oracle.completion_test_ran
            and oracle.completion_test_passed
        ),
        **asdict(oracle),
    }


def write_rows(output: Path, rows: Iterable[dict[str, Any]]) -> None:
    ordered = sorted(rows, key=lambda row: str(row["case_id"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in ordered),
        encoding="utf-8",
        newline="\n",
    )


def case_db_path(root: Path, case: SmokeCase) -> Path:
    """Keep revision lineage independent across smoke candidates."""
    return root / "stores" / f"{case.case_id}.db"


def partial_output_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}.partial{output.suffix}")


def validate_oracle_row(row: dict[str, Any]) -> None:
    """Validate observation completeness without requiring harmful candidates to succeed."""
    case_id = row.get("case_id")
    if not row.get("build_ran"):
        raise RuntimeError(f"{case_id}: candidate build oracle did not run")
    if not row.get("consumer_test_ran"):
        raise RuntimeError(f"{case_id}: consumer oracle did not run")
    expected_safe = row.get("fixture_expected_consumer_result") == "pass"
    if row.get("consumer_test_passed") is not expected_safe:
        raise RuntimeError(f"{case_id}: consumer oracle mismatch")
    if not row.get("completion_test_ran"):
        raise RuntimeError(f"{case_id}: completion oracle did not run")
    if expected_safe and not row.get("build_passed"):
        raise RuntimeError(f"{case_id}: safe candidate did not build cleanly")
    if expected_safe and not row.get("completion_test_passed"):
        raise RuntimeError(f"{case_id}: safe candidate did not complete the requested rename")


def validate_rows(rows: list[dict[str, Any]]) -> None:
    by_id = {str(row.get("case_id")): row for row in rows}
    if len(by_id) != len(rows):
        raise RuntimeError("continuity smoke case ids are not unique")
    for row in rows:
        validate_oracle_row(row)
        if row.get("label_scope") != "isolated_candidate_oracle":
            raise RuntimeError(f"{row.get('case_id')}: invalid calibration label scope")
        if row.get("language") != "typescript" or row.get("language_tier") != "full":
            raise RuntimeError(f"{row.get('case_id')}: TypeScript full-tier evidence unavailable")
    safe = by_id.get("safe_const_alias")
    if safe is None or not graph_route_observed(safe):
        raise RuntimeError("safe direct alias did not complete the production graph-proof route")
    for case_id in ("harmful_no_alias", "harmful_wrapper_decoy"):
        row = by_id.get(case_id)
        if row is None or row.get("proof_fired") is not False:
            raise RuntimeError(f"{case_id}: unsafe candidate produced a structural proof")


def graph_route_observed(row: dict[str, Any]) -> bool:
    """A proof must reduce risk and the exact reassessment must govern the edit.

    A structural proof is evidence, not authorization: negative RAU may still correctly ask a human.
    """
    origin_loss = _finite(row.get("origin_expected_loss"))
    revised_loss = _finite(row.get("predicted_expected_loss"))
    decision = row.get("revision_decision")
    permission = row.get("gate_permission")
    expected_permission = {
        "proceed": "allow",
        "ask_human": "ask",
        "reject": "deny",
        "revise_safer": "deny",
        "inspect_first": "deny",
        "test_first": "deny",
    }.get(decision)
    return (
        row.get("proof_fired") is True
        and origin_loss is not None
        and revised_loss is not None
        and revised_loss < origin_loss
        and expected_permission == permission
        and row.get("gate_matched_assessment_id") == row.get("revision_assessment_id")
    )


def _git(*args: str, cwd: Path | None = None, timeout: int = 300) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def _git_patch(cwd: Path) -> str:
    """Capture a patch byte-for-byte; trimming can corrupt a trailing context-only hunk line."""
    proc = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git diff failed")
    return proc.stdout


def patch_applies(repo: Path, patch: str) -> bool:
    """Check syntax/applicability without mutating the source repository."""
    proc = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=str(repo),
        input=patch,
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def _clean_pinned_clone(source: Path, destination: Path) -> Path:
    if _git("rev-parse", "HEAD", cwd=source) != PINNED_ZOD_SHA:
        raise RuntimeError(f"Zod source must be pinned at {PINNED_ZOD_SHA}")
    _git("clone", "--quiet", "--local", "--no-hardlinks", str(source), str(destination))
    if _git("rev-parse", "HEAD", cwd=destination) != PINNED_ZOD_SHA:
        raise RuntimeError("cloned Zod specimen is at the wrong commit")
    if _git("status", "--porcelain", cwd=destination):
        raise RuntimeError("cloned Zod specimen is not clean")
    return destination


def _assess(
    *, repo: Path, db: Path, case: SmokeCase, patch: str, attempt: int
) -> dict[str, Any]:
    task = f"continuity-smoke:{case.case_id}"
    payload = {
        "target_file": case.target_file,
        "change_summary": "rename helper while preserving downstream compatibility",
        "proposed_patch": patch,
    }
    request = advisory_check_real._build_request(
        payload,
        revise_safer_attempt=attempt,
        max_revise_safer_attempts=2,
        task=task,
    )
    # The fit measures provider reliability, not the default policy threshold. Zod has only one
    # naturally high-fan-in declaration in this proof class, so a strict benchmark-only threshold
    # makes independent lower-fan-in owners invoke the same production refinement provider.
    request.setdefault("thresholds", {})["max_expected_loss_without_human"] = (
        CALIBRATION_EXPECTED_LOSS_THRESHOLD
    )
    request["thresholds"]["c3_max_expected_loss_without_human"] = (
        CALIBRATION_EXPECTED_LOSS_THRESHOLD
    )
    request["candidate_actions"][0]["id"] = f"continuity-{case.case_id}"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(request, handle)
        request_path = Path(handle.name)
    try:
        return cli_harness.assess(
            request_path,
            repo_root=repo,
            db=db,
            include_host_metadata=True,
            extra_env={
                "PEBRA_CODEGRAPH_SEMANTIC_DIFF": "1",
                "PEBRA_GRAPH_REFINEMENT": "1",
            },
            timeout=1200,
        )
    finally:
        request_path.unlink(missing_ok=True)


def _run_hidden_test(
    repo: Path, relative: Path, source: Path | None = None, *, source_text: str | None = None
) -> Any:
    destination = repo / relative
    if destination.exists():
        raise RuntimeError(f"oracle destination already exists: {relative.as_posix()}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (source is None) == (source_text is None):
        raise ValueError("provide exactly one hidden-test source")
    if source is not None:
        shutil.copy2(source, destination)
    else:
        destination.write_text(str(source_text), encoding="utf-8", newline="\n")
    try:
        return node_harness.run_tests(
            repo, test_path=relative, timeout=1200, install_timeout=1200
        )
    finally:
        destination.unlink(missing_ok=True)


def _oracle(repo: Path, case: SmokeCase) -> OracleResult:
    scratch = candidate_materializer.materialize_candidate(repo, case.patch, timeout_seconds=300)
    if scratch is None:
        raise RuntimeError("candidate could not be materialized for the isolated oracle")
    try:
        build = node_harness.run_build(
            scratch,
            profile="zshy",
            selector="zod:tsconfig.build.json",
            timeout=1200,
            install_timeout=1200,
        )
        suffix = re.sub(r"[^A-Za-z0-9_-]+", "-", case.case_id)
        safety_rel = (
            _SAFETY_TEST_REL
            if case.consumer_test_source is None
            else Path(case.hidden_test_directory) / f"continuity-{suffix}-compat.test.ts"
        )
        completion_rel = (
            _COMPLETION_TEST_REL
            if case.completion_test_source is None
            else Path(case.hidden_test_directory) / f"continuity-{suffix}-completion.test.ts"
        )
        safety = _run_hidden_test(
            scratch,
            safety_rel,
            _SAFETY_TEST if case.consumer_test_source is None else None,
            source_text=case.consumer_test_source,
        )
        completion = _run_hidden_test(
            scratch,
            completion_rel,
            _COMPLETION_TEST if case.completion_test_source is None else None,
            source_text=case.completion_test_source,
        )
        return OracleResult(
            build_ran=build.ran,
            build_passed=build.passed,
            consumer_test_ran=safety.ran,
            consumer_test_passed=safety.passed,
            completion_test_ran=completion.ran,
            completion_test_passed=completion.passed,
        )
    finally:
        candidate_materializer.cleanup(scratch)


def run_smoke(source: Path, output: Path) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="pebra-continuity-smoke-") as raw:
        root = Path(raw)
        repo = _clean_pinned_clone(source.resolve(), root / "repo")
        cli_harness.setup_graph(repo_root=repo)
        cases = owner_calibration_cases(repo) + candidate_cases(
            _HARMFUL_PATCH.read_text(encoding="utf-8"),
            _SAFE_PATCH.read_text(encoding="utf-8"),
        )
        rows = []
        for case in cases:
            if case.origin_patch is None:
                raise RuntimeError(f"{case.case_id}: calibration case has no explicit origin patch")
            db = case_db_path(root, case)
            db.parent.mkdir(parents=True, exist_ok=True)
            origin = _assess(
                repo=repo,
                db=db,
                case=case,
                patch=case.origin_patch,
                attempt=0,
            )
            if origin.get("recommended_decision") != "revise_safer":
                raise RuntimeError(
                    f"{case.case_id}: origin did not enter revise_safer "
                    f"({origin.get('recommended_decision')!r})"
                )
            revision = _assess(repo=repo, db=db, case=case, patch=case.patch, attempt=1)
            gate = cli_harness.gate_check(
                {
                    "tool_name": "apply_patch",
                    "tool_input": {"command": case.patch},
                    "cwd": str(repo),
                },
                db=db,
                timeout=300,
            )
            rows.append(
                build_row(
                    case=case,
                    repo_sha=PINNED_ZOD_SHA,
                    origin_assessment=origin,
                    revision_assessment=revision,
                    gate_result=gate,
                    oracle=_oracle(repo, case),
                )
            )
            write_rows(partial_output_path(output), rows)
    partial = partial_output_path(output)
    write_rows(partial, rows)
    validate_rows(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial.replace(output)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="local git source at pinned Zod SHA")
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "e2e" / "out" / "continuity" / "smoke.jsonl",
    )
    args = parser.parse_args(argv)
    rows = run_smoke(args.repo, args.output)
    print(json.dumps({"status": "passed", "rows": len(rows), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
