"""Shared path and dependency contracts for the V1 benchmark inputs."""

from __future__ import annotations

import platform
from pathlib import Path

import numpy as np
import pandas as pd
import psutil


REQUIRED_INPUT_NAMES = (
    "industry_board",
    "panel",
    "panel_manifest",
    "trade_calendar",
    "universe",
)


def resolve_input_path(spec_path: Path, raw: object) -> Path:
    """Resolve a spec-relative input path without changing its spelling."""

    path = Path(str(raw))
    return path if path.is_absolute() else spec_path.parent / path


def dependency_versions() -> dict[str, str]:
    """Return dependency versions recorded in a frozen benchmark spec."""

    versions = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "psutil": psutil.__version__,
    }
    try:
        import pyarrow

        versions["pyarrow"] = pyarrow.__version__
    except ImportError:
        versions["pyarrow"] = "unavailable"
    try:
        import numba

        versions["numba"] = numba.__version__
    except ImportError:
        versions["numba"] = "unavailable"
    return versions


__all__ = ["REQUIRED_INPUT_NAMES", "dependency_versions", "resolve_input_path"]
