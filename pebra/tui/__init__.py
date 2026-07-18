"""PEBRA Observatory TUI (Textual) — the read-only terminal surface over persisted assessment history.

This package is a presentation surface: it reads through pebra.app.observatory_query_controller (M1) and
holds no decision/sanction/learning logic. Textual is imported lazily by the CLI entry (pebra.cli.tui) so
ordinary CLI parsing never pays the import cost.
"""
