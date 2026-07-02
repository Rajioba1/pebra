"""Control-arm placebo backing the ``advisory_check`` tool.

Deterministic and PEBRA-content-free: identical generic review/build/test advisory for ANY input, no
decision, no risk quantification, and — critically — none of the words a real graph-backed engine would
use. Its only job is to give the control arm the SAME pre-edit reasoning affordance as treatment, so
the measured difference is PEBRA's CONTENT, not "having an extra tool". Never imports pebra.

Invariant (asserted in tests): the output never contains the strings 'graph', 'fan-in', 'percentile',
'PEBRA', 'CodeGraph', and recommended_decision is always None, risk_level always 'unknown'.
"""

from __future__ import annotations

import json
import sys
from typing import Any

_ADVISORY_TEXT = (
    "This change may affect other parts of the codebase. Before committing, make sure you understand "
    "what references the code you are changing and run the full build and tests."
)


def advise(_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the fixed generic advisory, regardless of input."""
    return {
        "recommended_decision": None,
        "risk_level": "unknown",
        "advisory": _ADVISORY_TEXT,
        "detail": {},
    }


def _main(argv: list[str]) -> int:
    # CLI shape mirrors `pebra assess ... --json`: read a JSON payload from a file arg or stdin, emit JSON.
    payload: dict[str, Any] = {}
    args = [a for a in argv if a != "--json"]
    try:
        if args:
            with open(args[0], encoding="utf-8") as fh:
                payload = json.load(fh)
        elif not sys.stdin.isatty():
            data = sys.stdin.read().strip()
            if data:
                payload = json.loads(data)
    except (OSError, json.JSONDecodeError):
        payload = {}
    print(json.dumps(advise(payload)))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in the live runner
    raise SystemExit(_main(sys.argv[1:]))
