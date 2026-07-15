"""Pin the CLI-boundary assay parser to production's accepted unified-diff grammar."""

from e2e.experiments.agent_ab.patch_files import touched_files as e2e_touched_files
from pebra.core.patch_paths import touched_files as production_touched_files


def test_e2e_patch_parser_matches_production_across_supported_header_shapes() -> None:
    patches = (
        "--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1 +1 @@\n-old\n+new\n",
        (
            "diff --git a/src/a.ts b/src/a.ts\n"
            "--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
        ),
        (
            "diff --git a/src/a.ts b/src/a.ts\n"
            "--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/src/x b/y.ts b/src/x b/y.ts\n"
            "--- a/src/x b/y.ts\n+++ b/src/x b/y.ts\n@@ -1 +1 @@\n-old\n+new\n"
        ),
        (
            "diff --git a/src/old.ts b/src/new.ts\n"
            "similarity index 100%\nrename from src/old.ts\nrename to src/new.ts\n"
        ),
        "--- /dev/null\n+++ b/src/new.ts\n@@ -0,0 +1 @@\n+new\n",
        'diff --git "a/src/with space.ts" "b/src/with space.ts"\n',
        'diff --git "unterminated\n',
        "--- src/no-prefix.ts\n+++ b/src/no-prefix.ts\n",
    )

    assert [e2e_touched_files(patch) for patch in patches] == [
        production_touched_files(patch) for patch in patches
    ]


def test_e2e_patch_parser_matches_production_security_guards() -> None:
    patches = (
        (
            "*** Begin Patch\n"
            "diff --git a/src/a.ts b/src/a.ts\n"
            "--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
            "*** End Patch\n"
        ),
        "diff --git a/a b/b b/c\n",
    )

    assert [production_touched_files(patch) for patch in patches] == [(), ()]
    assert [e2e_touched_files(patch) for patch in patches] == [(), ()]
