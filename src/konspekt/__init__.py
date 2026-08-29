"""Konspekt — local tools for turning BBB lectures into study notes."""

from __future__ import annotations

import datetime as _datetime

# ``datetime.UTC`` was added in Python 3.11, while the project deliberately
# supports Python 3.10.  The package initializer runs before any submodule, so
# publishing the standard ``timezone.utc`` singleton on the module keeps the
# frozen allowlisted BBB importer byte-for-byte unchanged and makes all
# ``from datetime import UTC`` imports work consistently on Python 3.10.
if not hasattr(_datetime, "UTC"):
    setattr(_datetime, "UTC", _datetime.timezone.utc)

__all__ = ["__version__"]

__version__ = "0.1.0"
