"""
急速回测系统 - 主入口
"""

from __future__ import annotations

from collections.abc import Callable


def _load_run_application() -> Callable[[], int]:
    
    from app import run_application

    return run_application


def main() -> int:
    return _load_run_application()()


if __name__ == "__main__":
    raise SystemExit(main())


