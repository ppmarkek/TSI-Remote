"""Compatibility launcher. The application code lives in ``src/konspekt``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from konspekt.app import main

if __name__ == "__main__":
    main()
