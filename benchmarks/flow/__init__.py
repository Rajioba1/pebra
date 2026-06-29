"""Flow tier — replay + deterministic scorecard over a corpus.

The scorecard delegates every metric to ``pebra.core`` (``learning_eval`` / ``prediction_error``); it
adds only normalization + JSON shaping. The determinism target is the normalized ``scorecard.json``
artifact (NOT the SQLite DB, whose hash-chain carries wall-clock timestamps).
"""
