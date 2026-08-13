from __future__ import annotations

import os
import pickle
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .exceptions import SnapshotError
from .performance import PerformanceRecorder
from .reliability import profile_status
from .schema import episode_output_columns, time_column
from .utils import (
    normalize_condition_label,
    read_json,
    safe_name,
    sha256_file,
    utc_now_iso,
    write_json,
)


# V1.8B 保持 V1.7 策略含义，增加等价性能优化和 pickle 内部事实源。
EFFECTIVE_CONFIG_SCHEMA_VERSION = "1.4"
SNAPSHOT_SCHEMA_VERSION = "1.5"
REQUIRED_INCREMENTAL_FILES = (
    "effective_config.json",
    "manifest.json",
)
DATASET_ALTERNATIVES = {
    "valid_episode": (
        "datasets/valid_decision_episodes.pkl",
        "datasets/valid_decision_episodes.csv",
    ),
    "invalid_episode": (
        "datasets/invalid_decision_episodes.pkl",
        "datasets/invalid_decision_episodes.csv",
    ),
    "context_tail": (
        "datasets/context_tail.pkl",
        "datasets/context_tail.csv",
    ),
}


def _version_number(name: str) -> int:
    text = str(name).strip()
    if text.lower().startswith("v") and text[1:].isdigit():
        return int(text[1:])
    return -1


def next_snapshot_version(output_root: str | Path, prefix: str = "v") -> str:
    """兼容工具；正式 V1.8B 由第一模块版本决定，不应自行递增。"""
    snapshots = Path(output_root) / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    numbers = [_version_number(child.name) for child in snapshots.iterdir() if child.is_dir()]
    return f"v{(max(numbers, default=0) + 1):03d}"


def latest_snapshot_path(output_root: str | Path) -> Path:
    """按与第一模块一致的 v### 目录自动读取最新第二模块版本。"""
    output = Path(output_root)
    snapshots_dir = output / "snapshots"
    candidates = sorted(
        [item for item in snapshots_dir.glob("v*") if item.is_dir() and _version_number(item.name) >= 0],
        key=lambda item: _version_number(item.name),
        reverse=True,
    )
    for path in candidates:
        try:
            _validate_previous_snapshot(path)
            return path
        except Exception:
            continue
    # 兼容 V1.6 的 latest.json，仅用于迁移提示。
    latest = output / "latest.json"
    if latest.exists():
        data = read_json(latest)
        path = Path(data.get("snapshot_path", ""))
        if path.exists():
            return path
    raise SnapshotError(
        "未找到第二模块历史策略版本。第一次增量训练前必须先执行初次训练。"
    )


def cleanup_old_policy_versions(output_root: str | Path, max_versions_to_keep: int) -> None:
    if max_versions_to_keep <= 0:
        return
    snapshots_dir = Path(output_root) / "snapshots"
    versions = sorted(
        [item for item in snapshots_dir.glob("v*") if item.is_dir() and _version_number(item.name) >= 0],
        key=lambda item: _version_number(item.name),
    )
    for old in versions[:-max_versions_to_keep]:
        shutil.rmtree(old)


def write_pickle(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def _owner_info(
    owner_id: str,
    states: dict[str, Any],
    level: str,
    snapshot_version: str,
    condition_snapshot_version: str | None,
) -> dict[str, Any]:
    profiles = [
        profile for actions in states.values() for profile in actions.values()
    ]
    action_profile_count = len(profiles)
    supported_count = sum(
        1 for profile in profiles if profile.get("profile_status") == "SUPPORTED"
    )
    low_count = sum(
        1 for profile in profiles if profile.get("profile_status") == "LOW_SUPPORT"
    )
    if action_profile_count == 0:
        owner_status = "NO_DATA"
    elif supported_count == 0:
        owner_status = "LOW_SUPPORT_ONLY"
    elif supported_count == action_profile_count:
        owner_status = "SUPPORTED"
    else:
        owner_status = "PARTIALLY_SUPPORTED"
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "policy_snapshot_version": snapshot_version,
        "condition_snapshot_version": condition_snapshot_version,
        "level": level,
        "owner_id": owner_id,
        "state_count": len(states),
        "action_profile_count": action_profile_count,
        "supported_action_profile_count": supported_count,
        "low_support_action_profile_count": low_count,
        "profile_status": owner_status,
        "states": states,
    }


def _write_owner_collection(
    root: Path,
    level: str,
    collection: dict[str, dict[str, Any]],
    snapshot_version: str,
    condition_snapshot_version: str | None,
    write_pickle_only_when_profiles_exist: bool,
    progress: Callable[[float, str], None] | None = None,
) -> None:
    """写入 transients 等非 condition_label 层级。"""
    items = list(collection.items())
    if progress and not items:
        progress(1.0, f"{level} 没有可写入的策略对象")
    for item_index, (owner_id, states) in enumerate(items, start=1):
        if progress:
            progress(
                (item_index - 1) / max(len(items), 1),
                f"写入 {level} {item_index}/{len(items)}：{owner_id}",
            )
        owner_dir = root / level / safe_name(owner_id)
        info = _owner_info(
            owner_id,
            states,
            level,
            snapshot_version,
            condition_snapshot_version,
        )
        write_json(owner_dir / "policy_info.json", info)
        if states or not write_pickle_only_when_profiles_exist:
            write_pickle(
                owner_dir / "policy.pkl",
                {
                    "schema_version": SNAPSHOT_SCHEMA_VERSION,
                    "policy_snapshot_version": snapshot_version,
                    "condition_snapshot_version": condition_snapshot_version,
                    "level": level,
                    "owner_id": owner_id,
                    "state_action_profiles": states,
                },
            )
    if progress and items:
        progress(1.0, f"{level} 写入完成，共 {len(items)} 个对象")


