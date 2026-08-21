from __future__ import annotations

"""第一模块工况快照读取、版本握手和历史 episode 重映射。

第二模块不修改第一模块。它只读取第一模块发布的 condition_snapshot.json，
以不可变 ``grid_id`` 为主键，把历史 episode 重新归属到当前版本的
``condition_label``，再重新聚合所有派生 PKL。
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .exceptions import ConfigurationError, SnapshotError
from .utils import normalize_condition_label, sha256_file

_VERSION_RE = re.compile(r"^v(?P<n>\d+)$", re.IGNORECASE)
_GRID_RE = re.compile(r"^P(?P<p>\d+)-S(?P<s>\d+)$", re.IGNORECASE)


def parse_grid_id(value: Any) -> tuple[int, int] | None:
    """Parse stable internal grid slots P(first axis)-S(second axis)."""
    match = _GRID_RE.match(str(value).strip())
    if not match:
        return None
    return int(match.group("p")), int(match.group("s"))


def version_number(value: str) -> int:
    match = _VERSION_RE.match(str(value).strip())
    if not match:
        raise SnapshotError(f"第一模块版本必须使用 v001、v002 形式，实际为: {value!r}")
    return int(match.group("n"))


def _version_sort_key(path: Path) -> int:
    try:
        return version_number(path.name)
    except Exception:
        return -1


def resolve_condition_snapshot_path(
    requested: str | Path | None,
    configured_snapshots_dir: str | Path | None = None,
) -> Path:
    """解析第一模块快照。

    支持：
    - condition_snapshot.json 文件；
    - 单个 v### 目录；
    - snapshots 根目录，自动选择最新可读 v###；
    - requested 为 latest 时使用 configured_snapshots_dir。
    """
    if requested is None or str(requested).strip().lower() == "latest":
        if not configured_snapshots_dir:
            raise ConfigurationError(
                "未传入 --condition-snapshot，且 PLANT_CONFIG.paths.condition_snapshots_dir 未配置"
            )
        candidate = Path(configured_snapshots_dir)
    else:
        candidate = Path(requested)

    if candidate.is_file():
        return candidate
    if candidate.is_dir() and (candidate / "condition_snapshot.json").is_file():
        return candidate / "condition_snapshot.json"
    if candidate.is_dir():
        version_dirs = sorted(
            [item for item in candidate.iterdir() if item.is_dir() and _version_sort_key(item) >= 0],
            key=_version_sort_key,
            reverse=True,
        )
        errors: list[str] = []
        for version_dir in version_dirs:
            path = version_dir / "condition_snapshot.json"
            if not path.is_file():
                continue
            try:
                with path.open("r", encoding="utf-8") as stream:
                    value = json.load(stream)
                version_number(str(value.get("snapshot_version", "")))
                return path
            except Exception as exc:
                errors.append(f"{path}: {exc}")
        detail = "; ".join(errors) if errors else "没有找到 v###/condition_snapshot.json"
        raise SnapshotError(f"无法从第一模块快照目录读取有效版本: {candidate}; {detail}")
    raise SnapshotError(f"第一模块工况快照路径不存在: {candidate}")


@dataclass(frozen=True)
class GridConditionRecord:
    grid_id: str
    condition_label: str
    policy_region_id: str
    region_status: str
    region_member_count: int
    base_condition_id: str
    load_level: int
    inlet_so2_level: int


@dataclass
class ConditionSnapshotIndex:
    snapshot_path: Path
    snapshot_version: str
    previous_snapshot_version: str | None
    snapshot_sha256: str
    mapping_sha256: str
    grid_records: dict[str, GridConditionRecord]
    condition_members: dict[str, list[str]]
    raw_snapshot: dict[str, Any]

    def record_for_grid(self, grid_id: Any) -> GridConditionRecord | None:
        return self.grid_records.get(str(grid_id).strip())

    def to_metadata(self) -> dict[str, Any]:
        return {
            "condition_snapshot_path": str(self.snapshot_path.resolve()),
            "condition_snapshot_version": self.snapshot_version,
            "condition_previous_snapshot_version": self.previous_snapshot_version,
            "condition_snapshot_sha256": self.snapshot_sha256,
            "grid_condition_mapping_sha256": self.mapping_sha256,
            "grid_condition_mapping_count": len(self.grid_records),
            "condition_count": len(self.condition_members),
        }


def _inlet_cell_count(snapshot: dict[str, Any]) -> int | None:
    grid_config = snapshot.get("grid_config") or {}
    definition = grid_config.get("grid_definition") or grid_config
    for key in ("yyq_SO2", "inlet_so2"):
        axis = definition.get(key) if isinstance(definition, dict) else None
        if isinstance(axis, dict):
            try:
                minimum = float(axis.get("min", axis.get("minimum")))
                maximum = float(axis.get("max", axis.get("maximum")))
                step = float(axis.get("step"))
                if step > 0:
                    import math
                    return int(math.ceil((maximum - minimum) / step))
            except Exception:
                pass
    return None


def load_condition_snapshot_index(path: str | Path) -> ConditionSnapshotIndex:
    snapshot_path = Path(path)
    try:
        with snapshot_path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except Exception as exc:
        raise SnapshotError(f"第一模块 condition_snapshot.json 无法读取: {snapshot_path}; {exc}") from exc

    version = str(value.get("snapshot_version", "")).strip()
    version_number(version)
    grid_catalog = value.get("grid_catalog")
    regions = value.get("policy_regions")
    if not isinstance(grid_catalog, dict) or not isinstance(regions, dict):
        raise SnapshotError("第一模块快照缺少 grid_catalog 或 policy_regions")

    inlet_count = _inlet_cell_count(value)
    if inlet_count is None:
        parsed_levels = [parse_grid_id(grid_id) for grid_id in grid_catalog]
        inlet_levels = [item[1] for item in parsed_levels if item is not None]
        inlet_count = max(inlet_levels, default=None)
    records: dict[str, GridConditionRecord] = {}
    members: dict[str, list[str]] = {}
    problems: list[str] = []

    for grid_id, cell_value in grid_catalog.items():
        if not isinstance(cell_value, dict):
            problems.append(f"{grid_id}: grid cell 不是对象")
            continue
        grid_text = str(grid_id).strip()
        parsed = parse_grid_id(grid_text)
        if parsed is None:
            problems.append(f"{grid_text}: grid_id 格式无效")
            continue
        region_id = str(cell_value.get("policy_region_id", "")).strip()
        region = regions.get(region_id)
        if not isinstance(region, dict):
            problems.append(f"{grid_text}: policy_region_id={region_id!r} 不存在")
            continue
        label = normalize_condition_label(region.get("condition_label", "UNKNOWN"))
        if label == "UNKNOWN":
            problems.append(f"{grid_text}: region {region_id} 缺少 condition_label")
            continue
        member_ids = [str(item).strip() for item in (region.get("member_grid_ids") or [])]
        region_member_count = len(member_ids) if member_ids else 1
        load_level = int(cell_value.get("load_level", parsed[0]))
        inlet_level = int(cell_value.get("inlet_so2_level", parsed[1]))
        if inlet_count:
            base_id = str((load_level - 1) * inlet_count + inlet_level)
        else:
            base_id = str(cell_value.get("base_condition_id", f"{load_level}:{inlet_level}"))
        record = GridConditionRecord(
            grid_id=grid_text,
            condition_label=label,
            policy_region_id=region_id,
            region_status=str(region.get("status", "INDEPENDENT")),
            region_member_count=region_member_count,
            base_condition_id=base_id,
            load_level=load_level,
            inlet_so2_level=inlet_level,
        )
        if grid_text in records:
            problems.append(f"{grid_text}: 重复映射")
            continue
        records[grid_text] = record
        members.setdefault(label, []).append(grid_text)

    if problems:
        raise SnapshotError("第一模块快照映射无效: " + "; ".join(problems[:20]))
    if len(records) != len(grid_catalog):
        raise SnapshotError(
            f"第一模块快照存在未解析基础格: catalog={len(grid_catalog)}, mapped={len(records)}"
        )

    for label, grid_ids in members.items():
        grid_ids.sort(key=lambda item: (
            records[item].load_level,
            records[item].inlet_so2_level,
        ))

    mapping_payload = [
        {
            "grid_id": grid_id,
            "condition_label": records[grid_id].condition_label,
            "policy_region_id": records[grid_id].policy_region_id,
            "region_status": records[grid_id].region_status,
        }
        for grid_id in sorted(records, key=lambda item: (records[item].load_level, records[item].inlet_so2_level))
    ]
    mapping_bytes = json.dumps(
        mapping_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    mapping_sha = hashlib.sha256(mapping_bytes).hexdigest()

    return ConditionSnapshotIndex(
        snapshot_path=snapshot_path,
        snapshot_version=version,
        previous_snapshot_version=(
            str(value.get("previous_snapshot_version")).strip()
            if value.get("previous_snapshot_version") is not None
            else None
        ),
        snapshot_sha256=sha256_file(snapshot_path),
        mapping_sha256=mapping_sha,
        grid_records=records,
        condition_members=members,
        raw_snapshot=value,
    )


def validate_input_frame_alignment(
    frame: pd.DataFrame,
    index: ConditionSnapshotIndex,
    *,
    context: str,
) -> dict[str, Any]:
    required = {"grid_id", "condition_label", "condition_snapshot_version"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ConfigurationError(f"{context} 缺少第一模块对齐字段: {missing}")
    versions = sorted({str(v).strip() for v in frame["condition_snapshot_version"].dropna()})
    if versions != [index.snapshot_version]:
        raise ConfigurationError(
            f"{context} 的 condition_snapshot_version={versions}，但指定第一模块快照为 {index.snapshot_version}"
        )

    grid_text = frame["grid_id"].astype(str).str.strip()
    label_map = {
        grid_id: record.condition_label for grid_id, record in index.grid_records.items()
    }
    expected = grid_text.map(label_map)
    unresolved_mask = expected.isna()
    if unresolved_mask.any():
        unresolved = sorted(set(grid_text[unresolved_mask].tolist()))[:20]
        raise ConfigurationError(
            f"{context} 含第一模块当前快照无法识别的 grid_id: {unresolved}"
        )
    actual = frame["condition_label"].map(normalize_condition_label)
    mismatch_mask = actual != expected
    if mismatch_mask.any():
        examples = pd.DataFrame(
            {
                "grid_id": grid_text[mismatch_mask],
                "csv_condition_label": actual[mismatch_mask],
                "snapshot_condition_label": expected[mismatch_mask],
            }
        ).head(10).to_dict(orient="records")
        raise ConfigurationError(
            f"{context} 的 grid_id→condition_label 与第一模块快照不一致，示例: {examples}"
        )
    return {
        "row_count": int(len(frame)),
        "condition_snapshot_version": index.snapshot_version,
        "mapping_sha256": index.mapping_sha256,
        "mismatch_count": 0,
        "unresolved_count": 0,
    }


def _path_values(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(">") if part.strip()]


def _consecutive_unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if not output or output[-1] != value:
            output.append(value)
    return output


def remap_episode_conditions(
    frame: pd.DataFrame,
    index: ConditionSnapshotIndex,
    *,
    strict: bool,
    dataset_name: str,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    """按当前第一模块快照重映射历史 episode。

    V1.8B uses vectorized grid maps for the fixed fields and caches path
    remapping by unique grid-transition path.  Field meanings remain identical
    to V1.7.
    """
    result = frame.copy()
    if result.empty:
        for column in (
            "original_condition_label",
            "original_condition_snapshot_version",
            "previous_condition_label",
            "current_condition_label",
            "current_condition_snapshot_version",
            "condition_remapped",
        ):
            if column not in result.columns:
                result[column] = pd.Series(dtype="object")
        return result, {
            "dataset": dataset_name,
            "episode_count": 0,
            "resolved_episode_count": 0,
            "unresolved_episode_count": 0,
            "remapped_episode_count": 0,
            "unchanged_episode_count": 0,
        }, pd.DataFrame()

    if "anchor_grid_id" not in result.columns:
        if "start_grid_id" in result.columns:
            result["anchor_grid_id"] = result["start_grid_id"]
        else:
            raise SnapshotError(f"{dataset_name} 缺少 anchor_grid_id/start_grid_id，无法重映射")

    text_columns = [
        "condition_label", "anchor_condition_label", "condition_snapshot_version",
        "policy_region_id", "region_status", "base_condition_id",
        "start_condition_label", "end_condition_label", "condition_label_path",
        "original_condition_label", "original_condition_snapshot_version",
        "previous_condition_label", "current_condition_label",
        "current_condition_snapshot_version",
    ]
    for column in text_columns:
        if column in result.columns:
            result[column] = result[column].astype("object")

    if "condition_label" not in result.columns:
        result["condition_label"] = "UNKNOWN"
    old_labels = result["condition_label"].map(normalize_condition_label)
    if "original_condition_label" not in result.columns:
        result["original_condition_label"] = result["condition_label"]
    if "original_condition_snapshot_version" not in result.columns:
        result["original_condition_snapshot_version"] = result.get(
            "condition_snapshot_version", "UNKNOWN"
        )
    for column in (
        "previous_condition_label", "current_condition_label",
        "current_condition_snapshot_version", "anchor_condition_label",
        "condition_snapshot_version", "policy_region_id", "region_status",
        "base_condition_id", "start_condition_label", "end_condition_label",
        "condition_label_path",
    ):
        if column not in result.columns:
            result[column] = pd.Series(None, index=result.index, dtype="object")
    if "region_member_count" not in result.columns:
        result["region_member_count"] = pd.Series(pd.NA, index=result.index, dtype="Int64")

    anchor_grid = result["anchor_grid_id"].astype(str).str.strip()
    mapping = pd.DataFrame(
        {
            "condition_label": {k: v.condition_label for k, v in index.grid_records.items()},
            "policy_region_id": {k: v.policy_region_id for k, v in index.grid_records.items()},
            "region_status": {k: v.region_status for k, v in index.grid_records.items()},
            "region_member_count": {k: v.region_member_count for k, v in index.grid_records.items()},
            "base_condition_id": {k: v.base_condition_id for k, v in index.grid_records.items()},
        }
    )
    new_labels = anchor_grid.map(mapping["condition_label"])
    resolved_mask = new_labels.notna()
    unresolved_mask = ~resolved_mask

    unresolved = pd.DataFrame(
        {
            "dataset": dataset_name,
            "row_index": [
                int(value) if isinstance(value, int) else str(value)
                for value in result.index[unresolved_mask]
            ],
            "episode_id": (
                result.loc[unresolved_mask, "episode_id"].tolist()
                if "episode_id" in result.columns
                else [None] * int(unresolved_mask.sum())
            ),
            "anchor_grid_id": anchor_grid[unresolved_mask].tolist(),
            "old_condition_label": old_labels[unresolved_mask].tolist(),
            "reason": "ANCHOR_GRID_NOT_IN_CURRENT_CONDITION_SNAPSHOT",
        }
    )
    if strict and not unresolved.empty:
        examples = unresolved.head(10).to_dict(orient="records")
        raise SnapshotError(
            f"{dataset_name} 有 {len(unresolved)} 条 episode 无法按当前第一模块快照重映射，示例: {examples}"
        )

    existing_current_version = result["current_condition_snapshot_version"].fillna("").astype(str).str.strip()
    update_previous = resolved_mask & (existing_current_version != index.snapshot_version)
    result.loc[update_previous, "previous_condition_label"] = old_labels[update_previous]

    resolved_grids = anchor_grid[resolved_mask]
    result.loc[resolved_mask, "current_condition_label"] = new_labels[resolved_mask]
    result.loc[resolved_mask, "current_condition_snapshot_version"] = index.snapshot_version
    result.loc[resolved_mask, "condition_label"] = new_labels[resolved_mask]
    result.loc[resolved_mask, "anchor_condition_label"] = new_labels[resolved_mask]
    result.loc[resolved_mask, "condition_snapshot_version"] = index.snapshot_version
    for column in ("policy_region_id", "region_status", "region_member_count", "base_condition_id"):
        values = resolved_grids.map(mapping[column])
        result.loc[resolved_mask, column] = values.to_numpy()

    changed = resolved_mask & (old_labels != new_labels)
    if "condition_remapped" in result.columns:
        existing_remapped = result["condition_remapped"].fillna(False).map(
            lambda value: str(value).strip().lower() in {"true", "1"}
        )
    else:
        existing_remapped = pd.Series(False, index=result.index)
    result["condition_remapped"] = (existing_remapped | changed).astype(bool)

    label_map = mapping["condition_label"].to_dict()
    start_grid = (
        result["start_grid_id"].astype(str).str.strip()
        if "start_grid_id" in result.columns
        else anchor_grid
    )
    end_grid = (
        result["end_grid_id"].astype(str).str.strip()
        if "end_grid_id" in result.columns
        else anchor_grid
    )
    start_labels = start_grid.map(label_map).fillna(new_labels)
    end_labels = end_grid.map(label_map).fillna(new_labels)
    result.loc[resolved_mask, "start_condition_label"] = start_labels[resolved_mask]
    result.loc[resolved_mask, "end_condition_label"] = end_labels[resolved_mask]

    path_source = (
        result["grid_transition_path"].fillna("").astype(str)
        if "grid_transition_path" in result.columns
        else pd.Series("", index=result.index)
    )
    path_cache: dict[tuple[str, str, str], tuple[str, int] | None] = {}
    for row_index in result.index[resolved_mask]:
        key = (path_source.at[row_index], start_grid.at[row_index], end_grid.at[row_index])
        if key not in path_cache:
            grids = _path_values(key[0])
            if not grids:
                grids = [key[1]] + ([] if key[2] == key[1] else [key[2]])
            labels = [label_map.get(grid) for grid in grids]
            if any(label is None for label in labels):
                path_cache[key] = None
            else:
                label_path = _consecutive_unique([str(label) for label in labels])
                path_cache[key] = (">".join(label_path), max(0, len(label_path) - 1))
        mapped_path = path_cache[key]
        if mapped_path is not None:
            result.at[row_index, "condition_label_path"] = mapped_path[0]
            result.at[row_index, "condition_label_change_count"] = mapped_path[1]

    changes_frame = pd.DataFrame(
        {
            "old": old_labels[resolved_mask].astype(str),
            "new": new_labels[resolved_mask].astype(str),
        }
    )
    old_new_counts = (
        changes_frame.value_counts(sort=False).to_dict() if not changes_frame.empty else {}
    )
    remapped_count = int(changed.sum())
    resolved_count = int(resolved_mask.sum())
    report = {
        "dataset": dataset_name,
        "episode_count": int(len(result)),
        "resolved_episode_count": resolved_count,
        "unresolved_episode_count": int(unresolved_mask.sum()),
        "remapped_episode_count": remapped_count,
        "unchanged_episode_count": int(resolved_count - remapped_count),
        "condition_changes": [
            {
                "old_condition_label": old,
                "new_condition_label": new,
                "episode_count": int(count),
            }
            for (old, new), count in sorted(old_new_counts.items())
            if old != new
        ],
    }
    return result, report, unresolved


def remap_raw_condition_rows(
    frame: pd.DataFrame,
    index: ConditionSnapshotIndex,
    *,
    strict: bool = True,
    context: str = "raw context",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """把上一版 context_tail 的工况列更新到当前第一模块版本。"""
    result = frame.copy()
    if result.empty:
        return result, {"row_count": 0, "remapped_row_count": 0, "unresolved_row_count": 0}
    if "grid_id" not in result.columns:
        raise SnapshotError(f"{context} 缺少 grid_id，无法按当前第一模块快照重映射")
    for column in (
        "condition_label", "condition_snapshot_version", "policy_region_id",
        "region_status", "base_condition_id",
    ):
        if column in result.columns:
            result[column] = result[column].astype("object")
        else:
            result[column] = pd.Series(None, index=result.index, dtype="object")
    if "region_member_count" not in result.columns:
        result["region_member_count"] = pd.Series(pd.NA, index=result.index, dtype="Int64")

    grid_text = result["grid_id"].astype(str).str.strip()
    mapping = pd.DataFrame(
        {
            "condition_label": {k: v.condition_label for k, v in index.grid_records.items()},
            "policy_region_id": {k: v.policy_region_id for k, v in index.grid_records.items()},
            "region_status": {k: v.region_status for k, v in index.grid_records.items()},
            "region_member_count": {k: v.region_member_count for k, v in index.grid_records.items()},
            "base_condition_id": {k: v.base_condition_id for k, v in index.grid_records.items()},
        }
    )
    expected = grid_text.map(mapping["condition_label"])
    unresolved_mask = expected.isna()
    unresolved = [
        {"row_index": str(row_index), "grid_id": grid_text.at[row_index]}
        for row_index in result.index[unresolved_mask]
    ]
    if strict and unresolved:
        raise SnapshotError(f"{context} 有无法映射的 grid_id，示例: {unresolved[:10]}")

    resolved_mask = ~unresolved_mask
    old = result["condition_label"].map(normalize_condition_label)
    remapped = int((resolved_mask & (old != expected)).sum())
    resolved_grids = grid_text[resolved_mask]
    result.loc[resolved_mask, "condition_label"] = expected[resolved_mask]
    result.loc[resolved_mask, "condition_snapshot_version"] = index.snapshot_version
    for column in ("policy_region_id", "region_status", "region_member_count", "base_condition_id"):
        result.loc[resolved_mask, column] = resolved_grids.map(mapping[column]).to_numpy()
    return result, {
        "row_count": int(len(result)),
        "remapped_row_count": remapped,
        "unresolved_row_count": int(unresolved_mask.sum()),
        "unresolved_examples": unresolved[:20],
    }


def validate_episode_current_mapping(
    frame: pd.DataFrame,
    index: ConditionSnapshotIndex,
    *,
    strict: bool,
    dataset_name: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Validate final episode alignment without rewriting the full DataFrame again."""

    if frame.empty:
        return {
            "dataset": dataset_name,
            "episode_count": 0,
            "resolved_episode_count": 0,
            "unresolved_episode_count": 0,
            "remapped_episode_count": 0,
            "unchanged_episode_count": 0,
            "condition_changes": [],
        }, pd.DataFrame()
    if "anchor_grid_id" not in frame.columns:
        raise SnapshotError(f"{dataset_name} 缺少 anchor_grid_id，无法校验最终映射")

    grid_text = frame["anchor_grid_id"].astype(str).str.strip()
    expected_label = grid_text.map(
        {key: value.condition_label for key, value in index.grid_records.items()}
    )
    expected_region = grid_text.map(
        {key: value.policy_region_id for key, value in index.grid_records.items()}
    )
    unresolved_mask = expected_label.isna()
    actual_label = frame.get(
        "condition_label", pd.Series("UNKNOWN", index=frame.index)
    ).map(normalize_condition_label)
    actual_version = frame.get(
        "condition_snapshot_version", pd.Series("", index=frame.index)
    ).fillna("").astype(str).str.strip()
    actual_region = frame.get(
        "policy_region_id", pd.Series("", index=frame.index)
    ).fillna("").astype(str).str.strip()
    mismatch_mask = (~unresolved_mask) & (
        (actual_label != expected_label)
        | (actual_version != index.snapshot_version)
        | (actual_region != expected_region)
    )

    problem_mask = unresolved_mask | mismatch_mask
    problems = pd.DataFrame(
        {
            "dataset": dataset_name,
            "row_index": [
                int(value) if isinstance(value, int) else str(value)
                for value in frame.index[problem_mask]
            ],
            "episode_id": (
                frame.loc[problem_mask, "episode_id"].tolist()
                if "episode_id" in frame.columns
                else [None] * int(problem_mask.sum())
            ),
            "anchor_grid_id": grid_text[problem_mask].tolist(),
            "actual_condition_label": actual_label[problem_mask].tolist(),
            "expected_condition_label": expected_label[problem_mask].tolist(),
            "actual_condition_snapshot_version": actual_version[problem_mask].tolist(),
            "expected_condition_snapshot_version": index.snapshot_version,
            "reason": [
                "ANCHOR_GRID_NOT_IN_CURRENT_CONDITION_SNAPSHOT"
                if unresolved_mask.at[row_index]
                else "FINAL_MAPPING_FIELD_MISMATCH"
                for row_index in frame.index[problem_mask]
            ],
        }
    )
    if strict and not problems.empty:
        raise SnapshotError(
            f"{dataset_name} 最终映射校验失败，共 {len(problems)} 条，示例: "
            f"{problems.head(10).to_dict(orient='records')}"
        )
    resolved_count = int((~problem_mask).sum())
    return {
        "dataset": dataset_name,
        "episode_count": int(len(frame)),
        "resolved_episode_count": resolved_count,
        "unresolved_episode_count": int(problem_mask.sum()),
        "remapped_episode_count": 0,
        "unchanged_episode_count": resolved_count,
        "condition_changes": [],
    }, problems
