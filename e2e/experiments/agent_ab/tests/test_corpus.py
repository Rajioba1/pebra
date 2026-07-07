from __future__ import annotations

import pytest

from e2e.experiments.agent_ab.corpus import loader


def test_default_corpus_loads_and_joins():
    specs = {s.task_id: s for s in loader.load_corpus()}
    assert {"T1", "T2", "B1", "B2", "MNGAMMA"} <= set(specs)
    assert specs["T1"].harm_label == "risky" and specs["T1"].expected_edit_scope
    assert specs["B1"].harm_label == "safe" and specs["B1"].oracle_build_must_fail is False
    assert specs["MNGAMMA"].harm_type == "test_failure"
    assert specs["MNGAMMA"].evaluator_test_filter == "FullyQualifiedName~GammaTests"
    assert specs["MNGAMMA"].build_solution == "MathNet.Numerics.sln"


def test_risky_contract_tasks_allow_known_dependent_scope():
    specs = {s.task_id: s for s in loader.load_corpus()}
    assert "src/TemplateBlueprint.AppShell/ViewModels/WorkspaceViewModel.cs" in specs["T1"].expected_edit_scope
    assert "src/TemplateBlueprint.AppShell/ViewModels/WorkspaceManager.cs" in specs["T1"].expected_edit_scope
    assert "src/TemplateBlueprint.Controls/Extensions/GridSearchAdapter.cs" in specs["T2"].expected_edit_scope


def test_safe_tasks_disambiguate_duplicate_helper_name():
    specs = {s.task_id: s for s in loader.load_corpus()}
    for task_id in ("B1", "B2"):
        description = specs[task_id].description
        assert "CsvImportService.cs" in description
        assert "Do not modify ExcelImportService" in description


def _write(tmp_path, tasks, oracles):
    t = tmp_path / "tasks.jsonl"
    o = tmp_path / "oracles.jsonl"
    t.write_text("\n".join(tasks), encoding="utf-8")
    o.write_text("\n".join(oracles), encoding="utf-8")
    return t, o


def test_forbidden_word_in_task_text_is_rejected(tmp_path):
    t, o = _write(
        tmp_path,
        ['{"task_id":"X","description":"use the graph engine","target_hints":["a.cs"]}'],
        ['{"task_id":"X","harm_label":"risky","expected_edit_scope":["a.cs"],'
         '"harm_type":"build_failure","oracle_build_must_fail":true}'],
    )
    with pytest.raises(loader.CorpusError, match="leaks"):
        loader.load_corpus(t, o)


def test_forbidden_oracle_word_in_task_text_is_rejected(tmp_path):
    # "oracle" would tell the agent its actions are being scored against hidden labels.
    t, o = _write(
        tmp_path,
        ['{"task_id":"X","description":"update the oracle config value","target_hints":["a.cs"]}'],
        ['{"task_id":"X","harm_label":"safe","expected_edit_scope":["a.cs"],'
         '"harm_type":"none","oracle_build_must_fail":false}'],
    )
    with pytest.raises(loader.CorpusError, match="leaks"):
        loader.load_corpus(t, o)


def test_missing_oracle_is_rejected(tmp_path):
    t, o = _write(
        tmp_path,
        ['{"task_id":"X","description":"add a parameter","target_hints":["a.cs"]}'],
        ['{"task_id":"Y","harm_label":"safe","expected_edit_scope":["a.cs"],'
         '"harm_type":"none","oracle_build_must_fail":false}'],
    )
    with pytest.raises(loader.CorpusError, match="no oracle"):
        loader.load_corpus(t, o)


def test_duplicate_oracle_id_is_rejected(tmp_path):
    t, o = _write(
        tmp_path,
        ['{"task_id":"X","description":"add a parameter","target_hints":["a.cs"]}'],
        ['{"task_id":"X","harm_label":"safe","expected_edit_scope":["a.cs"],'
         '"harm_type":"none","oracle_build_must_fail":false}',
         '{"task_id":"X","harm_label":"risky","expected_edit_scope":["a.cs"],'
         '"harm_type":"build_failure","oracle_build_must_fail":true}'],
    )
    with pytest.raises(loader.CorpusError, match="duplicate oracle"):
        loader.load_corpus(t, o)


def test_safe_task_that_must_fail_build_is_rejected(tmp_path):
    t, o = _write(
        tmp_path,
        ['{"task_id":"X","description":"add a parameter","target_hints":["a.cs"]}'],
        ['{"task_id":"X","harm_label":"safe","expected_edit_scope":["a.cs"],'
         '"harm_type":"none","oracle_build_must_fail":true}'],
    )
    with pytest.raises(loader.CorpusError, match="must not be expected to break"):
        loader.load_corpus(t, o)


def test_test_failure_task_requires_hidden_test_config(tmp_path):
    t, o = _write(
        tmp_path,
        ['{"task_id":"X","description":"refactor a method","target_hints":["a.cs"]}'],
        ['{"task_id":"X","harm_label":"risky","expected_edit_scope":["a.cs"],'
         '"harm_type":"test_failure","oracle_build_must_fail":false}'],
    )
    with pytest.raises(loader.CorpusError, match="test_failure"):
        loader.load_corpus(t, o)


def test_required_language_tier_loads_when_valid(tmp_path):
    t, o = _write(
        tmp_path,
        ['{"task_id":"X","description":"refactor a method","target_hints":["a.ts"]}'],
        ['{"task_id":"X","harm_label":"risky","expected_edit_scope":["a.ts"],'
         '"harm_type":"test_failure","oracle_build_must_fail":false,'
         '"evaluator_test_project":"tests/Tests.csproj","required_language_tier":"full"}'],
    )

    [spec] = loader.load_corpus(t, o)

    assert spec.required_language_tier == "full"


def test_required_language_tier_rejects_unknown_values(tmp_path):
    t, o = _write(
        tmp_path,
        ['{"task_id":"X","description":"refactor a method","target_hints":["a.ts"]}'],
        ['{"task_id":"X","harm_label":"risky","expected_edit_scope":["a.ts"],'
         '"harm_type":"test_failure","oracle_build_must_fail":false,'
         '"evaluator_test_project":"tests/Tests.csproj","required_language_tier":"semantic"}'],
    )

    with pytest.raises(loader.CorpusError, match="required_language_tier"):
        loader.load_corpus(t, o)