def _normalize_episode_condition_fields(frame: pd.DataFrame) -> pd.DataFrame:
    """统一 V1.8B 决策片段中的锚点、路径、路由和证据字段。"""
    result = frame.copy()
    if "condition_label" not in result.columns:
        result["condition_label"] = "UNKNOWN"
    result["condition_label"] = result["condition_label"].map(normalize_condition_label)

    if "anchor_condition_label" not in result.columns:
        result["anchor_condition_label"] = result["condition_label"]
    else:
        result["anchor_condition_label"] = result["anchor_condition_label"].map(
            normalize_condition_label
        )
    result["condition_label"] = result["anchor_condition_label"]

    if "start_condition_label" not in result.columns:
        result["start_condition_label"] = result["condition_label"]
    else:
        result["start_condition_label"] = result["start_condition_label"].map(
            normalize_condition_label
        )
    if "end_condition_label" not in result.columns:
        result["end_condition_label"] = result["start_condition_label"]
    else:
        result["end_condition_label"] = result["end_condition_label"].map(
            normalize_condition_label
        )
    if "condition_label_path" not in result.columns:
        result["condition_label_path"] = result["start_condition_label"]
    if "condition_label_change_count" not in result.columns:
        result["condition_label_change_count"] = 0
    result["condition_label_change_count"] = pd.to_numeric(
        result["condition_label_change_count"], errors="coerce"
    ).fillna(0).astype(int)

    if "anchor_grid_id" not in result.columns:
        result["anchor_grid_id"] = result.get("start_grid_id", "UNKNOWN")
    if "training_route" not in result.columns:
        transient = result.get("is_transient", pd.Series(False, index=result.index))
        result["training_route"] = transient.fillna(False).astype(bool).map(
            {True: "TRANSIENT", False: "LOCAL_REGULAR"}
        )
    if "attribution_source" not in result.columns:
        result["attribution_source"] = "EXACT_LOCAL"
    if "evidence_weight" not in result.columns:
        result["evidence_weight"] = 1.0
    result["evidence_weight"] = pd.to_numeric(
        result["evidence_weight"], errors="coerce"
    ).fillna(1.0)
    return result


def _stable_episode_frame(frame: pd.DataFrame, plant: dict[str, Any]) -> pd.DataFrame:
    """保证零事件时 CSV 也有稳定表头，并迁移旧版工况字段。"""
    result = _normalize_episode_condition_fields(frame)
    expected = episode_output_columns(plant)
    for column in expected:
        if column not in result.columns:
            result[column] = pd.Series(dtype="object")
    extra = [column for column in result.columns if column not in expected]
    return result[expected + extra]


def _condition_versions(*frames: pd.DataFrame) -> list[str]:
    values: set[str] = set()
    for frame in frames:
        if "condition_snapshot_version" not in frame.columns:
            continue
        values.update(
            frame["condition_snapshot_version"].dropna().astype(str).tolist()
        )
    return sorted(values)


def _unique_text(frame: pd.DataFrame, column: str) -> list[str]:
    if frame.empty or column not in frame.columns:
        return []
    values = {
        str(value).strip()
        for value in frame[column].dropna().tolist()
        if str(value).strip() and str(value).strip().lower() not in {"nan", "none"}
    }
    return sorted(values)


def _observed_min_max(frame: pd.DataFrame, columns: list[str]) -> tuple[float | None, float | None]:
    values: list[pd.Series] = []
    for column in columns:
        if column in frame.columns:
            values.append(pd.to_numeric(frame[column], errors="coerce"))
    if not values:
        return None, None
    combined = pd.concat(values, ignore_index=True).dropna()
    if combined.empty:
        return None, None
    return float(combined.min()), float(combined.max())


