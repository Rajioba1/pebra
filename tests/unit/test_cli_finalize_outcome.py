from __future__ import annotations

import json
from types import SimpleNamespace

from pebra.cli import finalize_outcome as cmd
from pebra.cli import main


class _Store:
    def close(self):
        pass


def test_finalize_outcome_command_reads_trusted_sidecar(monkeypatch, tmp_path, capsys) -> None:
    sidecar = tmp_path / "outcome.json"
    sidecar.write_text(json.dumps({
        "assessment_id": "asm_1", "status": "skipped",
        "detail": {"actual_success": False},
    }), encoding="utf-8")
    context = SimpleNamespace(store=_Store(), repo=SimpleNamespace(repo_id="r"))
    seen = {}
    monkeypatch.setattr(cmd.composition, "resolve_repo_and_db", lambda *_a: context)
    monkeypatch.setattr(cmd.learning_composition, "build_learning_port", lambda _ctx: object())

    def finalize(assessment_id, status, **kwargs):
        seen.update(assessment_id=assessment_id, status=status, detail=kwargs["detail"])
        return SimpleNamespace(
            outcome_recorded=True, measurement_recorded=True, observed=1, censored=0,
            promotions={
                key: SimpleNamespace(promoted=False, snapshot_id=None)
                for key in ("risk", "benefit", "review_cost")
            },
        )

    monkeypatch.setattr(cmd.finalize_outcome_controller, "finalize_outcome", finalize)
    args = main.build_parser().parse_args([
        "finalize-outcome", "--trusted-outcome-file", str(sidecar), "--json",
    ])
    assert cmd.run(args) == 0
    assert seen == {"assessment_id": "asm_1", "status": "skipped",
                    "detail": {"actual_success": False}}
    assert json.loads(capsys.readouterr().out)["measurement_recorded"] is True


def test_finalize_outcome_rejects_bad_sidecar_shape(tmp_path) -> None:
    sidecar = tmp_path / "outcome.json"
    sidecar.write_text("[]", encoding="utf-8")
    args = main.build_parser().parse_args([
        "finalize-outcome", "--trusted-outcome-file", str(sidecar),
    ])
    assert cmd.run(args) == 2
