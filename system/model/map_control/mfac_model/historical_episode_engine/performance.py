from __future__ import annotations

"""Low-overhead stage timing used by V1.8B performance diagnostics."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator


@dataclass
class PerformanceRecorder:
    enabled: bool = True
    stages_seconds: dict[str, float] = field(default_factory=dict)
    counters: dict[str, Any] = field(default_factory=dict)
    _started_at: float = field(default_factory=perf_counter, init=False)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        start = perf_counter()
        try:
            yield
        finally:
            self.stages_seconds[name] = self.stages_seconds.get(name, 0.0) + (
                perf_counter() - start
            )

    def add_counter(self, name: str, value: Any) -> None:
        if self.enabled:
            self.counters[name] = value

    def report(self) -> dict[str, Any]:
        total = perf_counter() - self._started_at
        return {
            "total_elapsed_seconds": float(total),
            "stages_seconds": {
                key: float(value) for key, value in self.stages_seconds.items()
            },
            "counters": dict(self.counters),
        }
