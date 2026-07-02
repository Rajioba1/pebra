"""Single source of truth for the two blinding leak-guards, so they cannot drift.

Two consumers, one module:
  - ``metrics/blinding.py`` scans the AGENT TRANSCRIPT for experiment / engine-identity leaks. It uses
    only the high-signal ``EXPERIMENT_LEAK_TERMS`` — deliberately NOT the bare words "control"/"treatment",
    because this is a UI (Avalonia) codebase where "control" is a ubiquitous domain word (UserControl,
    "the control", TemplateBlueprint.Controls…). Scanning transcripts for bare "control" would
    false-exclude nearly every run. Arm identity is instead caught via PHRASES ("control arm",
    "treatment group", …). This is a deliberate divergence from the literal review note (which suggested
    bare "control"/"treatment"); documented here so it is a decision, not an oversight.
  - ``corpus/loader.py`` validates AUTHOR-WRITTEN task text, which is short and fully under our control,
    so it uses the stricter ``CORPUS_FORBIDDEN_TERMS`` (adds the bare arm words + trap descriptors).

Matching: a single alphabetic word is matched on word boundaries (so "trial" != "industrial" and
"control" != "Controls"); any term containing a space or punctuation ("control arm", "a/b", "fan-in")
is a plain case-insensitive substring.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Shared by BOTH guards: framing + engine identity + arm phrases. Safe to scan agent transcripts.
EXPERIMENT_LEAK_TERMS: tuple[str, ...] = (
    "experiment", "a/b", "pebra", "codegraph", "blinded", "blinding", "trial",
    "evaluation", "ablation", "oracle",
    "control arm", "treatment arm", "control group", "treatment group",
)

# Extra terms only the corpus author-text guard enforces (safe to forbid in text WE write).
_CORPUS_EXTRA_TERMS: tuple[str, ...] = (
    "control", "treatment", "graph", "blast", "trap", "risky", "safe", "fan-in",
)

CORPUS_FORBIDDEN_TERMS: tuple[str, ...] = EXPERIMENT_LEAK_TERMS + _CORPUS_EXTRA_TERMS


def _matches(low_text: str, term: str) -> bool:
    if term.isalpha():  # single word -> word boundary (no "industrial"/"Controls" false hits)
        return re.search(rf"\b{re.escape(term)}\b", low_text) is not None
    return term in low_text  # phrase / punctuated -> substring


def match_terms(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    """Case-insensitive; return the sorted matched terms."""
    low = (text or "").lower()
    return tuple(sorted(t for t in terms if _matches(low, t)))
