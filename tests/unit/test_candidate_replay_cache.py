from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from pebra.adapters.candidate_replay_cache import (
    CANDIDATE_REPLAY_ALGORITHM,
    CandidateReplayCache,
    CandidateReplayError,
    validate_candidate_replay_metadata,
)


def _bundle() -> dict:
    return {
        "request": {
            "task": "preserve compatibility",
            "candidate_actions": [{
                "id": "edit-api",
                "label": "rename with compatibility bridge",
                "action_type": "edit",
                "proposed_patch": "--- a/a.ts\n+++ b/a.ts\n@@ -1 +1 @@\n-old\n+new\n",
                "expected_files": ["a.ts"],
            }],
            "evidence": {"p_success": 0.8, "immediate_benefit": 0.4},
            "thresholds": {},
            "schema_version": "0.1",
        },
        "trusted_candidate_verification": None,
        "trusted_task_obligations": {"required_files": ["a.ts"]},
    }


def test_cache_round_trip_is_content_addressed_and_private(tmp_path: Path) -> None:
    cache = CandidateReplayCache(tmp_path)

    first = cache.store(_bundle())
    second = cache.store(_bundle())

    assert first == second
    assert first["status"] == "available"
    assert first["algorithm"] == CANDIDATE_REPLAY_ALGORITHM
    assert cache.load(first) == _bundle()
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    if os.name != "nt":
        assert files[0].stat().st_mode & 0o777 == 0o600


def test_cache_rejects_tampered_content(tmp_path: Path) -> None:
    cache = CandidateReplayCache(tmp_path)
    metadata = cache.store(_bundle())
    path = tmp_path / f"{metadata['digest']}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["request"]["evidence"]["p_success"] = 1.0
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(CandidateReplayError, match="digest"):
        cache.load(metadata)


@pytest.mark.parametrize("digest", ["../outside", "not-hex", "a" * 63, "a" * 65])
def test_cache_rejects_unsafe_digest(tmp_path: Path, digest: str) -> None:
    cache = CandidateReplayCache(tmp_path)
    with pytest.raises(CandidateReplayError, match="metadata"):
        cache.load({
            "status": "available",
            "algorithm": "sha256-candidate-replay-v1",
            "digest": digest,
        })


@pytest.mark.parametrize(
    "metadata",
    (
        {"status": "available", "digest": "a" * 64},
        {
            "status": "available",
            "algorithm": "sha256-candidate-replay-v0",
            "digest": "a" * 64,
        },
        {
            "status": "available",
            "algorithm": "sha256-candidate-replay-v1",
            "digest": "A" * 64,
        },
    ),
    ids=("missing-algorithm", "wrong-algorithm", "uppercase-digest"),
)
def test_public_metadata_validator_rejects_structurally_invalid_available_metadata(
    metadata,
) -> None:
    with pytest.raises(CandidateReplayError, match="metadata"):
        validate_candidate_replay_metadata(metadata)


def test_public_metadata_validator_returns_validated_digest() -> None:
    metadata = {
        "status": "available",
        "algorithm": CANDIDATE_REPLAY_ALGORITHM,
        "digest": "a" * 64,
    }

    assert validate_candidate_replay_metadata(metadata) == "a" * 64


def test_cache_rejects_expired_entry(tmp_path: Path) -> None:
    cache = CandidateReplayCache(tmp_path, max_age_seconds=10)
    metadata = cache.store(_bundle())
    path = tmp_path / f"{metadata['digest']}.json"
    old = time.time() - 11
    os.utime(path, (old, old))

    with pytest.raises(CandidateReplayError, match="expired"):
        cache.load(metadata)


def test_cache_enforces_explicit_size_limit_without_partial_file(tmp_path: Path) -> None:
    cache = CandidateReplayCache(tmp_path, max_bytes=64)

    with pytest.raises(CandidateReplayError, match="size limit"):
        cache.store(_bundle())

    assert list(tmp_path.iterdir()) == []


def test_cache_delete_is_idempotent(tmp_path: Path) -> None:
    cache = CandidateReplayCache(tmp_path)
    metadata = cache.store(_bundle())

    cache.delete(metadata)
    cache.delete(metadata)

    with pytest.raises(CandidateReplayError, match="missing"):
        cache.load(metadata)


def test_cache_consume_is_atomic_single_use_and_fresh_store_can_recreate(tmp_path: Path) -> None:
    cache = CandidateReplayCache(tmp_path)
    metadata = cache.store(_bundle())

    cache.consume(metadata)

    with pytest.raises(CandidateReplayError, match="missing"):
        cache.load(metadata)
    with pytest.raises(CandidateReplayError, match="consumed"):
        cache.consume(metadata)

    assert cache.store(_bundle()) == metadata
    assert cache.load(metadata) == _bundle()
    cache.consume(metadata)
    with pytest.raises(CandidateReplayError, match="consumed"):
        cache.consume(metadata)
