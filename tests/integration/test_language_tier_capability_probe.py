"""MEASURED per-language tier probe against the REAL codegraph binary (gated; skipped if not installed).

This is the self-verifying source of truth for "which languages reach which PEBRA tier". It indexes a
tiny fixture per language with the actual codegraph engine and asserts the tier PEBRA's OWN
``classify_tier`` measures (via ``composition.probe_language_capabilities``) — so nobody has to trust a
source-read of the extractors or a one-off manual probe. Tier is DB-measured, never hardcoded.

If a future codegraph release changes extraction, this test goes RED on purpose, flagging that the tier
map (+ the ``language-tier-map-visibility-gated`` memory) must be updated rather than silently drift.

Validated on codegraph 1.1.1 (2026-07-07):
  FULL  = TypeScript, Java, Rust, Dart, Scala, Pascal  (signature + visibility both measured >= 0.5),
          plus Go, JavaScript, JSX (signature measured; visibility DERIVED from is_exported by PEBRA's
          EXPORT_AS_VISIBILITY_LANGUAGES fill, since their extractor emits no getVisibility)
  PARTIAL = Kotlin, Swift  (visibility measured, but signature is NEVER populated: their getSignature
            calls childForFieldName with node-type strings the grammar doesn't expose as fields, so it
            returns undefined for every declaration — a codegraph bug, confirmed by measurement AND the
            source. A codegraph fix would flip them to FULL and turn this test red, which is the point.)
"""

from __future__ import annotations

import subprocess

import pytest

from pebra import composition
from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.engine_paths import find_engine

# language -> (filename, source). Each fixture declares >=1 callable with params + return + an explicit
# visibility marker, so a WORKING extractor clears the 0.5 signature/visibility coverage floors.
_FIXTURES: dict[str, tuple[str, str]] = {
    "typescript": ("calc.ts",
        "export class Calc {\n  public add(a: number, b: number): number { return a + b }\n}\n"),
    "java": ("Calc.java",
        "public class Calc {\n  public int add(int a, int b) { return a + b; }\n"
        "  private String secret(String x) { return x; }\n}\n"),
    "rust": ("calc.rs",
        "pub struct Calc;\nimpl Calc {\n  pub fn add(&self, a: i32, b: i32) -> i32 { a + b }\n"
        "  fn secret(&self, x: String) -> String { x }\n}\n"),
    "dart": ("calc.dart",
        "class Calc {\n  int add(int a, int b) { return a + b; }\n"
        "  String secret(String x) { return x; }\n}\n"),
    "scala": ("Calc.scala",
        "class Calc {\n  def add(a: Int, b: Int): Int = a + b\n"
        "  private def secret(x: String): String = x\n}\n"),
    "pascal": ("calc.pas",
        "unit Calc;\ninterface\ntype\n  TCalc = class\n  public\n    function Add(a, b: Integer): Integer;\n"
        "  end;\nimplementation\nfunction TCalc.Add(a, b: Integer): Integer;\nbegin\n  Result := a + b;\n"
        "end;\nend.\n"),
    "kotlin": ("Calc.kt",
        "class Calc {\n  fun add(a: Int, b: Int): Int { return a + b }\n}\n"),
    "swift": ("Calc.swift",
        "class Calc {\n  public func add(a: Int, b: Int) -> Int { return a + b }\n"
        "  private func secret(x: String) -> String { return x }\n}\n"),
    # Go/JS/JSX have signatures + is_exported but NO getVisibility -> raw graph is risk_only; PEBRA's
    # is_exported->visibility fill (EXPORT_AS_VISIBILITY_LANGUAGES) lifts them to full.
    "go": ("main.go",
        "package main\nfunc Add(a int, b int) int { return a + b }\nfunc secret(x int) int { return x }\n"),
    "javascript": ("calc.js",
        "export function add(a, b) { return a + b }\nfunction secret(x) { return x }\n"),
    "jsx": ("App.jsx",
        "export function App() { return null }\nfunction helper(x) { return x }\n"),
}

_EXPECT_FULL = frozenset(
    {"typescript", "java", "rust", "dart", "scala", "pascal", "go", "javascript", "jsx"})
_EXPECT_PARTIAL = frozenset({"kotlin", "swift"})


@pytest.fixture(scope="module")
def measured_tiers(tmp_path_factory) -> dict[str, str]:
    """Index one repo containing all fixtures with the real binary, once, and return language -> tier."""
    if find_engine() is None:
        pytest.skip("codegraph binary not installed")
    repo = tmp_path_factory.mktemp("lang-tier-probe")
    for fname, src in _FIXTURES.values():
        (repo / fname).write_text(src, encoding="utf-8")
    proc = subprocess.run(
        resolve_engine_argv(find_engine(), ["init", str(repo)]),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    assert proc.returncode == 0, f"codegraph init failed: {proc.stderr}"
    return {r["language"]: r["tier"] for r in composition.probe_language_capabilities(str(repo))}


def test_full_tier_languages_measure_full(measured_tiers):
    got = {lang: measured_tiers.get(lang, "MISSING") for lang in sorted(_EXPECT_FULL)}
    assert got == {lang: "full" for lang in sorted(_EXPECT_FULL)}, (
        f"a full-tier language regressed on codegraph {find_engine()}: {got}"
    )


def test_kotlin_and_swift_measure_partial_due_to_dead_getsignature(measured_tiers):
    # Not full: their getSignature is dead (signature never populated), so signature coverage is 0.
    got = {lang: measured_tiers.get(lang, "MISSING") for lang in sorted(_EXPECT_PARTIAL)}
    assert got == {lang: "partial" for lang in sorted(_EXPECT_PARTIAL)}, (
        f"Kotlin/Swift no longer measure partial on codegraph {find_engine()}: {got}. "
        "If codegraph fixed their getSignature, promote them to FULL and update the tier-map memory."
    )
