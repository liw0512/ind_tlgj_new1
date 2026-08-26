from __future__ import annotations

"""Fast, inclusive time-window slicing for sorted DataFrames.

The historical implementation repeatedly evaluated full-column boolean masks for
many overlapping windows.  ``TimeWindowIndexer`` keeps the same inclusive
``start <= timestamp <= end`` semantics, but locates window boundaries with
``numpy.searchsorted`` and returns an ``iloc`` slice.
"""

from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class WindowBounds:
    left: int
    right: int  # exclusive


class TimeWindowIndexer:
    """Index inclusive time ranges on a monotonically increasing timestamp column."""

    def __init__(self, frame: pd.DataFrame, timestamp_column: str):
        self.frame = frame
        self.timestamp_column = timestamp_column
        if timestamp_column not in frame.columns:
            raise KeyError(f"时间列不存在: {timestamp_column}")
        timestamps = pd.to_datetime(frame[timestamp_column], errors="coerce")
        if timestamps.isna().any():
            raise ValueError("TimeWindowIndexer 不接受无效时间值")
        if not timestamps.is_monotonic_increasing:
            raise ValueError("TimeWindowIndexer 要求时间列已按升序排列")
        # DatetimeIndex.asi8 and Timestamp.value both use nanoseconds from epoch,
        # including timezone-aware timestamps after UTC normalization.
        self._values = pd.DatetimeIndex(timestamps).asi8

    def __len__(self) -> int:
        return len(self.frame)

    @staticmethod
    def _ns(value: pd.Timestamp) -> int:
        return int(pd.Timestamp(value).value)

    def left(self, start: pd.Timestamp) -> int:
        return int(np.searchsorted(self._values, self._ns(start), side="left"))

    def right(self, end: pd.Timestamp) -> int:
        return int(np.searchsorted(self._values, self._ns(end), side="right"))

    def bounds(self, start: pd.Timestamp, end: pd.Timestamp) -> WindowBounds:
        if pd.Timestamp(end) < pd.Timestamp(start):
            return WindowBounds(0, 0)
        return WindowBounds(self.left(start), self.right(end))

    def slice(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        bounds = self.bounds(start, end)
        return self.frame.iloc[bounds.left : bounds.right]


class IntervalOverlapIndex:
    """Fast overlap queries for a fixed set of closed time intervals.

    Input intervals are merged first.  Query semantics are identical to:
    ``any(start <= right and end >= left for left, right in intervals)``.
    """

    def __init__(self, intervals: Iterable[tuple[pd.Timestamp, pd.Timestamp]]):
        ordered = sorted(
            (pd.Timestamp(left), pd.Timestamp(right))
            for left, right in intervals
            if pd.Timestamp(left) <= pd.Timestamp(right)
        )
        merged: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        for left, right in ordered:
            if not merged or left > merged[-1][1]:
                merged.append((left, right))
            else:
                previous_left, previous_right = merged[-1]
                merged[-1] = (previous_left, max(previous_right, right))
        self._intervals = merged
        self._ends = [right.value for _, right in merged]

    def overlaps(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        if not self._intervals or pd.Timestamp(end) < pd.Timestamp(start):
            return False
        start_ns = pd.Timestamp(start).value
        index = bisect_left(self._ends, start_ns)
        if index >= len(self._intervals):
            return False
        left, right = self._intervals[index]
        return pd.Timestamp(start) <= right and pd.Timestamp(end) >= left
