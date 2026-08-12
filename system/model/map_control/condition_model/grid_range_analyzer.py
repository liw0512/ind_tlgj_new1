# -*- coding: utf-8 -*-
"""CSV range analyzer for the configured condition axes.

The analyzer no longer defines its own load/SO2 fields.  It reads the same
``CONDITION_AXES`` used by initial/incremental/online condition processing, so
range inspection cannot silently drift from the actual model definition.
"""

import argparse
import csv
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Dict, Iterable, List, Sequence

from system.model.map_control.condition_model.condition_config import CONDITION_AXES


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1]
SUMMARY_FILENAME = "condition_grid_range_analysis.csv"
SUGGESTION_FILENAME = "condition_grid_definition_suggestion.csv"


def _configured_axes() -> list[dict]:
    axes = [dict(item) for item in CONDITION_AXES]
    if len(axes) not in {1, 2}:
        raise ValueError("CONDITION_AXES must contain exactly 1 or 2 axes")
    return axes


def percentile(values: List[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("No valid numeric values")
    position = (len(ordered) - 1) * quantile
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    return (
        ordered[low] * (high - position)
        + ordered[high] * (position - low)
    )


def summarize(values: List[float]) -> Dict[str, float]:
    result = {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": mean(values),
        "std": pstdev(values) if len(values) > 1 else 0.0,
    }
    for label, q in (
        ("q001", .001), ("q005", .005), ("q01", .01), ("q05", .05),
        ("q25", .25), ("q50", .5), ("q75", .75), ("q95", .95),
        ("q99", .99), ("q995", .995), ("q999", .999),
    ):
        result[label] = percentile(values, q)
    result["iqr"] = result["q75"] - result["q25"]
    return result


def analyze_csv(paths: Iterable[str], axes: Sequence[dict] | None = None) -> Dict:
    axes = list(axes or _configured_axes())
    columns = [str(axis["column"]) for axis in axes]
    values = {column: [] for column in columns}
    invalid = {column: 0 for column in columns}
    rows = 0
    for path in paths:
        with open(path, "r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                rows += 1
                for column in columns:
                    try:
                        value = float(row[column])
                        if not math.isfinite(value):
                            raise ValueError
                        values[column].append(value)
                    except (KeyError, TypeError, ValueError):
                        invalid[column] += 1
    statistics = {}
    for column, item in values.items():
        if not item:
            raise ValueError(
                f"configured condition axis has no valid numeric data: {column}"
            )
        statistics[column] = summarize(item)
    return {"raw_rows": rows, "invalid": invalid, "statistics": statistics}


def grid_suggestion(
    summary: Dict,
    axes: Sequence[dict] | None = None,
) -> Dict:
    axes = list(axes or _configured_axes())

    def axis(column: str, step: float) -> dict:
        stats = summary["statistics"][column]
        return {
            "column": column,
            "min": math.floor((stats["q005"] - step) / step) * step,
            "max": math.ceil((stats["q995"] + step) / step) * step,
            "step": step,
        }

    return {
        "condition_axes": [
            axis(str(item["column"]), float(item["step"]))
            for item in axes
        ],
        "out_of_range_policy": "clip",
        "auto_apply_to_config": False,
    }


def write_csv_outputs(
    report: Dict,
    suggestion: Dict,
    output_dir: Path,
) -> Dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / SUMMARY_FILENAME
    suggestion_path = output_dir / SUGGESTION_FILENAME

    statistic_fields = [
        "count", "min", "max", "mean", "std", "q001", "q005", "q01",
        "q05", "q25", "q50", "q75", "q95", "q99", "q995", "q999",
        "iqr",
    ]
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["column", "raw_rows", "invalid_count"] + statistic_fields,
        )
        writer.writeheader()
        for column, stats in report["statistics"].items():
            row = {
                "column": column,
                "raw_rows": report["raw_rows"],
                "invalid_count": report["invalid"][column],
            }
            row.update({field: stats.get(field) for field in statistic_fields})
            writer.writerow(row)

    with open(suggestion_path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "axis_index", "source_column", "min", "max", "step",
                "out_of_range_policy", "auto_apply_to_config",
            ],
        )
        writer.writeheader()
        for index, axis in enumerate(suggestion["condition_axes"], start=1):
            writer.writerow(
                {
                    "axis_index": index,
                    "source_column": axis["column"],
                    "min": axis["min"],
                    "max": axis["max"],
                    "step": axis["step"],
                    "out_of_range_policy": suggestion["out_of_range_policy"],
                    "auto_apply_to_config": suggestion["auto_apply_to_config"],
                }
            )
    return {
        "summary_csv": str(summary_path),
        "suggestion_csv": str(suggestion_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze ranges for condition_config.CONDITION_AXES"
    )
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    axes = _configured_axes()
    report = analyze_csv(args.input, axes)
    suggestion = grid_suggestion(report, axes)
    outputs = write_csv_outputs(report, suggestion, Path(args.output))
    for label, path in outputs.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
