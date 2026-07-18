from __future__ import annotations

import configparser
from pathlib import Path


def test_ports_must_not_import_tui() -> None:
    root = Path(__file__).resolve().parents[2]
    config = configparser.ConfigParser()
    config.read(root / ".importlinter", encoding="utf-8")

    contract = config["importlinter:contract:ports-no-tui"]
    assert contract["type"] == "forbidden"
    assert contract["source_modules"].split() == ["pebra.ports"]
    assert contract["forbidden_modules"].split() == ["pebra.tui"]
