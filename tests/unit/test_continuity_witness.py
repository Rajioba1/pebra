from __future__ import annotations

import pytest

from pebra.adapters.continuity_witness import witness_for_language


@pytest.mark.parametrize("language", ["javascript", "jsx", "typescript", "tsx"])
def test_ecmascript_family_uses_one_measured_witness(language: str) -> None:
    witness = witness_for_language(language)

    assert witness is not None
    assert witness.name == "ecmascript"
    assert witness.version == "1"
    assert witness.forwarder_kinds == frozenset({"constant", "variable"})


@pytest.mark.parametrize(
    ("language", "version"),
    [("java", "2"), ("rust", "1"), ("go", "1"), ("dart", "2"), ("scala", "1")],
)
def test_callable_forwarder_families_have_explicit_witnesses(
    language: str, version: str
) -> None:
    witness = witness_for_language(language)

    assert witness is not None
    assert witness.name == language
    assert witness.version == version
    assert witness.forwarder_kinds <= frozenset({"function", "method"})


def test_unknown_language_has_no_continuity_witness() -> None:
    assert witness_for_language("python") is None
    assert witness_for_language("pascal") is None


def test_ecmascript_witness_rejects_same_signature_different_implementation() -> None:
    witness = witness_for_language("typescript")
    assert witness is not None

    assert witness.same_implementation(
        "export function oldName(x: number): number { return x + 1; }",
        "export function newName(x: number): number { return x - 1; }",
        "oldName",
        "newName",
    ) is False


@pytest.mark.parametrize(
    ("language", "old_name", "new_name", "old_source", "new_source"),
    [
        (
            "java", "oldName", "newName",
            "public static int oldName(int x) { return x + 1; }",
            "public static int newName(int x) { return x + 1; }",
        ),
        (
            "rust", "old_name", "new_name",
            "pub fn old_name(x: i32) -> i32 { x + 1 }",
            "pub fn new_name(x: i32) -> i32 { x + 1 }",
        ),
        (
            "go", "OldName", "NewName",
            "func OldName(x int) int { return x + 1 }",
            "func NewName(x int) int { return x + 1 }",
        ),
        (
            "dart", "oldName", "newName",
            "int oldName(int x) => x + 1;",
            "int newName(int x) => x + 1;",
        ),
        (
            "scala", "oldName", "newName",
            "def oldName(x: Int): Int = x + 1",
            "def newName(x: Int): Int = x + 1",
        ),
    ],
)
def test_language_witnesses_compare_renamed_implementations(
    language: str,
    old_name: str,
    new_name: str,
    old_source: str,
    new_source: str,
) -> None:
    witness = witness_for_language(language)
    assert witness is not None

    assert witness.same_implementation(old_source, new_source, old_name, new_name) is True


@pytest.mark.parametrize(
    ("language", "old_name", "new_name", "source"),
    [
        ("typescript", "oldName", "newName", "export const oldName = newName;"),
        (
            "java", "oldName", "newName",
            "public static int oldName(int x) { return newName(x); }",
        ),
        ("rust", "old_name", "new_name", "pub fn old_name(x: i32) -> i32 { new_name(x) }"),
        ("go", "OldName", "NewName", "func OldName(x int) int { return NewName(x) }"),
        ("dart", "oldName", "newName", "int oldName(int x) => newName(x);"),
        ("scala", "oldName", "newName", "def oldName(x: Int): Int = newName(x)"),
    ],
)
def test_language_witnesses_accept_only_direct_parameter_forwarding(
    language: str, old_name: str, new_name: str, source: str
) -> None:
    witness = witness_for_language(language)
    assert witness is not None

    assert witness.is_safe_forwarder(source, old_name, new_name) is True


@pytest.mark.parametrize(
    ("language", "old_name", "new_name", "source"),
    [
        (
            "java", "oldName", "newName",
            "public static int oldName(int x) { return newName(x + 1); }",
        ),
        ("rust", "old_name", "new_name", "pub fn old_name(x: i32) -> i32 { new_name(x + 1) }"),
        ("go", "OldName", "NewName", "func OldName(x int) int { log(x); return NewName(x) }"),
        ("dart", "oldName", "newName", "int oldName(int x) => newName(x + 1);"),
        ("scala", "oldName", "newName", "def oldName(x: Int): Int = newName(x + 1)"),
    ],
)
def test_language_witnesses_reject_behavioral_wrappers(
    language: str, old_name: str, new_name: str, source: str
) -> None:
    witness = witness_for_language(language)
    assert witness is not None

    assert witness.is_safe_forwarder(source, old_name, new_name) is False


@pytest.mark.parametrize(
    ("language", "old_name", "new_name", "old_source", "new_source", "smuggled"),
    [
        (
            "java",
            "oldName",
            "newName",
            "public static int oldName(int x) { return x + 1; }",
            "public static int newName(int x) { return x + 1; }",
            "static { System.exit(leakSecrets()); } public static int oldName(int x) "
            "{ return newName(x); }",
        ),
        (
            "dart",
            "oldName",
            "newName",
            "int oldName(int x) => x + 1;",
            "int newName(int x) => x + 1;",
            "final stolen = leakSecrets(); int oldName(int x) => newName(x);",
        ),
    ],
)
def test_patch_proof_rejects_code_smuggled_before_forwarder(
    language: str,
    old_name: str,
    new_name: str,
    old_source: str,
    new_source: str,
    smuggled: str,
) -> None:
    witness = witness_for_language(language)
    assert witness is not None
    patch = (
        "diff --git a/Api b/Api\n"
        "--- a/Api\n+++ b/Api\n"
        "@@ -1 +1,2 @@\n"
        f"-{old_source}\n"
        f"+{new_source}\n"
        f"+{smuggled}\n"
    )

    assert witness.patch_is_exhaustive_forwarder(
        patch, old_name, new_name, smuggled
    ) is False


def test_ecmascript_witness_rejects_regex_identifier_migration() -> None:
    witness = witness_for_language("typescript")
    assert witness is not None

    assert witness.identifier_only_migration(
        "export function oldName(): RegExp { return /oldName/; }",
        "export function newName(): RegExp { return /newName/; }",
        "oldName",
        "newName",
    ) is False


def test_ecmascript_witness_handles_jsx_closing_tags_without_accepting_regexes() -> None:
    witness = witness_for_language("tsx")
    assert witness is not None

    assert witness.identifier_only_migration(
        "export function oldName() { return <span><b /></span>; }",
        "export function newName() { return <span><b /></span>; }",
        "oldName",
        "newName",
    ) is True


def test_patch_proof_requires_exactly_one_safe_forwarder() -> None:
    witness = witness_for_language("java")
    assert witness is not None
    forwarder = "public static int oldName(int x) { return newName(x); }"
    patch = (
        "diff --git a/Api.java b/Api.java\n"
        "--- a/Api.java\n+++ b/Api.java\n"
        "@@ -1 +1,2 @@\n"
        "-public static int oldName(int x) { return x + 1; }\n"
        "+public static int newName(int x) { return x + 1; }\n"
        f"+{forwarder}\n"
    )

    assert witness.patch_is_exhaustive_forwarder(
        patch, "oldName", "newName", forwarder
    ) is True
    assert witness.patch_is_exhaustive_forwarder(
        patch + f"+{forwarder}\n", "oldName", "newName", forwarder
    ) is False
