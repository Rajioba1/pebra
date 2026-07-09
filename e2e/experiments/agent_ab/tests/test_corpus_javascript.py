"""The JS/TS (Zod) corpus validates through the shared loader.

The JS1 trap was manually proven against Zod's zshy type-check build; this test pins the checked-in
metadata/patch shape so the live preflight can repeat that proof when the Zod specimen is selected.
"""

from __future__ import annotations

from pathlib import Path

from e2e.experiments.agent_ab.specimens.javascript.corpus import loader

_CORPUS = Path(loader.__file__).resolve().parent


def test_js_corpus_loads_and_validates():
    specs = {s.task_id: s for s in loader.load_corpus()}
    assert {"JS1", "JS2", "JS3"} == set(specs)
    js1 = specs["JS1"]
    assert js1.harm_label == "risky" and js1.oracle_build_must_fail is True
    assert js1.language == "typescript" and js1.harness_id == "node"
    assert js1.build_profile == "zshy" and js1.build_selector == "zod:tsconfig.build.json"
    assert js1.required_language_tier == "full"
    assert js1.specimen == "javascript"
    assert js1.repo_identity_files == ("package.json", "pnpm-lock.yaml")
    assert js1.build_solution == ""
    assert js1.expected_edit_scope == ("packages/zod/src/v3/types.ts",)
    assert specs["JS2"].harm_label == "safe" and specs["JS2"].oracle_build_must_fail is False
    assert specs["JS3"].harm_label == "safe"


def test_js1_patches_exist_and_target_only_scope():
    oracle = (_CORPUS / "oracle_patches" / "JS1.patch").read_text(encoding="utf-8")
    fix = (_CORPUS / "correct_fix_patches" / "JS1.patch").read_text(encoding="utf-8")
    # both edit exactly the scope file
    assert "packages/zod/src/v3/types.ts" in oracle and "packages/zod/src/v3/types.ts" in fix
    for patch in (oracle, fix):
        touched = {ln.split()[2][2:] for ln in patch.splitlines() if ln.startswith("diff --git ")}
        assert touched == {"packages/zod/src/v3/types.ts"}
    assert "abstract _schemaTypeLabel" in oracle        # harmful widening (breaks subclasses)
    assert "pebra" not in oracle.lower()
    assert "pebra" not in fix.lower()
    assert 'return "schema"' in fix                      # concrete default (all subclasses inherit)


def test_safe_patches_exist():
    for tid in ("JS2", "JS3"):
        assert (_CORPUS / "oracle_patches" / f"{tid}.patch").is_file()
