from __future__ import annotations

import glob
from pathlib import Path
from typing import Any, Callable, Sequence

import pandas as pd

from .config_loader import all_valves, enabled_towers
from .exceptions import InputDataError
from .schema import (
    ALL_CONDITION_COLUMNS,
    OUTLET_SO2_COLUMN,
    REQUIRED_CONDITION_COLUMNS,
    condition_axis_columns,
    time_column,
)


def resolve_input_paths(input_specs: Sequence[str] | str) -> list[Path]:
    specs = [input_specs] if isinstance(input_specs, str) else list(input_specs)
    found: list[Path] = []
    for spec in specs:
        path = Path(spec)
        if path.is_dir():
            found.extend(sorted(path.glob("*.csv")))
        elif path.is_file():
            found.append(path)
        else:
            found.extend(Path(p) for p in sorted(glob.glob(spec)))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    if not unique:
        raise InputDataError(f"未找到输入 CSV: {specs}")
    return unique


def required_columns(
    plant: dict[str, Any], training: dict[str, Any]
) -> list[str]:
    """Return required fields for the current condition snapshot.

    The condition-axis columns are injected into ``training`` from the matching
    first-module snapshot.  The policy model therefore never hard-codes unit
    load or inlet-SO2 as mandatory fields.
    """
    required = [
        time_column(plant),
        *condition_axis_columns(training),
        OUTLET_SO2_COLUMN,
        *REQUIRED_CONDITION_COLUMNS,
    ]
    for tower in enabled_towers(plant):
        required.append(tower["ph_column"])
    required.extend(v["column"] for v in all_valves(plant))
    required.extend(
        str(c) for c in (plant.get("supply_pump_state_columns", []) or [])
    )
    return list(dict.fromkeys(required))


def selected_input_columns(
    plant: dict[str, Any], training: dict[str, Any]
) -> list[str]:
    """Return every raw column used by the current training pipeline."""
    return list(
        dict.fromkeys(
            [*required_columns(plant, training), *ALL_CONDITION_COLUMNS]
        )
    )


def _read_csv_selected(
    path: Path,
    *,
    encoding: str,
    selected_columns: set[str] | None,
) -> pd.DataFrame:
    usecols = None
    if selected_columns is not None:
        usecols = (
            lambda name: str(name).replace("\ufeff", "").strip()
            in selected_columns
        )
    try:
        return pd.read_csv(
            path,
            encoding=encoding,
            low_memory=False,
            usecols=usecols,
        )
    except UnicodeDecodeError:
        return pd.read_csv(
            path,
            encoding="utf-8",
            low_memory=False,
            usecols=usecols,
        )


def load_input_data(
    input_specs: Sequence[str] | str,
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    paths = resolve_input_paths(input_specs)
    if progress:
        progress(0.02, f"已定位 {len(paths)} 个输入 CSV")
    frames: list[pd.DataFrame] = []
    encoding = training["io"]["csv_encoding"]
    performance_cfg = training.get("performance", {})
    selected = (
        set(selected_input_columns(plant, training))
        if bool(performance_cfg.get("read_only_required_columns", True))
        else None
    )
    for file_index, path in enumerate(paths, start=1):
        frame = _read_csv_selected(
            path,
            encoding=encoding,
            selected_columns=selected,
        )
        frame.columns = [
            str(c).replace("\ufeff", "").strip() for c in frame.columns
        ]
        duplicates = frame.columns[frame.columns.duplicated()].tolist()
        if duplicates:
            raise InputDataError(
                f"CSV 标准化后存在重复字段: {duplicates}; 文件={path}"
            )
        frame["__source_file"] = str(path)
        frames.append(frame)
        if progress:
            progress(
                0.05 + 0.45 * file_index / max(len(paths), 1),
                f"读取输入文件 {file_index}/{len(paths)}：{path.name}",
            )
    df = pd.concat(frames, ignore_index=True, sort=False)
    if progress:
        progress(0.55, f"输入文件合并完成，共 {len(df)} 行")

    required_now = required_columns(plant, training)
    missing = [column for column in required_now if column not in df.columns]
    if progress:
        progress(0.62, "校验必要字段")
    if missing and training["io"].get("strict_required_columns", True):
        raise InputDataError(
            "输入数据缺少第二模块必要字段: "
            f"{missing}。工况轴字段来自指定第一模块 snapshot，"
            "其余第一模块 condition/grid/version 字段必须存在于标注后 CSV。"
        )

    for column in ALL_CONDITION_COLUMNS:
        if column not in df.columns:
            df[column] = None

    ts_col = time_column(plant)
    timestamp_format = training["io"].get("timestamp_format")
    df[ts_col] = pd.to_datetime(
        df[ts_col], format=timestamp_format, errors="coerce"
    )
    if progress:
        progress(0.72, f"解析时间列 {ts_col}")
    invalid_ts = int(df[ts_col].isna().sum())
    df = df[df[ts_col].notna()].copy()
    if df.empty:
        raise InputDataError(f"时间列 {ts_col!r} 解析后没有有效数据")

    if training["preprocessing"].get("coerce_numeric", True):
        numeric_cols = [
            *condition_axis_columns(training),
            OUTLET_SO2_COLUMN,
            *(t["ph_column"] for t in enabled_towers(plant)),
            *(v["column"] for v in all_valves(plant)),
        ]
        numeric_list = list(
            dict.fromkeys(c for c in numeric_cols if c in df.columns)
        )
        if numeric_list:
            df[numeric_list] = df[numeric_list].apply(
                pd.to_numeric, errors="coerce"
            )
        if progress:
            progress(0.88, f"转换数值字段，共 {len(numeric_list)} 个")

    keep = training["io"].get("drop_duplicate_timestamp_keep", "last")
    skip_sort = bool(
        training.get("performance", {}).get(
            "skip_sort_when_already_ordered", True
        )
    )
    already_sorted = bool(df[ts_col].is_monotonic_increasing)
    has_duplicates = bool(df[ts_col].duplicated(keep=False).any())
    if not (skip_sort and already_sorted):
        df.sort_values(ts_col, inplace=True, kind="stable")
    if has_duplicates:
        df.drop_duplicates(subset=[ts_col], keep=keep, inplace=True)
    df.reset_index(drop=True, inplace=True)

    if progress:
        if skip_sort and already_sorted and not has_duplicates:
            progress(0.96, "时间列已升序且无重复，跳过全表排序")
        else:
            progress(0.96, "完成时间排序和重复时间记录处理")
    warnings: list[str] = []
    if invalid_ts:
        warnings.append(f"已删除 {invalid_ts} 行无效时间值，时间列={ts_col}")
    if progress:
        progress(1.0, f"输入数据准备完成，有效 {len(df)} 行")
    return df, warnings


def assign_continuous_segments(
    df: pd.DataFrame, plant: dict[str, Any], training: dict[str, Any]
) -> pd.DataFrame:
    result = df.copy()
    ts_col = time_column(plant)
    max_gap = float(
        training["preprocessing"]["max_continuous_gap_seconds"]
    )
    gaps = result[ts_col].diff().dt.total_seconds().fillna(0)
    result["continuous_segment_id"] = (gaps > max_gap).cumsum().astype(int)
    return result