def _condition_subset(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    labels = frame["condition_label"].map(normalize_condition_label)
    return frame[labels == label].copy()


def _support_summary(valid: pd.DataFrame, invalid: pd.DataFrame) -> dict[str, Any]:
    routes = (
        valid.get("training_route", pd.Series("LOCAL_REGULAR", index=valid.index))
        if not valid.empty else pd.Series(dtype="object")
    )
    sources = (
        valid.get("attribution_source", pd.Series("UNKNOWN", index=valid.index))
        if not valid.empty else pd.Series(dtype="object")
    )
    weights = pd.to_numeric(
        valid.get("evidence_weight", pd.Series(1.0, index=valid.index)),
        errors="coerce",
    ).fillna(0.0) if not valid.empty else pd.Series(dtype=float)
    local_mask = routes == "LOCAL_REGULAR"
    local = valid[local_mask] if not valid.empty else valid
    local_weights = weights[local_mask] if not valid.empty else weights
    return {
        "valid_episode_count": int(len(valid)),
        "invalid_episode_count": int(len(invalid)),
        "local_regular_episode_count": int((routes == "LOCAL_REGULAR").sum()),
        "global_only_episode_count": int((routes == "GLOBAL_ONLY").sum()),
        "transient_valid_episode_count": int((routes == "TRANSIENT").sum()),
        # 兼容旧字段：regular 现在只表示可进入本地工况的 LOCAL_REGULAR。
        "regular_valid_episode_count": int((routes == "LOCAL_REGULAR").sum()),
        "exact_local_episode_count": int((sources == "EXACT_LOCAL").sum()),
        "nearby_accepted_episode_count": int((sources == "NEARBY_ACCEPTED").sum()),
        "effective_weighted_episode_count": float(local_weights.sum()),
        "all_valid_effective_weighted_episode_count": float(weights.sum()),
        "action_episode_count": int((valid.get("episode_type") == "ACTION").sum()) if not valid.empty else 0,
        "hold_episode_count": int((valid.get("episode_type") == "HOLD").sum()) if not valid.empty else 0,
        "local_action_episode_count": int((local.get("episode_type") == "ACTION").sum()) if not local.empty else 0,
        "local_hold_episode_count": int((local.get("episode_type") == "HOLD").sum()) if not local.empty else 0,
        "independent_segment_count": int(valid["continuous_segment_id"].nunique()) if not valid.empty and "continuous_segment_id" in valid.columns else 0,
        "independent_day_count": int(valid["event_date"].nunique()) if not valid.empty and "event_date" in valid.columns else 0,
        "invalid_reason_counts": invalid["invalid_reason"].value_counts().to_dict() if not invalid.empty and "invalid_reason" in invalid.columns else {},
    }



def _concat_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    non_empty = [frame for frame in frames if not frame.empty]
    if non_empty:
        return pd.concat(non_empty, ignore_index=True, sort=False)
    for frame in frames:
        if len(frame.columns):
            return frame.iloc[0:0].copy()
    return pd.DataFrame()

def _condition_catalog_record(
    label: str,
    valid: pd.DataFrame,
    invalid: pd.DataFrame,
    states: dict[str, Any],
    member_grid_states: dict[str, dict[str, Any]],
    neighbor_states: dict[str, Any],
    reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    all_events = _concat_frames(valid, invalid)
    load_min, load_max = _observed_min_max(all_events, ["before_load", "after_load"])
    inlet_min, inlet_max = _observed_min_max(
        all_events, ["before_inlet_so2", "after_inlet_so2"]
    )
    info = _owner_info(label, states, "CONDITION_LABEL", "", None)
    neighbor_info = _owner_info(label, neighbor_states, "NEIGHBOR_STATE", "", None)
    reference = reference or {}
    member_grids = sorted(
        {
            str(value)
            for value in all_events.get("anchor_grid_id", pd.Series(dtype="object"))
            .dropna()
            .tolist()
            if str(value).strip()
        }
        | set(member_grid_states.keys())
        | set(reference.get("member_grid_ids", []))
    )
    return {
        "condition_label": label,
        "condition_directory": f"conditions/condition_label_{safe_name(label)}",
        "condition_snapshot_versions": (
            [reference["condition_snapshot_version"]]
            if reference.get("condition_snapshot_version")
            else _unique_text(all_events, "condition_snapshot_version")
        ),
        "base_condition_ids": sorted(set(reference.get("base_condition_ids", [])) | set(_unique_text(all_events, "base_condition_id"))),
        "policy_region_ids": sorted(set(reference.get("policy_region_ids", [])) | set(_unique_text(all_events, "policy_region_id"))),
        "region_statuses": sorted(set(reference.get("region_statuses", [])) | set(_unique_text(all_events, "region_status"))),
        "member_grid_count": len(member_grids),
        "member_grid_ids": member_grids,
        "historical_source_condition_labels": _unique_text(all_events, "original_condition_label"),
        "remapped_episode_count": int(
            all_events.get("condition_remapped", pd.Series(False, index=all_events.index))
            .astype(str).str.lower().isin(["true", "1"]).sum()
        ) if not all_events.empty else 0,
        "observed_load_min": load_min,
        "observed_load_max": load_max,
        "observed_inlet_so2_min": inlet_min,
        "observed_inlet_so2_max": inlet_max,
        **_support_summary(valid, invalid),
        "state_count": info["state_count"],
        "action_profile_count": info["action_profile_count"],
        "supported_action_profile_count": info["supported_action_profile_count"],
        "low_support_action_profile_count": info["low_support_action_profile_count"],
        "profile_status": info["profile_status"],
        "neighbor_state_count": neighbor_info["state_count"],
        "neighbor_action_profile_count": neighbor_info["action_profile_count"],
        "neighbor_profile_status": neighbor_info["profile_status"],
    }


def _member_grid_records(
    label: str,
    valid: pd.DataFrame,
    invalid: pd.DataFrame,
    grid_states: dict[str, dict[str, Any]],
    reference: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    all_events = _concat_frames(valid, invalid)
    reference = reference or {}
    grid_ids = sorted(
        {
            str(value)
            for value in all_events.get("anchor_grid_id", pd.Series(dtype="object"))
            .dropna()
            .tolist()
            if str(value).strip()
        }
        | set(grid_states.keys())
        | set(reference.get("member_grid_ids", []))
    )
    records: list[dict[str, Any]] = []
    for grid_id in grid_ids:
        valid_grid = valid[valid.get("anchor_grid_id") == grid_id].copy()
        invalid_grid = invalid[invalid.get("anchor_grid_id") == grid_id].copy()
        all_grid = _concat_frames(valid_grid, invalid_grid)
        load_min, load_max = _observed_min_max(all_grid, ["before_load", "after_load"])
        inlet_min, inlet_max = _observed_min_max(
            all_grid, ["before_inlet_so2", "after_inlet_so2"]
        )
        states = grid_states.get(grid_id, {})
        info = _owner_info(grid_id, states, "GRID_LOCAL", "", None)
        grid_ref = (reference.get("grid_records", {}) or {}).get(grid_id, {})
        records.append(
            {
                "condition_label": label,
                "grid_id": grid_id,
                "base_condition_id": grid_ref.get("base_condition_id"),
                "policy_region_ids": grid_ref.get("policy_region_id") or ";".join(_unique_text(all_grid, "policy_region_id")),
                "region_statuses": grid_ref.get("region_status") or ";".join(_unique_text(all_grid, "region_status")),
                "condition_snapshot_versions": reference.get("condition_snapshot_version") or ";".join(
                    _unique_text(all_grid, "condition_snapshot_version")
                ),
                "observed_load_min": load_min,
                "observed_load_max": load_max,
                "observed_inlet_so2_min": inlet_min,
                "observed_inlet_so2_max": inlet_max,
                **_support_summary(valid_grid, invalid_grid),
                "state_count": info["state_count"],
                "action_profile_count": info["action_profile_count"],
                "supported_action_profile_count": info["supported_action_profile_count"],
                "low_support_action_profile_count": info["low_support_action_profile_count"],
                "profile_status": info["profile_status"],
            }
        )
    return records


def _write_condition_collection(
    snapshot_dir: Path,
    snapshot_version: str,
    condition_version: str | None,
    valid_episodes: pd.DataFrame,
    invalid_episodes: pd.DataFrame,
    aggregated: dict[str, Any],
    write_pickle_only_when_profiles_exist: bool,
    condition_reference: dict[str, dict[str, Any]] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> list[dict[str, Any]]:
    conditions = aggregated.get("conditions", {})
    condition_grids = aggregated.get("condition_grids", {})
    neighbor_state = aggregated.get("neighbor_state", {})
    condition_reference = condition_reference or {}
    labels = {
        *condition_reference.keys(),
        *conditions.keys(),
        *condition_grids.keys(),
        *neighbor_state.keys(),
        *valid_episodes["condition_label"].map(normalize_condition_label).tolist(),
        *invalid_episodes["condition_label"].map(normalize_condition_label).tolist(),
    }
    labels = sorted(labels, key=lambda value: (value == "UNKNOWN", value))
    catalog: list[dict[str, Any]] = []
    if progress and not labels:
        progress(1.0, "没有 condition_label 可写入")

    for index, label in enumerate(labels, start=1):
        if progress:
            progress(
                (index - 1) / max(len(labels), 1),
                f"写入 condition_label {index}/{len(labels)}：{label}",
            )
        states = conditions.get(label, {})
        grids = condition_grids.get(label, {})
        neighbor_states = neighbor_state.get(label, {})
        valid_subset = _condition_subset(valid_episodes, label)
        invalid_subset = _condition_subset(invalid_episodes, label)
        reference = condition_reference.get(label, {})
        catalog_record = _condition_catalog_record(
            label, valid_subset, invalid_subset, states, grids, neighbor_states, reference
        )
        catalog.append(catalog_record)

        condition_dir = (
            snapshot_dir / "conditions" / f"condition_label_{safe_name(label)}"
        )
        condition_dir.mkdir(parents=True, exist_ok=True)
        member_records = _member_grid_records(
            label, valid_subset, invalid_subset, grids, reference
        )
        pd.DataFrame(member_records).to_csv(
            condition_dir / "member_grids.csv",
            index=False,
            encoding="utf-8-sig",
        )

        condition_info = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "policy_snapshot_version": snapshot_version,
            "condition_identity": {
                "condition_label": label,
                "condition_snapshot_versions": catalog_record[
                    "condition_snapshot_versions"
                ],
                "base_condition_ids": catalog_record["base_condition_ids"],
                "policy_region_ids": catalog_record["policy_region_ids"],
                "region_statuses": catalog_record["region_statuses"],
                "member_grid_count": catalog_record["member_grid_count"],
                "member_grid_ids": catalog_record["member_grid_ids"],
                "historical_source_condition_labels": catalog_record[
                    "historical_source_condition_labels"
                ],
                "remapped_episode_count": catalog_record["remapped_episode_count"],
            },
            "observed_scope": {
                "note": "以下范围来自决策片段实际观测值，不替代第一模块理论网格边界。",
                "load_min": catalog_record["observed_load_min"],
                "load_max": catalog_record["observed_load_max"],
                "inlet_so2_min": catalog_record["observed_inlet_so2_min"],
                "inlet_so2_max": catalog_record["observed_inlet_so2_max"],
            },
            "training_support": {
                key: catalog_record[key]
                for key in (
                    "valid_episode_count",
                    "invalid_episode_count",
                    "local_regular_episode_count",
                    "global_only_episode_count",
                    "transient_valid_episode_count",
                    "exact_local_episode_count",
                    "nearby_accepted_episode_count",
                    "effective_weighted_episode_count",
                    "all_valid_effective_weighted_episode_count",
                    "action_episode_count",
                    "hold_episode_count",
                    "local_action_episode_count",
                    "local_hold_episode_count",
                    "independent_segment_count",
                    "independent_day_count",
                    "invalid_reason_counts",
                )
            },
            "state_count": catalog_record["state_count"],
            "action_profile_count": catalog_record["action_profile_count"],
            "supported_action_profile_count": catalog_record["supported_action_profile_count"],
            "low_support_action_profile_count": catalog_record["low_support_action_profile_count"],
            "profile_status": catalog_record["profile_status"],
            "neighbor_summary": {
                "state_count": catalog_record["neighbor_state_count"],
                "action_profile_count": catalog_record["neighbor_action_profile_count"],
                "profile_status": catalog_record["neighbor_profile_status"],
            },
            "states": states,
        }
        write_json(condition_dir / "condition_policy_info.json", condition_info)
        if states or not write_pickle_only_when_profiles_exist:
            write_pickle(
                condition_dir / "condition_policy.pkl",
                {
                    "schema_version": SNAPSHOT_SCHEMA_VERSION,
                    "policy_snapshot_version": snapshot_version,
                    "condition_snapshot_version": condition_version,
                    "level": "CONDITION_LABEL",
                    "condition_label": label,
                    "member_grid_ids": catalog_record["member_grid_ids"],
                    "policy_region_ids": catalog_record["policy_region_ids"],
                    "state_action_profiles": states,
                },
            )

        neighbor_info = _owner_info(
            label, neighbor_states, "NEIGHBOR_STATE", snapshot_version, condition_version
        )
        neighbor_info["usage_constraint"] = (
            "仅在当前工况本地策略不足时使用；来源基础格受 ±2/±3 空间半径约束"
        )
        write_json(condition_dir / "neighbor_state_policy_info.json", neighbor_info)
        if neighbor_states or not write_pickle_only_when_profiles_exist:
            write_pickle(
                condition_dir / "neighbor_state_policy.pkl",
                {
                    "schema_version": SNAPSHOT_SCHEMA_VERSION,
                    "policy_snapshot_version": snapshot_version,
                    "condition_snapshot_version": condition_version,
                    "level": "NEIGHBOR_STATE",
                    "condition_label": label,
                    "member_grid_ids": catalog_record["member_grid_ids"],
                    "state_action_profiles": neighbor_states,
                },
            )

        for member in member_records:
            grid_id = member["grid_id"]
            grid_states = grids.get(grid_id, {})
            grid_dir = condition_dir / "grids" / safe_name(grid_id)
            grid_info = {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "policy_snapshot_version": snapshot_version,
                "condition_snapshot_version": condition_version,
                "level": "GRID_LOCAL",
                "condition_label": label,
                "grid_id": grid_id,
                "identity_and_support": member,
                "states": grid_states,
            }
            write_json(grid_dir / "grid_policy_info.json", grid_info)
            if grid_states or not write_pickle_only_when_profiles_exist:
                write_pickle(
                    grid_dir / "grid_policy.pkl",
                    {
                        "schema_version": SNAPSHOT_SCHEMA_VERSION,
                        "policy_snapshot_version": snapshot_version,
                        "condition_snapshot_version": condition_version,
                        "level": "GRID_LOCAL",
                        "condition_label": label,
                        "grid_id": grid_id,
                        "state_action_profiles": grid_states,
                    },
                )

    if progress and labels:
        progress(1.0, f"condition_label 写入完成，共 {len(labels)} 个工况")
    return catalog


def write_snapshot(
    output_root: str | Path,
    snapshot_version: str,
    plant: dict[str, Any],
    training: dict[str, Any],
    effective_config: dict[str, Any],
    raw_df: pd.DataFrame,
    valid_episodes: pd.DataFrame,
    invalid_episodes: pd.DataFrame,
    aggregated: dict[str, Any],
    training_mode: str,
    previous_snapshot: str | None,
    warnings: list[str],
    source_paths: list[str],
    condition_index: Any,
    remap_report: dict[str, Any] | None = None,
    performance_recorder: PerformanceRecorder | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> Path:
    if progress:
        progress(0.01, f"创建策略快照 {snapshot_version}")
    output_root = Path(output_root)
    snapshot_dir = output_root / "snapshots" / snapshot_version
    if snapshot_dir.exists():
        raise SnapshotError(f"快照目录已存在: {snapshot_dir}")
    snapshot_dir.mkdir(parents=True)

    if progress:
        progress(0.05, "整理决策片段和 condition_label 输出表头")
    valid_episodes = _stable_episode_frame(valid_episodes, plant)
    invalid_episodes = _stable_episode_frame(invalid_episodes, plant)
    condition_versions = _condition_versions(valid_episodes, invalid_episodes)
    condition_version = str(condition_index.snapshot_version)
    if snapshot_version != condition_version:
        raise SnapshotError(
            f"第二模块版本 {snapshot_version} 与第一模块版本 {condition_version} 不一致"
        )

    condition_reference: dict[str, dict[str, Any]] = {}
    for label, grid_ids in condition_index.condition_members.items():
        records = {
            grid_id: {
                "base_condition_id": condition_index.grid_records[grid_id].base_condition_id,
                "policy_region_id": condition_index.grid_records[grid_id].policy_region_id,
                "region_status": condition_index.grid_records[grid_id].region_status,
            }
            for grid_id in grid_ids
        }
        condition_reference[str(label)] = {
            "condition_snapshot_version": condition_version,
            "member_grid_ids": list(grid_ids),
            "base_condition_ids": sorted({item["base_condition_id"] for item in records.values()}),
            "policy_region_ids": sorted({item["policy_region_id"] for item in records.values()}),
            "region_statuses": sorted({item["region_status"] for item in records.values()}),
            "grid_records": records,
        }

    effective_to_write = dict(effective_config)
    effective_to_write["condition_alignment"] = condition_index.to_metadata()
    effective_to_write["effective_config_schema_version"] = (
        EFFECTIVE_CONFIG_SCHEMA_VERSION
    )
    write_json(snapshot_dir / "effective_config.json", effective_to_write)
    write_json(snapshot_dir / "condition_alignment.json", condition_index.to_metadata())
    write_json(snapshot_dir / "condition_remap_report.json", remap_report or {})
    mapping_rows = []
    for grid_id, record in sorted(
        condition_index.grid_records.items(),
        key=lambda item: (item[1].load_level, item[1].inlet_so2_level),
    ):
        mapping_rows.append({
            "condition_snapshot_version": condition_version,
            "grid_id": grid_id,
            "condition_label": record.condition_label,
            "base_condition_id": record.base_condition_id,
            "policy_region_id": record.policy_region_id,
            "region_status": record.region_status,
            "region_member_count": record.region_member_count,
            "load_level": record.load_level,
            "inlet_so2_level": record.inlet_so2_level,
        })
    pd.DataFrame(mapping_rows).to_csv(
        snapshot_dir / "grid_condition_mapping.csv", index=False, encoding="utf-8-sig"
    )
    if progress:
        progress(0.11, "写入配置、版本握手和工况重映射报告")

    datasets = snapshot_dir / "datasets"
    datasets.mkdir(parents=True)
    output_cfg = training.get("output", {})
    write_episode_pickle_enabled = bool(output_cfg.get("write_episode_pickle", True))
    write_episode_csv_enabled = bool(output_cfg.get("write_full_episode_csv", True))
    write_tail_pickle_enabled = bool(output_cfg.get("write_context_tail_pickle", True))
    write_tail_csv_enabled = bool(output_cfg.get("write_context_tail_csv", True))

    recorder = performance_recorder or PerformanceRecorder(enabled=False)
    with recorder.measure("snapshot_write_episode_datasets"):
        if write_episode_pickle_enabled:
            write_pickle(datasets / "valid_decision_episodes.pkl", valid_episodes)
            write_pickle(datasets / "invalid_decision_episodes.pkl", invalid_episodes)
        if write_episode_csv_enabled:
            valid_episodes.to_csv(
                datasets / "valid_decision_episodes.csv",
                index=False,
                encoding="utf-8-sig",
            )
            invalid_episodes.to_csv(
                datasets / "invalid_decision_episodes.csv",
                index=False,
                encoding="utf-8-sig",
            )
    if progress:
        formats = "+".join(
            name for name, enabled in (("PKL", write_episode_pickle_enabled), ("CSV", write_episode_csv_enabled))
            if enabled
        )
        progress(0.22, f"写入决策片段 {formats}：VALID={len(valid_episodes)}，INVALID={len(invalid_episodes)}")

    ts_col = time_column(plant)
    if raw_df.empty:
        context_tail = raw_df.copy()
    else:
        tail_minutes = float(training["episode"]["incremental_context_tail_minutes"])
        cutoff = raw_df[ts_col].max() - pd.Timedelta(minutes=tail_minutes)
        context_tail = raw_df[raw_df[ts_col] >= cutoff].copy()
    with recorder.measure("snapshot_write_context_tail"):
        if write_tail_pickle_enabled:
            write_pickle(datasets / "context_tail.pkl", context_tail)
        if write_tail_csv_enabled:
            context_tail.to_csv(
                datasets / "context_tail.csv", index=False, encoding="utf-8-sig"
            )
    if progress:
        progress(0.27, f"写入增量边界上下文，共 {len(context_tail)} 行")

    write_pickle_only = bool(
        training["output"]["write_pickle_only_when_profiles_exist"]
    )
    condition_progress = (
        (lambda value, message: progress(0.27 + 0.30 * value, message))
        if progress
        else None
    )
    with recorder.measure("snapshot_write_condition_policies"):
        catalog = _write_condition_collection(
            snapshot_dir,
            snapshot_version,
            condition_version,
            valid_episodes,
            invalid_episodes,
            aggregated,
            write_pickle_only,
            condition_reference,
            condition_progress,
        )
    catalog_columns = [
        "condition_label",
        "condition_directory",
        "condition_snapshot_versions",
        "base_condition_ids",
        "policy_region_ids",
        "region_statuses",
        "member_grid_count",
        "member_grid_ids",
        "historical_source_condition_labels",
        "remapped_episode_count",
        "observed_load_min",
        "observed_load_max",
        "observed_inlet_so2_min",
        "observed_inlet_so2_max",
        "valid_episode_count",
        "invalid_episode_count",
        "local_regular_episode_count",
        "global_only_episode_count",
        "transient_valid_episode_count",
        "exact_local_episode_count",
        "nearby_accepted_episode_count",
        "effective_weighted_episode_count",
        "all_valid_effective_weighted_episode_count",
        "action_episode_count",
        "hold_episode_count",
        "local_action_episode_count",
        "local_hold_episode_count",
        "independent_segment_count",
        "independent_day_count",
        "invalid_reason_counts",
        "state_count",
        "action_profile_count",
        "supported_action_profile_count",
        "low_support_action_profile_count",
        "profile_status",
        "neighbor_state_count",
        "neighbor_action_profile_count",
        "neighbor_profile_status",
    ]
    catalog_frame = pd.DataFrame(catalog, columns=catalog_columns)
    catalog_csv_frame = catalog_frame.copy()
    for list_column in (
        "condition_snapshot_versions",
        "base_condition_ids",
        "policy_region_ids",
        "region_statuses",
        "member_grid_ids",
    ):
        if list_column in catalog_csv_frame.columns:
            catalog_csv_frame[list_column] = catalog_csv_frame[list_column].map(
                lambda value: ";".join(map(str, value))
                if isinstance(value, list)
                else value
            )
    catalog_csv_frame.to_csv(
        snapshot_dir / "condition_catalog.csv",
        index=False,
        encoding="utf-8-sig",
    )
    write_json(
        snapshot_dir / "condition_catalog.json",
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "policy_snapshot_version": snapshot_version,
            "condition_count": len(catalog),
            "conditions": catalog,
        },
    )
    write_json(
        snapshot_dir / "condition_index.json",
        {
            record["condition_label"]: record["condition_directory"]
            for record in catalog
        },
    )
    # 与总目录相同的按工况事件摘要放到 datasets，便于 Excel 审查。
    summary_columns = [
        "condition_label",
        "member_grid_count",
        "member_grid_ids",
        "valid_episode_count",
        "invalid_episode_count",
        "action_episode_count",
        "hold_episode_count",
        "local_regular_episode_count",
        "global_only_episode_count",
        "transient_valid_episode_count",
        "exact_local_episode_count",
        "nearby_accepted_episode_count",
        "effective_weighted_episode_count",
        "state_count",
        "action_profile_count",
        "supported_action_profile_count",
        "low_support_action_profile_count",
        "profile_status",
        "neighbor_state_count",
        "neighbor_action_profile_count",
        "neighbor_profile_status",
    ]
    if catalog_frame.empty:
        pd.DataFrame(columns=summary_columns).to_csv(
            datasets / "condition_episode_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )
    else:
        summary_frame = catalog_frame.copy()
        if "member_grid_ids" in summary_frame:
            summary_frame["member_grid_ids"] = summary_frame["member_grid_ids"].map(
                lambda value: ";".join(value) if isinstance(value, list) else value
            )
        summary_frame[summary_columns].to_csv(
            datasets / "condition_episode_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )
    if progress:
        progress(0.61, "写入 condition_catalog 和工况事件摘要")

    global_dir = snapshot_dir / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    plant_prior = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "policy_snapshot_version": snapshot_version,
        "condition_snapshot_version": condition_version,
        "level": "PLANT_ACTION_PRIOR",
        "usage_constraint": (
            "仅提供全厂动作方向、稳定性、安全性和空间推广性先验；"
            "不得直接输出精确阀位增量"
        ),
        "state_action_profiles": aggregated.get("plant_action_prior", {}),
    }
    write_pickle(global_dir / "plant_action_prior.pkl", plant_prior)
    write_json(global_dir / "plant_action_prior_info.json", plant_prior)
    if progress:
        progress(0.68, "写入全厂动作方向与安全先验")

    transient_progress = (
        (lambda value, message: progress(0.68 + 0.10 * value, message))
        if progress
        else None
    )
    _write_owner_collection(
        snapshot_dir,
        "transients",
        aggregated["transients"],
        snapshot_version,
        condition_version,
        write_pickle_only,
        transient_progress,
    )
    _write_owner_collection(
        snapshot_dir,
        "transient_direction",
        aggregated.get("transient_direction", {}),
        snapshot_version,
        condition_version,
        write_pickle_only,
        None,
    )

    member_grid_count = sum(
        len(items) for items in aggregated.get("condition_grids", {}).values()
    )
    summary = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "policy_snapshot_version": snapshot_version,
        "training_mode": training_mode,
        "previous_snapshot": previous_snapshot,
        "condition_snapshot_version": condition_version,
        "condition_snapshot_sha256": condition_index.snapshot_sha256,
        "grid_condition_mapping_sha256": condition_index.mapping_sha256,
        "created_at": utc_now_iso(),
        "source_paths": source_paths,
        "time_column": ts_col,
        "raw_row_count": len(raw_df),
        "valid_episode_count": len(valid_episodes),
        "invalid_episode_count": len(invalid_episodes),
        "action_episode_count": int(
            (valid_episodes["episode_type"] == "ACTION").sum()
        ),
        "hold_episode_count": int(
            (valid_episodes["episode_type"] == "HOLD").sum()
        ),
        "local_regular_episode_count": int(
            (valid_episodes["training_route"] == "LOCAL_REGULAR").sum()
        ),
        "global_only_episode_count": int(
            (valid_episodes["training_route"] == "GLOBAL_ONLY").sum()
        ),
        "transient_episode_count": int(
            (valid_episodes["training_route"] == "TRANSIENT").sum()
        ),
        "exact_local_episode_count": int(
            (valid_episodes["attribution_source"] == "EXACT_LOCAL").sum()
        ),
        "nearby_accepted_episode_count": int(
            (valid_episodes["attribution_source"] == "NEARBY_ACCEPTED").sum()
        ),
        "condition_label_count": len(catalog),
        "condition_label_count_with_profiles": len(aggregated["conditions"]),
        "member_grid_count_with_profiles": member_grid_count,
        "neighbor_condition_count_with_profiles": len(aggregated.get("neighbor_state", {})),
        "plant_action_prior_state_count": len(aggregated.get("plant_action_prior", {})),
        # 兼容旧审计字段名称。
        "grid_count_with_profiles": member_grid_count,
        "region_count_with_profiles": len(aggregated["conditions"]),
        "condition_snapshot_versions_in_episode_files": condition_versions,
        "condition_snapshot_versions": [condition_version],
        "remap_report": remap_report or {},
        "warnings": warnings,
        "dataset_formats": {
            "episode_pickle": write_episode_pickle_enabled,
            "episode_csv": write_episode_csv_enabled,
            "context_tail_pickle": write_tail_pickle_enabled,
            "context_tail_csv": write_tail_csv_enabled,
        },
        "last_data_timestamp": raw_df[ts_col].max() if not raw_df.empty else None,
    }
    write_json(snapshot_dir / "training_summary.json", summary)
    if progress:
        progress(0.82, "写入训练摘要")

    files = [p for p in snapshot_dir.rglob("*") if p.is_file()]
    manifest = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "policy_snapshot_version": snapshot_version,
        "condition_snapshot_version": condition_version,
        "grid_condition_mapping_sha256": condition_index.mapping_sha256,
        "created_at": utc_now_iso(),
        "files": [],
    }
    sorted_files = sorted(files)
    with recorder.measure("snapshot_manifest_hashing"):
        for file_index, path in enumerate(sorted_files, start=1):
            manifest["files"].append(
                {
                    "path": str(path.relative_to(snapshot_dir)).replace("\\", "/"),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
            if progress:
                progress(
                    0.82 + 0.14 * file_index / max(len(sorted_files), 1),
                    f"计算文件校验哈希 {file_index}/{len(sorted_files)}",
                )
    performance_path = snapshot_dir / "performance_report.json"
    write_json(performance_path, recorder.report())
    manifest["files"].append(
        {
            "path": "performance_report.json",
            "size": performance_path.stat().st_size,
            "sha256": sha256_file(performance_path),
        }
    )
    write_json(snapshot_dir / "manifest.json", manifest)
    cleanup_old_policy_versions(
        output_root, int(training.get("output", {}).get("max_versions_to_keep", 5))
    )
    if progress:
        progress(1.0, f"版本发布完成：{snapshot_dir}")
    return snapshot_dir


def _resolve_existing_dataset_file(
    snapshot: Path,
    alternatives: tuple[str, ...],
    *,
    prefer_pickle: bool = True,
) -> Path:
    ordered = list(alternatives)
    if not prefer_pickle:
        ordered.sort(key=lambda value: value.endswith(".pkl"))
    for relative in ordered:
        candidate = snapshot / relative
        if candidate.exists():
            return candidate
    raise SnapshotError(
        "上一版快照缺少可用数据文件，候选为: " + ", ".join(alternatives)
    )


def _validate_manifest_file(
    snapshot: Path,
    listed: dict[str, Any],
    path: Path,
) -> None:
    relative = str(path.relative_to(snapshot)).replace("\\", "/")
    item = listed.get(relative)
    if not item:
        raise SnapshotError(f"manifest.json 未登记必要文件: {relative}")
    if int(item.get("size", -1)) != path.stat().st_size:
        raise SnapshotError(f"上一版文件大小校验失败: {relative}")
    if str(item.get("sha256")) != sha256_file(path):
        raise SnapshotError(f"上一版文件哈希校验失败: {relative}")


def _validate_previous_snapshot(snapshot: Path) -> None:
    if not snapshot.exists() or not snapshot.is_dir():
        raise SnapshotError(f"上一版快照目录不存在: {snapshot}")
    missing = [
        relative
        for relative in REQUIRED_INCREMENTAL_FILES
        if not (snapshot / relative).exists()
    ]
    if missing:
        raise SnapshotError(
            "上一版快照不完整，不能执行增量训练。缺少: "
            + ", ".join(missing)
            + "。请恢复上一版文件，或重新执行初次训练。"
        )

    selected_dataset_files = [
        _resolve_existing_dataset_file(snapshot, alternatives)
        for alternatives in DATASET_ALTERNATIVES.values()
    ]
    manifest = read_json(snapshot / "manifest.json")
    listed = {
        str(item.get("path", "")).replace("\\", "/"): item
        for item in manifest.get("files", [])
    }
    _validate_manifest_file(snapshot, listed, snapshot / "effective_config.json")
    for path in selected_dataset_files:
        _validate_manifest_file(snapshot, listed, path)


def _coerce_episode_types(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for col in ["action_start_time", "action_end_time", "response_end_time"]:
        if col in result:
            result[col] = pd.to_datetime(result[col], errors="coerce")
    if "valid" in result:
        if result["valid"].dtype != bool:
            result["valid"] = result["valid"].astype(str).str.lower().isin(["true", "1"])
    bool_columns = [
        c
        for c in result.columns
        if c.startswith("ph_")
        or c
        in {
            "outlet_so2_out_of_range",
            "outlet_so2_over_hard_max",
            "is_transient",
            "stable_response",
            "oscillation_detected",
            "short_reverse_action",
            "followup_action_in_response",
            "condition_valid",
            "supply_pump_state_changed",
            "condition_remapped",
        }
    ]
    for bool_col in bool_columns:
        if result[bool_col].dtype != bool:
            result[bool_col] = result[bool_col].astype(str).str.lower().isin(
                ["true", "1"]
            )
    return _normalize_episode_condition_fields(result)


def _read_episode_csv(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError as exc:
        raise SnapshotError(f"决策片段文件为空且没有表头: {path}") from exc
    return _coerce_episode_types(frame)


def _read_episode_pickle(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_pickle(path)
    except Exception as exc:
        raise SnapshotError(f"无法读取决策片段 pickle: {path}: {exc}") from exc
    if not isinstance(frame, pd.DataFrame):
        raise SnapshotError(f"决策片段 pickle 不是 DataFrame: {path}")
    return _coerce_episode_types(frame)


def _read_episode_file(path: Path) -> pd.DataFrame:
    return _read_episode_pickle(path) if path.suffix.lower() == ".pkl" else _read_episode_csv(path)


def _read_context_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".pkl":
        frame = pd.read_pickle(path)
        if not isinstance(frame, pd.DataFrame):
            raise SnapshotError(f"context_tail pickle 不是 DataFrame: {path}")
        return frame
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def load_previous_episodes(
    snapshot_dir: str | Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    snapshot = Path(snapshot_dir)
    _validate_previous_snapshot(snapshot)
    effective = read_json(snapshot / "effective_config.json")
    output_cfg = effective.get("training", {}).get("output", {})
    prefer_pickle = bool(
        output_cfg.get("prefer_episode_pickle_for_incremental_read", True)
    )
    valid_path = _resolve_existing_dataset_file(
        snapshot, DATASET_ALTERNATIVES["valid_episode"], prefer_pickle=prefer_pickle
    )
    invalid_path = _resolve_existing_dataset_file(
        snapshot, DATASET_ALTERNATIVES["invalid_episode"], prefer_pickle=prefer_pickle
    )
    tail_path = _resolve_existing_dataset_file(
        snapshot, DATASET_ALTERNATIVES["context_tail"], prefer_pickle=prefer_pickle
    )
    valid = _read_episode_file(valid_path)
    invalid = _read_episode_file(invalid_path)
    tail = _read_context_file(tail_path)
    schema_version = effective.get("effective_config_schema_version")
    if schema_version not in {"1.2", "1.3", EFFECTIVE_CONFIG_SCHEMA_VERSION}:
        raise SnapshotError(
            "上一版 effective_config.json 结构版本不兼容。"
            f"当前支持 1.2、1.3 或 {EFFECTIVE_CONFIG_SCHEMA_VERSION}，上一版为 {schema_version!r}。"
            "请重新执行初次训练。"
        )
    return valid, invalid, tail, effective

