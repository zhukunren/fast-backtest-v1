from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager


def _reload_config_cache() -> None:
    from config import get_config

    get_config(reload=True)


@contextmanager
def temporary_env(
    overrides: Mapping[str, object | None],
    *,
    reload_config: bool = True,
) -> Iterator[None]:
    normalized = {
        str(key).strip(): (None if value is None else str(value))
        for key, value in overrides.items()
        if str(key).strip()
    }
    keys = tuple(sorted(normalized, key=str.casefold))
    old_env = {key: os.environ.get(key) for key in keys}

    for key in keys:
        value = normalized[key]
        if value is None or str(value).strip() == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)

    if reload_config:
        _reload_config_cache()
    try:
        yield
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if reload_config:
            _reload_config_cache()


__all__ = ["temporary_env"]
