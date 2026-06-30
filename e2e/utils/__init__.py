"""E2E harness utilities — subprocess/IO wrappers around the PEBRA CLI/MCP/dashboard boundaries.

These are the ONLY place an e2e test touches PEBRA, and they touch it as an external process (argv /
JSON-RPC / HTTP), never by importing pebra. Pure helpers (JSON parsing, tolerance comparison, report
rendering) are unit-tested in ``tests/``.
"""
