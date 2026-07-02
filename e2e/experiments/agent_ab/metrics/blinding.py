"""Blinding leak scanner (pure). A subject must not know it is in an experiment or that the advisory
is PEBRA. If any leak term appears in the transcript, the run is flagged and excluded from the
efficacy analysis (reported separately). Case-insensitive, word/phrase aware.

The forbidden terms live in ``e2e.experiments.agent_ab.forbidden`` (shared with the corpus loader so
the two guards cannot drift). The transcript scanner uses ``EXPERIMENT_LEAK_TERMS`` — arm identity is
caught via phrases ("control arm"/"treatment group"), not the bare word "control" (a ubiquitous UI
domain word here); see forbidden.py for the rationale.
"""

from __future__ import annotations

from collections.abc import Iterable

from e2e.experiments.agent_ab.forbidden import EXPERIMENT_LEAK_TERMS, match_terms

# Back-compat alias for any caller referring to the scanner's term list.
LEAK_TERMS: tuple[str, ...] = EXPERIMENT_LEAK_TERMS


def scan_text(text: str) -> tuple[bool, tuple[str, ...]]:
    """Return (leaked, matched_terms) for a single string, case-insensitive."""
    matched = match_terms(text, EXPERIMENT_LEAK_TERMS)
    return (bool(matched), matched)


def scan_transcript(messages: Iterable[str]) -> tuple[bool, tuple[str, ...]]:
    """Scan a whole transcript (iterable of message texts). Returns (leaked, sorted matched terms)."""
    found: set[str] = set()
    for msg in messages:
        found.update(match_terms(msg or "", EXPERIMENT_LEAK_TERMS))
    return (bool(found), tuple(sorted(found)))
