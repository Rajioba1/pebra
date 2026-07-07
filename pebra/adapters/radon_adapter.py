"""radon_adapter (Slice 4b) — maintainability/complexity benefit evidence via radon.

Adapter layer: radon is allowed here (the import-linter forbids it in core/). Produces a
``BenefitDeltaEvidence`` consumed by the pure ``benefit_model``. Honest about the pre-edit
before-image problem:

  proposed_patch present AND applies cleanly to a TEMP copy
      -> run radon before/after -> real complexity/maintainability deltas -> source_type="measured"
  no patch / patch does not apply / radon cannot analyze the sources
      -> source_type="projected", deltas={}  (no maintainability credit) — an evidence GAP, never a
         model-invented delta.

Guardrails (ratified): never mutate the real repo (temp dir only); a patch-apply failure is not an
error, just projected; the genuine post-edit *measured* delta is computed later by the verify path.
Radon affects BENEFIT only — it never lowers risk.
"""

from __future__ import annotations

from pathlib import Path

from radon.complexity import cc_visit
from radon.metrics import mi_visit

from pebra.adapters._paths import safe_relative_files
from pebra.adapters.patch_materializer import materialize_patch
from pebra.core.models import BenefitDeltaEvidence


def _projected(scope: str = "") -> BenefitDeltaEvidence:
    return BenefitDeltaEvidence(scope=scope, source_type="projected", deltas={})


def _file_metrics(source: str) -> tuple[float, float] | None:
    """(total cyclomatic complexity, maintainability index) for one source, or None when radon can't
    analyze it — a per-file gap, not a crash. radon is third-party with an undocumented exception
    contract (SyntaxError, tokenize.TokenError on errors='replace'-mangled source, etc.), so any
    failure degrades to a gap rather than propagating."""
    try:
        total_cc = float(sum(block.complexity for block in cc_visit(source)))
        mi = float(mi_visit(source, multi=True))
    except Exception:
        return None
    return total_cc, mi


def _aggregate(sources: dict[str, str]) -> tuple[float, float] | None:
    """Summed complexity + averaged MI over the analyzable files; None if NONE could be analyzed."""
    total_cc = 0.0
    mis: list[float] = []
    for src in sources.values():
        metrics = _file_metrics(src)
        if metrics is None:
            continue
        total_cc += metrics[0]
        mis.append(metrics[1])
    if not mis:
        return None
    return total_cc, sum(mis) / len(mis)


def _apply_patch(before: dict[str, str], patch: str) -> dict[str, str] | None:
    """Apply ``patch`` to TEMP copies of ``before`` (never the real repo) via the shared materializer.
    Returns after-sources, or None if the patch does not apply cleanly, escapes the temp dir, or changes
    nothing. The radon-specific policy (a deleted/unreadable file counts as unchanged; "changed nothing"
    -> None) lives here, not in the shared recipe."""
    materialized = materialize_patch(before, patch)
    if materialized is None:
        return None
    after = {
        rel: (materialized[rel] if materialized[rel] is not None else before[rel]) for rel in before
    }
    if all(after[rel] == before[rel] for rel in before):
        return None  # applied but changed none of the analyzed files -> not a real measurement
    return after


class RadonAdapter:
    def gather_benefit_evidence(
        self,
        repo_root: str,
        files: list[str],
        proposed_patch: str | None = None,
        *,
        future_change_exposure: float = 0.0,
    ) -> BenefitDeltaEvidence:
        # Validate caller-supplied paths BEFORE any read: absolute / ``..`` / escaping paths are
        # dropped so radon never reads outside the repo (the same escape class as the patch payload).
        py = sorted(f for f in safe_relative_files(repo_root, files) if f.endswith(".py"))
        if not py:
            return _projected()
        root = Path(repo_root)
        before: dict[str, str] = {}
        for rel in py:
            try:
                before[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        if not before:
            return _projected()
        scope = ",".join(sorted(before))
        if not proposed_patch:
            # pre-edit baseline only — no after-image, so no honest delta (verify measures it later).
            return _projected(scope)
        after = _apply_patch(before, proposed_patch)
        if after is None:
            return _projected(scope)  # patch didn't apply -> evidence gap, not an error
        before_metrics = _aggregate(before)
        after_metrics = _aggregate(after)
        if before_metrics is None or after_metrics is None:
            return _projected(scope)
        deltas = {
            "complexity_delta": after_metrics[0] - before_metrics[0],
            "maintainability_index_delta": after_metrics[1] - before_metrics[1],
        }
        return BenefitDeltaEvidence(
            scope=scope,
            source_type="measured",
            deltas=deltas,
            future_change_exposure=future_change_exposure,
        )
