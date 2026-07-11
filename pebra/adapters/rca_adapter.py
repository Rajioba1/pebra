"""rca_adapter — multi-language complexity + maintainability BENEFIT evidence via rust-code-analysis.

Replaces the old Python-only in-process benefit adapter. RCA is an EXTERNAL CLI
(``rust-code-analysis-cli``, located via ``find_rca()``) that computes cyclomatic complexity + a
maintainability index for py/js/jsx/ts/tsx/java/rs/c/cc/cpp/h/hpp. It produces ``BenefitDeltaEvidence``
with the SAME benefit keys (``complexity_delta``, ``maintainability_index_delta``), so
``benefit_model`` is unchanged. BENEFIT-only: it never feeds a risk/loss/gate term.

Honest & fail-safe — any of {binary missing, unsupported language, parse failure, empty output} yields
NO maintainability credit (``source_type="projected", deltas={}``), never a crash, never lowering risk.

RCA parses ONE FILE per invocation (``-p <file> -O json --pr`` → JSON on stdout). Each source string is
written to its OWN throwaway temp file (with the real extension so RCA dispatches the language), so
multi-file measurement never collides and no shared scratch tree is needed — the patch is applied only
to obtain the after-*strings* (via the shared, vetted ``patch_materializer``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from pebra.adapters._paths import safe_relative_files
from pebra.adapters.patch_materializer import materialize_patch
from pebra.core.benefit_aggregation import aggregate_file_deltas
from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.models import BenefitDeltaEvidence
from pebra.core.rca_engine_paths import RCA_ACCEPTED_VERSION, RCA_SOURCE_REVISION, find_rca

# Extensions the BUILT rca binary actually parses (empirically verified against the git-HEAD build; the
# declared grammar list is broader — e.g. Kotlin/.kt and Go/.go are declared/plausible but produce NO
# output — so this is a MEASURED allowlist, not the advertised one). Files outside it get no credit.
_RCA_SUPPORTED_EXTS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".rs", ".c", ".cc", ".cpp", ".h", ".hpp",
})

# A runner maps a source-file Path -> parsed FuncSpace JSON (or None on any failure). Injectable so unit
# tests feed canned JSON without the binary; the default shells out to rust-code-analysis-cli.
RcaRunner = Callable[[Path], "dict[str, Any] | None"]


@lru_cache(maxsize=8)
def _rca_version_for_binary(exe: str, mtime_ns: int, size: int) -> str | None:
    """Version for one concrete binary identity; mtime/size invalidate the cache on replacement."""
    del mtime_ns, size
    try:
        proc = subprocess.run(
            resolve_engine_argv(exe, ["--version"]),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    prefix = "rust-code-analysis-cli "
    text = proc.stdout.strip()
    return text[len(prefix):].strip() if text.startswith(prefix) else None


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _cargo_source_revision(exe: str) -> str | None:
    """Read Cargo's install provenance for a launcher under <cargo-root>/bin."""
    binary = Path(exe).resolve()
    metadata = binary.parent.parent / ".crates2.json"
    try:
        installs = json.loads(metadata.read_text(encoding="utf-8")).get("installs", {})
    except (OSError, ValueError, AttributeError):
        return None
    for descriptor, details in installs.items():
        if not isinstance(descriptor, str) or not descriptor.startswith("rust-code-analysis-cli "):
            continue
        bins = details.get("bins", []) if isinstance(details, dict) else []
        if binary.name.lower() not in {str(name).lower() for name in bins}:
            continue
        match = re.search(r"#([0-9a-f]{40})\)$", descriptor)
        return match.group(1) if match else None
    return None


def _validated_rca(exe: str) -> str | None:
    """Require the validated version plus pinned Cargo provenance or an explicit exact hash."""
    try:
        binary = Path(exe)
        stat = binary.stat()
    except OSError:
        return None
    version = _rca_version_for_binary(exe, stat.st_mtime_ns, stat.st_size)
    if version != RCA_ACCEPTED_VERSION:
        return None
    expected_hash = os.environ.get("PEBRA_RCA_SHA256", "").strip().lower()
    if expected_hash:
        try:
            return exe if _sha256_file(binary) == expected_hash else None
        except OSError:
            return None
    return exe if _cargo_source_revision(exe) == RCA_SOURCE_REVISION else None


