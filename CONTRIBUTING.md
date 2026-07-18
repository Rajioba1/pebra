# Contributing To PEBRA

PEBRA accepts focused contributions that preserve its deterministic decision core, explicit trust
boundaries, and testable CLI behavior.

## Contribution Terms

PEBRA accepts contributions under the Apache License 2.0, without additional terms or conditions.
Alternative terms require prior written agreement from a project maintainer. By submitting a
contribution, you confirm that you have the **right to submit** it under the accepted terms.

Apache License 2.0 permits commercial use, modification, and redistribution subject to its terms.
Do not submit code, data, generated artifacts, or other material that you do not have the right to
license. Identify copied or adapted third-party material and preserve its required notices.

## Development Setup

Use Python 3.11 or newer. A minimal Windows setup is:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install pytest pytest-cov hypothesis syrupy jsonschema ruff import-linter nox textual-dev pytest-textual-snapshot==1.1.0
```

Use the equivalent activation and executable paths on macOS or Linux.

Run `nox -s tests lint e2e-fast` for the normal source checkout. Before release, run
`nox -s dev-package` to build and verify the tracked source as a clean wheel and source distribution;
use `nox -s dev-package -- --open` to open the installed wheel's dashboard.

### Developing the Observatory TUI

`pebra tui` is a Textual surface. Launch the editable checkout directly from the repository root:

```powershell
.\.venv\Scripts\pebra.exe tui --repo-root .
```

The equivalent module form is
`.\.venv\Scripts\python.exe -m pebra tui --repo-root .`. For an explicitly bound read-only store, use
`--read-only --db path\to\pebra.db --repo-id <id>` instead of `--repo-root .`.

For hot reload and the Textual dev console, run these in two terminals from the repository root:

```powershell
# Terminal 1: Textual events and self.log(...) output
.\.venv\Scripts\textual.exe console

# Terminal 2: make the editable console script visible to Textual's child process
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
.\.venv\Scripts\textual.exe run --dev -c "pebra tui --repo-root ."
```

The `--dev` run hot-reloads `pebra/tui/theme.tcss`, and `self.log(...)` output routes to the console
instead of corrupting the TUI. TUI diagnostics log identifiers, counts, timing, and error categories
only — never source, tokens, candidate payloads, or sanction data (the TUI never handles those). After a
deliberate visual change, regenerate the SVG baselines with
`.\.venv\Scripts\python.exe -m pytest tests\snapshots --snapshot-update` and review them before
committing.

## Engineering Rules

- Keep `pebra.core` deterministic and standard-library-only.
- Respect the established core, application, port, adapter, composition, CLI, dashboard, and E2E
  boundaries. Import contracts enforce these relationships.
- Exercise production behavior through real public surfaces where an end-to-end test is intended.
- Do not weaken fail-closed behavior, candidate hash binding, approval binding, or audit-chain
  integrity to make a test pass.
- Keep changes focused. Avoid unrelated refactors and generated metadata churn.
- Add a regression test for every bug fix and focused coverage for new behavior.

## Validation

Run the smallest relevant tests while developing, then the repository checks before requesting
review:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\lint-imports.exe
.\.venv\Scripts\python.exe -m pytest e2e\test_boundary_discipline.py -q
```

Some external-engine, browser, and paid-model lanes are gated. State clearly which gated checks were
not run and why; never report them as passing without evidence.

## Pull Requests

- Explain the user-visible or architectural problem and why the change is scoped correctly.
- Include verification commands and results.
- Call out behavior changes, trust-boundary changes, migration needs, and deferred work.
- Keep commits reviewable and do not include local databases, credentials, paid-run artifacts, or
  unrelated experiment output.
- Address review findings with tests when the finding describes a reproducible failure mode.

## Security Reports

Follow [SECURITY.md](SECURITY.md) for suspected vulnerabilities. **Do not open a public issue** for an
undisclosed security problem.
