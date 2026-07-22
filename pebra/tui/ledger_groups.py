"""Pure, presentation-only grouping for contiguous Observatory ledger rows."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import groupby
from typing import Any, Mapping, Sequence

_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_PRIOR_SOURCES = {"cold_start", "shipped", "local_learned", "mixed"}


@dataclass(frozen=True)
class LedgerGroup:
    primary_assessment_id: str
    assessment_ids: tuple[str, ...]
    latest_row: Mapping[str, Any]


def prior_display_semantics(facet: object) -> tuple[str, int] | None:
    """Validate the facet fields that determine the ledger's visible prior label."""
    if not isinstance(facet, Mapping):
        return None
    source = facet.get("source")
    count = facet.get("applied_target_count")
    if (
        not isinstance(source, str)
        or source not in _PRIOR_SOURCES
        or isinstance(count, bool)
        or not isinstance(count, int)
    ):
        return None
    if count < 0 or (source in {"local_learned", "mixed"} and count <= 0):
        return None
    return str(source), count


def _prior_grouping_semantics(facet: object) -> tuple[Any, ...] | None:
    """Canonical persisted prior identity; malformed/unavailable facets never group."""
    display = prior_display_semantics(facet)
    if display is None or not isinstance(facet, Mapping):
        return None
    snapshot_ids = facet.get("snapshot_ids")
    calibration_tags = facet.get("calibration_tags")
    if not isinstance(snapshot_ids, list) or not isinstance(calibration_tags, list):
        return None
    if not all(isinstance(value, str) and value for value in (*snapshot_ids, *calibration_tags)):
        return None
    return (*display, tuple(snapshot_ids), tuple(calibration_tags))


def _grouping_key(row: Mapping[str, Any], index: int) -> tuple[Any, ...]:
    fingerprint = row.get("candidate_fingerprint")
    if not isinstance(fingerprint, str) or _FINGERPRINT.fullmatch(fingerprint) is None:
        return ("unique", index)
    scores = row.get("scores") or {}
    score_values = tuple(scores.get(name) for name in ("rau", "expected_loss", "benefit"))
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
        for value in score_values
    ):
        return ("unique", index)
    prior = _prior_grouping_semantics(row.get("prior_facet"))
    if prior is None:
        return ("unique", index)
    return (
        "candidate",
        fingerprint,
        row.get("assessed_commit"),
        row.get("decision"),
        row.get("terminal_status"),
        row.get("task"),
        row.get("action_id"),
        tuple(row.get("target_files") or ()),
        prior,
        *score_values,
    )


def group_contiguous_assessments(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[LedgerGroup, ...]:
    """Collapse adjacent rows with one validated, identical semantic grouping key."""
    keyed_rows = ((_grouping_key(row, index), row) for index, row in enumerate(rows))
    groups: list[LedgerGroup] = []
    for _, members in groupby(keyed_rows, key=lambda item: item[0]):
        grouped_rows = tuple(row for _, row in members)
        latest_row = grouped_rows[0]
        assessment_ids = tuple(str(row["assessment_id"]) for row in grouped_rows)
        groups.append(
            LedgerGroup(
                primary_assessment_id=assessment_ids[0],
                assessment_ids=assessment_ids,
                latest_row=latest_row,
            )
        )
    return tuple(groups)
