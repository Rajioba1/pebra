from e2e.experiments.agent_ab.path_scope import is_in_scope


def test_scope_supports_exact_files_and_bounded_subtrees():
    scope = ("src/primary.ts", "src/v3/")
    assert is_in_scope("./src/primary.ts", scope)
    assert is_in_scope("src\\v3\\helpers\\new.ts", scope)
    assert not is_in_scope("src/v30/new.ts", scope)
    assert not is_in_scope("tests/new.ts", scope)


def test_scope_rejects_traversal_absolute_drive_and_ads_paths():
    scope = ("src/v3/",)
    for path in (
        "src/v3/../outside.ts",
        "/src/v3/file.ts",
        "C:/src/v3/file.ts",
        "C:src/v3/file.ts",
        "src/v3/file.ts:stream",
    ):
        assert not is_in_scope(path, scope)
