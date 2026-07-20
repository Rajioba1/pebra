"""Launch an isolated, purpose-built PEBRA Observatory demo.

This developer utility never resolves the current checkout and never opens its
``.pebra/pebra.db``. It creates a dedicated temporary ledger and launches one
of the existing read-only Observatory surfaces against that explicit database.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pebra.adapters.store.db import SqliteStore
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult
from pebra.observatory_context import OBSERVATORY_LABEL_ENV

DEMO_LABEL_ENV = OBSERVATORY_LABEL_ENV


@dataclass(frozen=True)
class DemoWorkspace:
    root: Path
    db_path: Path
    repo_id: str
    label: str
    assessment_count: int


_ROWS = (
    (
        "Tighten login token validation",
        "src/auth/token.py",
        Decision.PROCEED,
        {"rau": 0.34, "expected_loss": 0.08, "benefit": 0.78},
        "completed",
    ),
    (
        "Inspect callers before changing cache keys",
        "src/cache/keys.py",
        Decision.INSPECT_FIRST,
        {"rau": 0.04, "expected_loss": 0.31, "benefit": 0.46},
        None,
    ),
    (
        "Add regression coverage for invoice rounding",
        "tests/test_invoice_rounding.py",
        Decision.TEST_FIRST,
        {"rau": 0.11, "expected_loss": 0.18, "benefit": 0.52},
        "completed",
    ),
    (
        "Narrow the public session migration",
        "src/session/migrate.py",
        Decision.REVISE_SAFER,
        {"rau": -0.17, "expected_loss": 0.63, "benefit": 0.49},
        None,
    ),
    (
        "Confirm destructive cleanup with an operator",
        "src/storage/cleanup.py",
        Decision.ASK_HUMAN,
        {"rau": -0.42, "expected_loss": 0.81, "benefit": 0.39},
        "skipped",
    ),
    (
        "Reject an unbounded credential rewrite",
        "src/security/credentials.py",
        Decision.REJECT,
        {"rau": -0.79, "expected_loss": 0.94, "benefit": 0.15},
        "rejected",
    ),
)


def _candidate_digest(task: str, target: str) -> str:
    return hashlib.sha256(f"{task}\0{target}".encode()).hexdigest()


def prepare_demo(root: Path) -> DemoWorkspace:
    """Create a fresh isolated demo ledger under ``root``."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "pebra-demo.db"
    if db_path.exists():
        raise FileExistsError(f"refusing to replace existing demo database: {db_path}")
    repo_id = "repo_demo_" + hashlib.sha256(os.fsencode(root)).hexdigest()[:12]
    store = SqliteStore(str(db_path))
    try:
        for index, (task, target, decision, scores, outcome) in enumerate(_ROWS, start=1):
            digest = _candidate_digest(task, target)
            result = AssessmentResult(
                recommended_decision=decision,
                requires_confirmation=decision in {Decision.ASK_HUMAN, Decision.REJECT},
                action_status=ActionStatus.PENDING,
                risk_mode=(
                    RiskMode.NORMAL
                    if index <= 3
                    else RiskMode.ELEVATED_REVIEW
                ),
                scores=dict(scores),
                repo_id=repo_id,
                repo_root="/isolated/pebra-demo",
                assessed_commit=hashlib.sha1(f"demo-{index}".encode()).hexdigest(),  # noqa: S324
                model_guidance_packet={
                    "decision": decision.value,
                    "binding": {
                        "candidate": {
                            "algorithm": CANDIDATE_BINDING_ALGORITHM,
                            "files": {target: digest},
                        }
                    },
                },
            )
            assessment_id = store.persist_assessment(
                result,
                {
                    "task": task,
                    "action_id": f"demo-action-{index}",
                    "revision_envelope": {"expected_files": [target]},
                },
            )
            if outcome is not None:
                store.record_outcome(
                    assessment_id,
                    outcome,
                    {"demo": True, "summary": f"Purpose-built {outcome} example"},
                )
    finally:
        store.close()
    return DemoWorkspace(root, db_path, repo_id, "DEMO", len(_ROWS))


def launch_spec(
    demo: DemoWorkspace, *, surface: str
) -> tuple[list[str], dict[str, str]]:
    if surface not in {"tui", "dashboard"}:
        raise ValueError(f"unknown Observatory surface: {surface}")
    command = [
        sys.executable,
        "-m",
        "pebra",
        surface,
        "--read-only",
        "--db",
        str(demo.db_path),
        "--repo-id",
        demo.repo_id,
    ]
    if surface == "dashboard":
        command.append("--open")
    env = os.environ.copy()
    env[DEMO_LABEL_ENV] = demo.label
    return command, env


def launch_demo(demo: DemoWorkspace, *, surface: str) -> int:
    command, env = launch_spec(demo, surface=surface)
    print(f"PEBRA DEMO: {demo.assessment_count} synthetic assessments in {demo.db_path}")
    try:
        return subprocess.run(command, env=env, check=False).returncode
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    surfaces = parser.add_mutually_exclusive_group()
    surfaces.add_argument("--tui", action="store_const", const="tui", dest="surface")
    surfaces.add_argument("--dashboard", action="store_const", const="dashboard", dest="surface")
    parser.set_defaults(surface="tui")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Retain the isolated demo directory after the viewer exits and print its path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.keep:
        root = Path(tempfile.mkdtemp(prefix="pebra-observatory-demo-"))
        demo = prepare_demo(root)
        print(f"PEBRA DEMO retained at: {root}")
        return launch_demo(demo, surface=args.surface)
    with tempfile.TemporaryDirectory(prefix="pebra-observatory-demo-") as raw:
        return launch_demo(prepare_demo(Path(raw)), surface=args.surface)


if __name__ == "__main__":
    raise SystemExit(main())