def _run_rca_cli(path: Path) -> dict[str, Any] | None:
    exe = find_rca()
    if exe is None or _validated_rca(exe) is None:
        return None
    try:
        proc = subprocess.run(
            resolve_engine_argv(exe, ["-m", "-p", str(path), "-O", "json", "--pr"]),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # RCA exits 0 with EMPTY stdout for an unsupported language, so gate on parseable, non-empty output.
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_metrics(parsed: dict[str, Any] | None) -> tuple[float, float] | None:
    """(cyclomatic_sum, maintainability_index) from a top-level FuncSpace, or None if unusable.
    Complexity ← metrics.cyclomatic.sum; MI ← metrics.mi.mi_visual_studio (0-100, the comparable
    variant)."""
    if not isinstance(parsed, dict):
        return None
    try:
        metrics = parsed["metrics"]
        cc = float(metrics["cyclomatic"]["sum"])
        mi = float(metrics["mi"]["mi_visual_studio"])
    except (KeyError, TypeError, ValueError):
        return None
    return cc, mi


def _supported(rel_path: str) -> bool:
    return Path(rel_path).suffix.lower() in _RCA_SUPPORTED_EXTS


class RustCodeAnalysisAdapter:
    def __init__(self, runner: RcaRunner | None = None) -> None:
        self._runner = runner or _run_rca_cli

    def _measure_source(self, source: str, suffix: str) -> tuple[float, float] | None:
        """Write ``source`` to a throwaway single-file temp dir (``suffix`` = the real extension so RCA
        dispatches the language) and return (cyclomatic_sum, MI), or None on any failure. One file per
        call → no cross-file basename collision."""
        try:
            with tempfile.TemporaryDirectory(prefix="pebra-rca-") as tmp:
                fp = Path(tmp) / f"src{suffix}"
                fp.write_text(source, encoding="utf-8")
                return _extract_metrics(self._runner(fp))
        except OSError:
            return None

    def measure_delta(
        self, rel_path: str, before_src: str | None, after_src: str | None
    ) -> tuple[float, float] | None:
        """Post-edit (verify) path: (complexity_delta, maintainability_index_delta) for ONE file, or None
        if the language is unsupported or either side is unmeasurable. Plain text — verify already holds
        both blobs from git, so no patch/materialization is needed."""
        if not _supported(rel_path) or before_src is None or after_src is None:
            return None
        suffix = Path(rel_path).suffix.lower()
        before = self._measure_source(before_src, suffix)
        after = self._measure_source(after_src, suffix)
        if before is None or after is None:
            return None
        return after[0] - before[0], after[1] - before[1]

    def measure_file_delta(
        self, rel_path: str, before_src: str | None, after_src: str | None
    ) -> tuple[float, float, float] | None:
        """Verify-path delta plus baseline-complexity exposure weight for multi-file aggregation."""
        if not _supported(rel_path) or before_src is None:
            return None
        if after_src is None:
            after_src = before_src
        suffix = Path(rel_path).suffix.lower()
        before = self._measure_source(before_src, suffix)
        after = self._measure_source(after_src, suffix)
        if before is None or after is None:
            return None
        return after[0] - before[0], after[1] - before[1], max(1.0, before[0])

    def gather_benefit_evidence(
        self, repo_root: str, files: list[str], proposed_patch: str | None = None, *,
        future_change_exposure: float = 0.0,
    ) -> BenefitDeltaEvidence:
        """Pre-edit benefit: apply the patch to a throwaway copy (shared materializer) to derive after-
        strings, measure RCA complexity+MI before/after per supported file, return SUMMED complexity +
        AVERAGED MI deltas. Fail-safe to projected/{} on any gap; never mutates the repo."""
        # Validate + language-gate caller-supplied paths BEFORE any read (agent-supplied → untrusted).
        supported = [f for f in safe_relative_files(repo_root, files) if _supported(f)]
        if not supported:
            return BenefitDeltaEvidence(source_type="projected", deltas={})
        root = Path(repo_root)
        before: dict[str, str | None] = {}
        for rel in supported:
            try:
                before[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        if not before:
            return BenefitDeltaEvidence(source_type="projected", deltas={})
        scope = ",".join(sorted(before))
        if not proposed_patch:
            return BenefitDeltaEvidence(scope=scope, source_type="projected", deltas={})
        after = materialize_patch(before, proposed_patch)
        if after is None:
            return BenefitDeltaEvidence(scope=scope, source_type="projected", deltas={})
        # Benefit-measurement policy: a deleted/unreadable after-file counts as unchanged.
        after = {rel: (after[rel] if after[rel] is not None else before[rel]) for rel in before}
        if all(after[rel] == before[rel] for rel in before):
            return BenefitDeltaEvidence(scope=scope, source_type="projected", deltas={})
        measured: dict[str, tuple[float, float, float]] = {}
        file_deltas: dict[str, dict[str, float]] = {}
        for rel, before_src in before.items():
            if before_src is None:
                continue
            suffix = Path(rel).suffix.lower()
            b = self._measure_source(before_src, suffix)
            a = self._measure_source(after[rel] or "", suffix)
            if b is None or a is None:
                continue
            file_cc_delta, file_mi_delta = a[0] - b[0], a[1] - b[1]
            # Baseline cyclomatic complexity is a deterministic exposure proxy available from the
            # same measured RCA result. A floor of one keeps branch-free files represented.
            weight = max(1.0, b[0])
            measured[rel] = (file_cc_delta, file_mi_delta, weight)
            file_deltas[rel] = {
                "complexity_delta": file_cc_delta,
                "maintainability_index_delta": file_mi_delta,
                "exposure_weight": weight,
            }
        if not file_deltas:
            return BenefitDeltaEvidence(scope=scope, source_type="projected", deltas={})
        deltas = aggregate_file_deltas(measured)
        return BenefitDeltaEvidence(
            scope=scope, source_type="measured", deltas=deltas,
            future_change_exposure=future_change_exposure,
            auto_exposure_allowed=True,
            file_deltas=file_deltas,
        )
