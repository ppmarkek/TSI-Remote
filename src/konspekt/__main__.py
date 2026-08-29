"""Run Konspekt with ``python -m konspekt``."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--chatgpt-auth-window":
        from konspekt.chatgpt_auth_window import main as auth_window_main

        return auth_window_main(arguments[1:])

    if arguments and arguments[0] == "--diagnostics-json":
        import json

        from konspekt.diagnostics import collect_system_diagnostics

        diag = collect_system_diagnostics()
        sys.stdout.write(json.dumps(diag, ensure_ascii=False, indent=2) + "\n")
        return 0

    from konspekt.app import main as app_main

    app_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
