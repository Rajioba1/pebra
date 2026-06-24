"""Enable ``python -m pebra``."""

from __future__ import annotations

import sys

from pebra.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
