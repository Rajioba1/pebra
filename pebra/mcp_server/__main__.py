"""`python -m pebra.mcp_server` / `pebra-mcp` console entry — start the PEBRA stdio MCP server.

Kept trivial so the dep-light import surface is just ``serve`` (which lazy-imports the mcp SDK).
"""

from __future__ import annotations

from pebra.mcp_server.server import serve


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
