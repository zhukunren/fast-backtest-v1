"""Run-local stage timing."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import time
from typing import Iterator


@dataclass(slots=True)
class StageTimer:
    enabled: bool = False
    times: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started_at = time.perf_counter()
        try:
            yield
        finally:
            self.times[name] = self.times.get(name, 0.0) + (time.perf_counter() - started_at)

    def summary(self) -> list[str]:
        return [f"{name}={self.times[name]:.2f}s" for name in sorted(self.times, key=str.casefold)]

    def snapshot(self) -> dict[str, float]:
        return {
            name: float(self.times[name])
            for name in sorted(self.times, key=str.casefold)
        }

    def add_many(self, timings: dict[str, float] | None) -> None:
        if not (self.enabled and isinstance(timings, dict)):
            return
        for name, value in timings.items():
            key = str(name)
            self.times[key] = self.times.get(key, 0.0) + float(value)


__all__ = ["StageTimer"]
