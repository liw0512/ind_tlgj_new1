# -*- coding: utf-8 -*-
"""Standard-library CSV range analyzer for manual grid confirmation."""

import argparse
import csv
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Dict, Iterable, List


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1]
SUMMARY_FILENAME = "condition_grid_range_analysis.csv"
SUGGESTION_FILENAME = "condition_grid_definition_suggestion.csv"


def percentile(values: List[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("No valid numeric values")
    position = (len(ordered) - 1) * quantile
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def summarize(values: List[float]) -> Dict[str, float]:
    result = {"count": len(values), "min": min(values), "max": max(values), "mean": mean(values),
              "std": pstdev(values) if len(values) > 1 else 0.0}
    for label, q in (("q001", .001), ("q005", .005), ("q01", .01), ("q05", .05), ("q25", .25),
                     ("q50", .5), ("q75", .75), ("q95", .95), ("q99", .99), ("q995", .995), ("q999", .999)):
        result[label] = percentile(values, q)
    result["iqr"] = result["q75"] - result["q25"]
    return result


def analyze_csv(paths: Iterable[str], load_column: str = "jzfh", so2_column: str = "yyq_SO2") -> Dict:
    values = {load_column: [], so2_column: []}
    invalid = {load_column: 0, so2_column: 0}
    rows = 0
    for path in paths:
        with open(path, "r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                rows += 1
                for column in values:
                    try:
                        value = float(row[column])
                        if not math.isfinite(value):
                            raise ValueError
                        values[column].append(value)
                    except (KeyError, TypeError, ValueError):
                        invalid[column] += 1
    return {"raw_rows": rows, "invalid": invalid, "statistics": {key: summarize(item) for key, item in values.items()}}


def grid_suggestion(summary: Dict, load_step: float, so2_step: float, load_column="jzfh", so2_column="yyq_SO2") -> Dict:
    def axis(column, step):
        stats = summary["statistics"][column]
        return {"min": math.floor((stats["q005"] - step) / step) * step,
                "max": math.ceil((stats["q995"] + step) / step) * step, "step": step}
    return {"grid_definition": {load_column: axis(load_column, load_step), so2_column: axis(so2_column, so2_step)},
            "out_of_range_policy": "clip", "auto_apply_to_config": False}


def write_csv_outputs(
    report: Dict,
    suggestion: Dict,
    output_dir: Path,
    load_column: str = "jzfh",
    so2_column: str = "yyq_SO2",
) -> Dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / SUMMARY_FILENAME
    suggestion_path = output_dir / SUGGESTION_FILENAME

    statistic_fields = [
        "count", "min", "max", "mean", "std", "q001", "q005", "q01", "q05",
        "q25", "q50", "q75", "q95", "q99", "q995", "q999", "iqr",
    ]
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["column", "raw_rows", "invalid_count"] + statistic_fields,
        )
        writer.writeheader()
        for column, stats in report["statistics"].items():
            row = {"column": column, "raw_rows": report["raw_rows"], "invalid_count": report["invalid"][column]}
            row.update({field: stats.get(field) for field in statistic_fields})
            writer.writerow(row)

    with open(suggestion_path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["config_key", "source_column", "min", "max", "step", "out_of_range_policy", "auto_apply_to_config"],
        )
        writer.writeheader()
        for config_key, axis in suggestion["grid_definition"].items():
            writer.writerow({
                "config_key": config_key,
                "source_column": config_key,
                "min": axis["min"],
                "max": axis["max"],
                "step": axis["step"],
                "out_of_range_policy": suggestion["out_of_range_policy"],
                "auto_apply_to_config": suggestion["auto_apply_to_config"],
            })
    return {"summary_csv": str(summary_path), "suggestion_csv": str(suggestion_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze manual V3 grid ranges from selected CSV files")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--load-col", default="jzfh")
    parser.add_argument("--so2-col", default="yyq_SO2")
    parser.add_argument("--load-step", type=float, required=True)
    parser.add_argument("--so2-step", type=float, required=True)
    args = parser.parse_args()
    report = analyze_csv(args.input, args.load_col, args.so2_col)
    suggestion = grid_suggestion(report, args.load_step, args.so2_step, args.load_col, args.so2_col)
    outputs = write_csv_outputs(report, suggestion, Path(args.output), args.load_col, args.so2_col)
    for label, path in outputs.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
