# -*- coding: utf-8 -*-
"""Robust incremental statistics for condition-region operating-context evidence.

The first condition module needs two different views of liquid/gas ratio:

1. a long-horizon historical reference distribution with stable semantics; and
2. a current incremental-batch distribution used for operating-context shift detection.

Quantiles are not additive, so this module stores a fixed histogram rather than
combining batch-level P05/P50/P95 values. The histogram is intentionally simple
and auditable. Values outside the configured physical analysis range are counted
as under/overflow and never silently clipped into the baseline bins.

Important semantic boundary:
liquid/gas ratio is derived from circulation-pump topology and gas flow. A shift
in this distribution is therefore operating-context evidence, not proof that the
underlying desulfurization process dynamics have drifted. It must not directly
merge/split condition regions or replace second-module dynamic validation.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional


OPERATING_CONTEXT_EVIDENCE_TYPE = "OPERATING_CONTEXT_DISTRIBUTION_SHIFT"
STABLE_STATUS = "STABLE"
WATCH_STATUS = "WATCH"
SUSPECTED_CONTEXT_SHIFT_STATUS = "SUSPECTED_CONTEXT_SHIFT"
STRONG_CONTEXT_SHIFT_STATUS = "STRONG_CONTEXT_SHIFT"
INSUFFICIENT_EVIDENCE_STATUS = "INSUFFICIENT_EVIDENCE"
ACTIVE_CONTEXT_SHIFT_STATUSES = frozenset({
    WATCH_STATUS,
    SUSPECTED_CONTEXT_SHIFT_STATUS,
    STRONG_CONTEXT_SHIFT_STATUS,
})


@dataclass(frozen=True)
class RobustHistogramConfig:
    minimum: float = 0.0
    maximum: float = 100.0
    bin_width: float = 0.25
    trim_low_quantile: float = 0.05
    trim_high_quantile: float = 0.95
    min_batch_samples: int = 300
    min_baseline_samples: int = 300
    min_independent_days: int = 2
    watch_relative_shift: float = 0.04
    suspect_relative_shift: float = 0.06
    strong_relative_shift: float = 0.10
    confirmation_versions: int = 3

    def validate(self) -> None:
        if not all(math.isfinite(v) for v in (self.minimum, self.maximum, self.bin_width)):
            raise ValueError("histogram range must be finite")
        if self.maximum <= self.minimum or self.bin_width <= 0:
            raise ValueError("invalid histogram range")
        if not 0.0 <= self.trim_low_quantile < self.trim_high_quantile <= 1.0:
            raise ValueError("invalid trim quantiles")
        if not (
            0.0 <= self.watch_relative_shift
            <= self.suspect_relative_shift
            <= self.strong_relative_shift
        ):
            raise ValueError("context-shift thresholds must be monotonic")

    @property
    def bin_count(self) -> int:
        self.validate()
        return int(math.ceil((self.maximum - self.minimum) / self.bin_width))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Optional[Mapping[str, Any]]) -> "RobustHistogramConfig":
        if not value:
            return cls()
        allowed = {field for field in cls.__dataclass_fields__}
        return cls(**{key: value[key] for key in allowed if key in value})


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def empty_histogram(config: RobustHistogramConfig) -> Dict[str, Any]:
    return {
        "config": config.to_dict(),
        "counts": [0 for _ in range(config.bin_count)],
        "finite_count": 0,
        "in_range_count": 0,
        "underflow_count": 0,
        "overflow_count": 0,
    }


def sanitize_histogram(
    value: Optional[Mapping[str, Any]],
    config: RobustHistogramConfig,
) -> Dict[str, Any]:
    if not value:
        return empty_histogram(config)
    counts = list(value.get("counts") or [])
    if len(counts) != config.bin_count:
        raise ValueError(
            f"histogram bin geometry mismatch: expected={config.bin_count}, actual={len(counts)}"
        )
    clean_counts = [max(0, int(item or 0)) for item in counts]
    return {
        "config": config.to_dict(),
        "counts": clean_counts,
        "finite_count": max(0, int(value.get("finite_count", sum(clean_counts)) or 0)),
        "in_range_count": max(0, int(value.get("in_range_count", sum(clean_counts)) or 0)),
        "underflow_count": max(0, int(value.get("underflow_count", 0) or 0)),
        "overflow_count": max(0, int(value.get("overflow_count", 0) or 0)),
    }


def add_value(
    histogram: Dict[str, Any],
    value: Any,
    config: RobustHistogramConfig,
) -> None:
    number = _finite(value)
    if number is None:
        return
    histogram["finite_count"] = int(histogram.get("finite_count", 0)) + 1
    if number < config.minimum:
        histogram["underflow_count"] = int(histogram.get("underflow_count", 0)) + 1
        return
    if number > config.maximum:
        histogram["overflow_count"] = int(histogram.get("overflow_count", 0)) + 1
        return
    index = int((number - config.minimum) / config.bin_width)
    index = min(max(index, 0), config.bin_count - 1)
    histogram["counts"][index] += 1
    histogram["in_range_count"] = int(histogram.get("in_range_count", 0)) + 1


def build_histogram(
    values: Iterable[Any],
    config: RobustHistogramConfig,
) -> Dict[str, Any]:
    histogram = empty_histogram(config)
    for value in values:
        add_value(histogram, value, config)
    return histogram


def merge_histograms(
    first: Optional[Mapping[str, Any]],
    second: Optional[Mapping[str, Any]],
    config: RobustHistogramConfig,
) -> Dict[str, Any]:
    left = sanitize_histogram(first, config)
    right = sanitize_histogram(second, config)
    return {
        "config": config.to_dict(),
        "counts": [a + b for a, b in zip(left["counts"], right["counts"])],
        "finite_count": left["finite_count"] + right["finite_count"],
        "in_range_count": left["in_range_count"] + right["in_range_count"],
        "underflow_count": left["underflow_count"] + right["underflow_count"],
        "overflow_count": left["overflow_count"] + right["overflow_count"],
    }


def _quantile_from_counts(
    counts: List[int],
    quantile: float,
    config: RobustHistogramConfig,
) -> Optional[float]:
    total = sum(counts)
    if total <= 0:
        return None
    target = quantile * max(total - 1, 0)
    cumulative = 0
    for index, count in enumerate(counts):
        if count <= 0:
            continue
        if cumulative + count > target:
            inside = (target - cumulative) / max(count, 1)
            lower = config.minimum + index * config.bin_width
            upper = min(lower + config.bin_width, config.maximum)
            return lower + inside * (upper - lower)
        cumulative += count
    return config.maximum


def histogram_quantile(
    histogram: Mapping[str, Any],
    quantile: float,
    config: RobustHistogramConfig,
) -> Optional[float]:
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    clean = sanitize_histogram(histogram, config)
    return _quantile_from_counts(clean["counts"], quantile, config)


def histogram_trimmed_mean(
    histogram: Mapping[str, Any],
    config: RobustHistogramConfig,
) -> Optional[float]:
    clean = sanitize_histogram(histogram, config)
    counts = clean["counts"]
    total = sum(counts)
    if total <= 0:
        return None

    low_mass = config.trim_low_quantile * total
    high_mass = config.trim_high_quantile * total
    cumulative = 0.0
    weighted_sum = 0.0
    kept_mass = 0.0

    for index, count in enumerate(counts):
        if count <= 0:
            continue
        start = cumulative
        end = cumulative + count
        keep_start = max(start, low_mass)
        keep_end = min(end, high_mass)
        keep = max(0.0, keep_end - keep_start)
        if keep:
            center = min(
                config.minimum + (index + 0.5) * config.bin_width,
                config.maximum,
            )
            weighted_sum += keep * center
            kept_mass += keep
        cumulative = end

    return weighted_sum / kept_mass if kept_mass > 0 else None


def summarize_histogram(
    histogram: Mapping[str, Any],
    config: RobustHistogramConfig,
) -> Dict[str, Any]:
    clean = sanitize_histogram(histogram, config)
    finite_count = clean["finite_count"]
    out_of_range = clean["underflow_count"] + clean["overflow_count"]
    return {
        "finite_count": finite_count,
        "in_range_count": clean["in_range_count"],
        "underflow_count": clean["underflow_count"],
        "overflow_count": clean["overflow_count"],
        "out_of_range_ratio": out_of_range / finite_count if finite_count else None,
        "p05": histogram_quantile(clean, 0.05, config),
        "p50": histogram_quantile(clean, 0.50, config),
        "p95": histogram_quantile(clean, 0.95, config),
        "trimmed_mean_05_95": histogram_trimmed_mean(clean, config),
    }


def _relative_shift(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if current is None or baseline is None:
        return None
    denominator = abs(float(baseline))
    if denominator <= 1.0e-12:
        return None
    return abs(float(current) - float(baseline)) / denominator


def classify_distribution_shift(
    baseline_histogram: Mapping[str, Any],
    batch_histogram: Mapping[str, Any],
    config: RobustHistogramConfig,
    *,
    independent_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Classify liquid/gas distribution change as operating-context evidence.

    The returned status intentionally avoids the term ``process drift``. A
    context shift can later become corroborating evidence for process drift,
    but only after independent physical/dynamic evidence is available.
    """
    baseline = summarize_histogram(baseline_histogram, config)
    batch = summarize_histogram(batch_histogram, config)
    d50 = _relative_shift(batch["p50"], baseline["p50"])
    dtrim = _relative_shift(
        batch["trimmed_mean_05_95"],
        baseline["trimmed_mean_05_95"],
    )

    enough_days = independent_days is None or independent_days >= config.min_independent_days
    enough_samples = (
        baseline["in_range_count"] >= config.min_baseline_samples
        and batch["in_range_count"] >= config.min_batch_samples
    )
    if not enough_samples or not enough_days or d50 is None:
        status = INSUFFICIENT_EVIDENCE_STATUS
    elif d50 <= config.watch_relative_shift:
        status = STABLE_STATUS
    elif d50 <= config.suspect_relative_shift:
        status = WATCH_STATUS
    elif d50 <= config.strong_relative_shift:
        status = SUSPECTED_CONTEXT_SHIFT_STATUS
    else:
        status = STRONG_CONTEXT_SHIFT_STATUS

    direction = "UNKNOWN"
    if baseline["p50"] is not None and batch["p50"] is not None:
        if batch["p50"] > baseline["p50"]:
            direction = "UP"
        elif batch["p50"] < baseline["p50"]:
            direction = "DOWN"
        else:
            direction = "FLAT"

    return {
        "status": status,
        "evidence_type": OPERATING_CONTEXT_EVIDENCE_TYPE,
        "structural_evidence": False,
        "direction": direction,
        "independent_days": independent_days,
        "median_relative_shift": d50,
        "trimmed_mean_relative_shift": dtrim,
        "median_and_trimmed_mean_agree": (
            d50 is not None
            and dtrim is not None
            and d50 > config.suspect_relative_shift
            and dtrim > config.watch_relative_shift
        ),
        "baseline": baseline,
        "batch": batch,
    }
