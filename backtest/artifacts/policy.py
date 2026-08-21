"""Explicit artifact policy for one backtest run."""
from __future__ import annotations

from enum import Enum


class ArtifactPolicy(str, Enum):
    """Choose output publication without changing backtest computation."""

    NONE = "none"
    FULL = "full"

    @property
    def writes_outputs(self) -> bool:
        return self is ArtifactPolicy.FULL

    @classmethod
    def coerce(cls, value: ArtifactPolicy | str) -> ArtifactPolicy:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().casefold())
        except ValueError as exc:
            expected = ", ".join(item.value for item in cls)
            raise ValueError(f"unsupported artifact policy {value!r}; expected {expected}") from exc


__all__ = ["ArtifactPolicy"]
