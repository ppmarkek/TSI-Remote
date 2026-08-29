from __future__ import annotations

import unittest
from datetime import timedelta
from datetime import timezone as fixed_timezone

from konspekt import library_manager


class RecordingDateTests(unittest.TestCase):
    def test_formats_import_timestamp_in_the_requested_timezone(self) -> None:
        formatted = library_manager.format_imported_at(
            "2026-07-15T10:00:00+00:00",
            timezone=fixed_timezone(timedelta(hours=3)),
        )

        self.assertEqual(formatted, "Добавлено 15.07.2026, 13:00")

    def test_reports_unknown_date_instead_of_crashing(self) -> None:
        self.assertEqual(
            library_manager.format_imported_at("not-a-date"),
            "Дата добавления неизвестна",
        )


if __name__ == "__main__":
    unittest.main()
