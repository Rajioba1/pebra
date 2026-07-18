# PEBRA CLI Help And 0.1.1 Release Design

## Goal

Make the complete PEBRA command surface discoverable from the README, CLI, and Observatory TUI, then
publish the post-`v0.1.0` fixes as `0.1.1` through the existing verified release workflow.

## User-facing behavior

- `pebra --help` lists `--version, -V` without computing provenance while the parser is built.
- Invoking either version flag computes the existing provenance line lazily and exits successfully.
- The Observatory footer displays a clickable `? pebra --help` binding beside the existing refresh and
  quit bindings.
- Activating that footer binding opens Textual's existing help panel; it does not launch a subprocess or
  a second PEBRA process.
- The README documents regular and editable TUI launch, version inspection, and the three help levels:
  `pebra --help`, `pebra help <command>`, and `pebra help --all`.

## Implementation boundaries

- Add one small custom `argparse.Action` for lazy version rendering and register it on the root parser.
  Remove the separate pre-parser version shortcut once behavior and laziness are covered by tests.
- Reuse Textual's built-in `show_help_panel` action for the footer binding. Do not add a custom screen,
  subprocess execution, or a second help system.
- Keep package metadata single-sourced in `pyproject.toml`; bump it from `0.1.0` to `0.1.1`.
- Update version-specific development and release examples that would otherwise point at the old wheel.
  Test fixtures that intentionally model a self-contained `0.1.0` archive remain unchanged.
- Keep all work on `main`, matching the repository workflow requested by the maintainer.

## Verification

- CLI tests prove the version flags appear in root help, both flags render provenance, parser construction
  does not import Textual or invoke git, and every registered command remains discoverable.
- TUI integration tests prove the footer binding is visible and activating it opens the existing help panel
  without disturbing quit/refresh behavior.
- Documentation and distribution checks prove the README commands are accurate and the built archives and
  installed wheel report `0.1.1`.
- Run the full local test, lint, fast E2E, and distribution lanes before commit and push.
- Require successful Ubuntu, Windows, and macOS GitHub CI for the release commit before tagging.

## Release safeguards and sequence

Before publication, align repository settings with `RELEASING.md`: require a reviewer for the `pypi`
environment and enable immutable GitHub releases. Then create and push annotated tag `v0.1.1`, run the
release workflow from `main`, smoke-test the TestPyPI candidate, approve production, and verify that PyPI
and TestPyPI contain byte-identical `0.1.1` artifacts.

If the TestPyPI candidate bytes must change, do not reuse `0.1.1`; fix the issue and publish a new patch
version. Recovery of the same already-tested bytes uses the workflow's existing candidate recovery path.
