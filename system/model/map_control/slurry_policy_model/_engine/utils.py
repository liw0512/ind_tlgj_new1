from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(text: Any) -> str:
    value = str(text)
    value = re.sub(r"[^0-9A-Za-z_.-]+", "_", value)
    return value.strip("_") or "UNKNOWN"


def strict_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): strict_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [strict_json_value(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def write_json(path: str | Path, data: Any, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(
            strict_json_value(data),
            handle,
            ensure_ascii=False,
            indent=indent,
            allow_nan=False,
            sort_keys=False,
        )
    os.replace(tmp, path)


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_text(text: str, length: int = 20) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]



def normalize_condition_label(value: Any) -> str:
    """将 condition_label 规范为稳定、便于目录展示的字符串。

    pandas 读取纯数字标签时可能得到 ``619.0``。该函数会将整型数值规范为
    ``619``，缺失值规范为 ``UNKNOWN``，其余值保留去除首尾空白后的文本。
    """
    if value is None or pd.isna(value):
        return "UNKNOWN"
    if isinstance(value, (np.integer, int)):
        return str(int(value))
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if not math.isfinite(number):
            return "UNKNOWN"
        if number.is_integer():
            return str(int(number))
        return format(number, "g")
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return "UNKNOWN"
    try:
        number = float(text)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
    except (TypeError, ValueError):
        pass
    return text

def consecutive_unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = "" if pd.isna(value) else str(value)
        if not result or result[-1] != text:
            result.append(text)
    return result


def robust_slope_per_minute(times: pd.Series, values: pd.Series) -> float:
    mask = times.notna() & values.notna()
    if mask.sum() < 2:
        return float("nan")
    t = times[mask]
    v = values[mask].astype(float)
    x = (t - t.iloc[0]).dt.total_seconds().to_numpy(dtype=float) / 60.0
    y = v.to_numpy(dtype=float)
    if np.ptp(x) <= 0:
        return 0.0
    # 简单线性斜率；窗口短且使用中位去噪后的统计，足够可解释。
    return float(np.polyfit(x, y, 1)[0])


def quantiles(series: pd.Series, probs: tuple[float, ...]) -> list[float]:
    clean = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return [float("nan") for _ in probs]
    return [float(clean.quantile(p)) for p in probs]


def median_or_nan(series: pd.Series) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.median()) if not clean.empty else float("nan")


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "t"}
