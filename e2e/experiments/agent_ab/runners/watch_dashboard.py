"""Dev-only entry point for the Experiment Run Observatory. Read-only; NEVER imports pebra.

    python -m e2e.experiments.agent_ab.runners.watch_dashboard --run-id <id> --open
    python -m e2e.experiments.agent_ab.runners.watch_dashboard --once [--run-id <id>]   # dump JSON, no server

Starts a small stdlib http.server that renders the assay's run artifacts (scoreboard / matrix / coverage)
and offers a per-arm drilldown into the REAL `pebra dashboard`. It is NOT gated (it never runs an agent
and never writes into a run dir), so it is safe to launch any time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

from e2e.experiments.agent_ab.runners import launch_dashboard
from e2e.experiments.agent_ab.runners.observatory import aggregate, server

# Same assay output root the orchestrator/launch_dashboard use. Overridable in tests.
_AB_OUT = Path(__file__).resolve().parents[4] / "e2e" / "out" / "ab"
_DEFAULT_PORT = 8787  # deliberately distinct from PEBRA's own dashboard base to avoid confusion


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Dev-only run observatory for the agent A/B assay (read-only; wraps pebra dashboard).")
    p.add_argument("--run-id", default=None, help="Open directly on this run (default: the run index).")
    p.add_argument("--mode", default=None,
                   help="Assay mode (smoke|pilot|powered|assay|assay_js). Enables the planned/pending grid "
                        "when run_status.json is absent.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=_DEFAULT_PORT)
    p.add_argument("--open", action="store_true", help="Open the observatory in a browser.")
    p.add_argument("--once", action="store_true",
                   help="Print the run view (or run index) as JSON and exit — no server.")
    args = p.parse_args(argv)

    if args.run_id is not None and (not launch_dashboard._RUN_ID_RE.fullmatch(args.run_id)  # noqa: SLF001
                                    or args.run_id in (".", "..")):
        print("run-id must be a simple run directory name", file=sys.stderr)
        return 1

    if args.once:
        if args.run_id:
            try:
                view = aggregate.build_run_view(args.run_id, ab_out=_AB_OUT, mode=args.mode)
            except aggregate.RunNotFound:
                print(f"no run '{args.run_id}' under {_AB_OUT}", file=sys.stderr)
                return 1
            print(json.dumps(view, indent=2))
        else:
            print(json.dumps({"runs": aggregate.list_runs(ab_out=_AB_OUT)}, indent=2))
        return 0

    if args.run_id:
        open_hash = f"#/run/{quote(args.run_id, safe='')}"
        if args.mode:
            open_hash += "?" + urlencode({"mode": args.mode})
    else:
        open_hash = ""
    return server.serve(ab_out=_AB_OUT, host=args.host, port=args.port,
                        open_browser=args.open, open_hash=open_hash)


if __name__ == "__main__":  # pragma: no cover - manual dev entry point
    raise SystemExit(main())
