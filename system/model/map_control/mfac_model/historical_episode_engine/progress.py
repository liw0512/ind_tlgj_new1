from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Callable


ProgressCallback = Callable[[float, str], None]


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass
class ProgressOptions:
    enabled: bool = True
    bar_width: int = 32
    min_interval_seconds: float = 0.20
    show_elapsed: bool = True


class TrainingProgress:
    """标准库实现的单行训练进度条。

    设计目标：
    - 不增加 tqdm 等第三方依赖；
    - 初次和增量训练使用同一套显示；
    - 支持核心函数通过 0~1 回调汇报内部进度；
    - 非交互终端中仍能正常输出。
    """

    def __init__(self, options: ProgressOptions | None = None):
        self.options = options or ProgressOptions()
        self.started_at = time.monotonic()
        self.last_print_at = 0.0
        self.last_percent = -1.0
        self.last_message = ""
        self.finished = False

    @classmethod
    def from_training_config(
        cls,
        training: dict[str, Any],
        enabled_override: bool | None = None,
    ) -> "TrainingProgress":
        config = dict(training.get("progress") or {})
        enabled = bool(config.get("enabled", True))
        if enabled_override is not None:
            enabled = bool(enabled_override)
        options = ProgressOptions(
            enabled=enabled,
            bar_width=max(10, int(config.get("bar_width", 32))),
            min_interval_seconds=max(
                0.0, float(config.get("min_interval_seconds", 0.20))
            ),
            show_elapsed=bool(config.get("show_elapsed", True)),
        )
        return cls(options)

    def update(
        self,
        percent: float,
        message: str,
        *,
        force: bool = False,
    ) -> None:
        if not self.options.enabled or self.finished:
            return

        value = min(100.0, max(0.0, float(percent)))
        if self.last_percent >= 0:
            value = max(value, self.last_percent)
        now = time.monotonic()
        message = str(message)
        enough_time = now - self.last_print_at >= self.options.min_interval_seconds
        enough_progress = value - self.last_percent >= 0.20
        if not force and not enough_time and not enough_progress:
            return

        width = self.options.bar_width
        filled = int(round(width * value / 100.0))
        filled = min(width, max(0, filled))
        bar = "█" * filled + "-" * (width - filled)
        elapsed = ""
        if self.options.show_elapsed:
            elapsed = f" | 已用 {format_duration(now - self.started_at)}"

        line = f"\r训练进度 [{bar}] {value:6.2f}% | {message}{elapsed}"
        # 清除上一行可能残留的较长文本。
        sys.stdout.write(line + " " * 8)
        sys.stdout.flush()
        self.last_print_at = now
        self.last_percent = value
        self.last_message = message

        if value >= 100.0:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.finished = True

    def fail(self, message: str) -> None:
        if not self.options.enabled or self.finished:
            return
        sys.stdout.write("\n")
        sys.stdout.write(f"训练中止 | {message}\n")
        sys.stdout.flush()
        self.finished = True

    def child(
        self,
        start_percent: float,
        end_percent: float,
        prefix: str = "",
    ) -> ProgressCallback:
        start = float(start_percent)
        end = float(end_percent)
        span = end - start

        def callback(fraction: float, message: str) -> None:
            local = min(1.0, max(0.0, float(fraction)))
            text = f"{prefix}{message}" if prefix else message
            self.update(start + span * local, text)

        return callback


class NullProgress:
    def update(self, percent: float, message: str, *, force: bool = False) -> None:
        del percent, message, force

    def fail(self, message: str) -> None:
        del message

    def child(
        self,
        start_percent: float,
        end_percent: float,
        prefix: str = "",
    ) -> ProgressCallback:
        del start_percent, end_percent, prefix

        def callback(fraction: float, message: str) -> None:
            del fraction, message

        return callback
