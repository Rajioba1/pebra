"""Harness-keyed build/test backends for the agent A/B experiment.

``get_backend(harness_id)`` / ``backend_for_spec(spec)`` resolve the fixed toolchain profile; call sites
dispatch through the returned ``BuildBackend`` instead of hard-wiring dotnet. ``harness`` is injectable
for tests.
"""

from __future__ import annotations

from typing import Any

from e2e.experiments.agent_ab.backends.base import BuildBackend
from e2e.experiments.agent_ab.backends.csharp import CSharpBackend
from e2e.experiments.agent_ab.backends.javascript import JavaScriptBackend

_REGISTRY = {
    "dotnet": CSharpBackend,
    "node": JavaScriptBackend,
    # Compatibility aliases while older tests/callers still pass languages directly.
    "csharp": CSharpBackend,
    "javascript": JavaScriptBackend,
    "typescript": JavaScriptBackend,
}
_LANGUAGE_HARNESS = {"csharp": "dotnet", "javascript": "node", "typescript": "node"}


def get_backend(harness_id: str, **kwargs: Any) -> BuildBackend:
    try:
        cls = _REGISTRY[harness_id]
    except KeyError:
        raise ValueError(f"no build backend for harness {harness_id!r}") from None
    return cls(**kwargs)


def backend_for_spec(spec: Any, **kwargs: Any) -> BuildBackend:
    language = getattr(spec, "language", "csharp")
    expected = _LANGUAGE_HARNESS.get(language)
    harness_id = getattr(spec, "harness_id", None) or expected or language
    if expected is not None and harness_id != expected:
        raise ValueError(
            f"language {language!r} requires harness {expected!r}, got {harness_id!r}"
        )
    return get_backend(harness_id, **kwargs)


__all__ = ["BuildBackend", "CSharpBackend", "JavaScriptBackend", "get_backend", "backend_for_spec"]
