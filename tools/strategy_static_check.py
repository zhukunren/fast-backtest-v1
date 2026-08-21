"""CLI entry point for strategy static analysis."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.static_check import (
    build_arg_parser,
    check_strategy_files,
    check_strategy_source,
    main,
)

__all__ = [
    "build_arg_parser",
    "check_strategy_files",
    "check_strategy_source",
    "main",
]
if __name__ == "__main__":
    raise SystemExit(main())
