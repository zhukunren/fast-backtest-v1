"""Allow running with: python -m web"""

from __future__ import annotations

from collections.abc import Callable


def _load_web_server_main() -> Callable[[], None]:
    from web.server import main

    return main

if __name__ == "__main__":
    _load_web_server_main()()
