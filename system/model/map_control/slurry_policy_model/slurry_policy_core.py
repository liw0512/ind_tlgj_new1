"""供浆历史动作响应模型——离线训练核心。

公开职责：
1. 加载一个统一配置文件；
2. 执行初次训练；
3. 执行增量训练；
4. 维护版本化快照及上一版继承关系。

实际动作检测、HOLD 提取、响应统计、聚合及快照写入位于 _engine，
厂级部署时通常只修改 slurry_policy_config.py，并调用两个训练入口文件。
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

import pandas as pd

from _engine.aggregator import aggregate_all_levels
from _engine.config_loader import (
    deep_merge,
    enabled_towers,
    validate_plant_config,
    validate_training_config,
)
from _engine.data_loader import assign_continuous_segments
from _engine.exceptions import ConfigurationError, SnapshotError
from _engine.pipeline import prepare_raw_data, run_episode_pipeline
from _engine.progress import TrainingProgress
from _engine.performance import PerformanceRecorder
from _engine.schema import time_column
from _engine.signal_processing import add_clean_valve_columns
from _engine.snapshot_store import (
    latest_snapshot_path,
    load_previous_episodes,
    write_snapshot,
)
from _engine.condition_snapshot_bridge import (
    load_condition_snapshot_index,
    remap_episode_conditions,
    remap_raw_condition_rows,
    resolve_condition_snapshot_path,
    validate_input_frame_alignment,
    validate_episode_current_mapping,
    version_number,
)
from _engine.utils import read_json, strict_json_value


DEFAULT_CONFIG_MODULE = "slurry_policy_config"


def _load_module_from_spec(spec: str | None) -> ModuleType:
    """加载统一配置。

    spec 可为：
    - None：加载同目录 slurry_policy_config.py；
    - Python 文件路径；
    - 可导入模块名。
    """
    if not spec:
        return importlib.import_module(DEFAULT_CONFIG_MODULE)

    path = Path(spec)
    if path.exists():
        module_name = f"slurry_policy_external_config_{abs(hash(path.resolve()))}"
        module_spec = importlib.util.spec_from_file_location(module_name, path)
        if module_spec is None or module_spec.loader is None:
            raise ConfigurationError(f"无法加载配置文件: {path}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return module

    try:
        return importlib.import_module(spec)
    except Exception as exc:  # pragma: no cover
        raise ConfigurationError(f"无法加载配置模块 {spec}: {exc}") from exc


def load_config(config_spec: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """加载统一配置并与项目默认值递归合并。"""
    default_module = importlib.import_module(DEFAULT_CONFIG_MODULE)
    override_module = _load_module_from_spec(config_spec)

    if not hasattr(default_module, "PLANT_CONFIG") or not hasattr(
        default_module, "TRAINING_CONFIG"
    ):
        raise ConfigurationError("默认配置必须同时定义 PLANT_CONFIG 和 TRAINING_CONFIG")
    if not hasattr(override_module, "PLANT_CONFIG") or not hasattr(
        override_module, "TRAINING_CONFIG"
    ):
        raise ConfigurationError("统一配置文件必须同时定义 PLANT_CONFIG 和 TRAINING_CONFIG")

    plant = deep_merge(default_module.PLANT_CONFIG, override_module.PLANT_CONFIG)
    training = deep_merge(
        default_module.TRAINING_CONFIG, override_module.TRAINING_CONFIG
    )
    validate_plant_config(plant)
    validate_training_config(training)
    return plant, training


def inspect_config(config_spec: str | None = None) -> dict[str, Any]:
    """返回完成默认值合并和校验后的有效配置。"""
    plant, training = load_config(config_spec)
    return strict_json_value({"plant": plant, "training": training})


def _resolve_inputs(
    input_paths: Sequence[str] | str | None,
    plant: dict[str, Any],
    mode: str,
) -> Sequence[str] | str:
    if input_paths:
        return input_paths
    key = (
        "default_initial_input"
        if mode == "INITIAL"
        else "default_incremental_input"
    )
    value = plant["paths"].get(key)
    if not value:
        raise ConfigurationError(
            f"未传入 --input，且 PLANT_CONFIG.paths.{key} 未配置"
        )
    return value


def _source_paths(input_specs: Sequence[str] | str) -> list[str]:
    if isinstance(input_specs, (str, Path)):
        return [str(input_specs)]
    return [str(item) for item in input_specs]


def _plant_structure_signature(plant: dict[str, Any]) -> dict[str, Any]:
    """增量训练必须保持一致的厂级固定结构和事件定义。"""
    towers: list[dict[str, Any]] = []
    for tower in enabled_towers(plant):
        towers.append(
            {
                "tower_id": tower["tower_id"],
                "ph_column": tower["ph_column"],
                "ph_safe_range": [float(x) for x in tower["ph_safe_range"]],
                "ph_guard_band": float(tower.get("ph_guard_band", 0.0)),
                "valves": [
                    {
                        "valve_id": valve["valve_id"],
                        "column": valve["column"],
                        "min_opening": float(valve["min_opening"]),
                        "max_opening": float(valve["max_opening"]),
                        "action_threshold": float(valve["action_threshold"]),
                    }
                    for valve in tower["valves"]
                ],
            }
        )
    return {
        "time_column": time_column(plant),
        "outlet_so2_safe_range": [
            float(x) for x in plant["outlet_so2_safe_range"]
        ],
        "supply_pump_state_columns": [
            str(x) for x in (plant.get("supply_pump_state_columns", []) or [])
        ],
        "towers": towers,
    }


def _event_definition_signature(training: dict[str, Any]) -> dict[str, Any]:
    """旧 episode 无法自动重算的训练定义；发生变化时必须重新初次训练。"""
    return {
        "episode": copy.deepcopy(training.get("episode", {})),
        "disturbance": copy.deepcopy(training.get("disturbance", {})),
        "condition_attribution": copy.deepcopy(
            training.get("condition_attribution", {})
        ),
        "state": copy.deepcopy(training.get("state", {})),
        "action_magnitude": copy.deepcopy(training.get("action_magnitude", {})),
        "response": copy.deepcopy(training.get("response", {})),
        "validity": {
            "require_condition_valid": bool(
                training.get("validity", {}).get("require_condition_valid", True)
            ),
            "allow_out_of_range_clipped": bool(
                training.get("validity", {}).get("allow_out_of_range_clipped", True)
            ),
            "invalidate_supply_pump_state_change": bool(
                training.get("validity", {}).get(
                    "invalidate_supply_pump_state_change", True
                )
            ),
        },
    }


def _validate_incremental_compatibility(
    current_plant: dict[str, Any],
    current_training: dict[str, Any],
    previous_effective_config: dict[str, Any],
) -> None:
    previous_plant = previous_effective_config.get("plant")
    previous_training = previous_effective_config.get("training")
    if not previous_plant or not previous_training:
        raise SnapshotError("上一版 effective_config.json 缺少 plant/training 配置")
    current_signature = _plant_structure_signature(current_plant)
    previous_signature = _plant_structure_signature(previous_plant)
    if current_signature != previous_signature:
        current_text = json.dumps(current_signature, ensure_ascii=False, sort_keys=True)
        previous_text = json.dumps(previous_signature, ensure_ascii=False, sort_keys=True)
        raise ConfigurationError(
            "增量训练检测到厂级固定结构发生变化。时间列、SO2安全范围、"
            "供浆泵状态字段、塔数量、pH字段/安全范围、阀门数量/字段/开度范围/"
            "动作阈值变化时，应重新执行初次训练。\n"
            f"当前结构: {current_text}\n上一版结构: {previous_text}"
        )
    current_event = _event_definition_signature(current_training)
    previous_event = _event_definition_signature(previous_training)
    if current_event != previous_event:
        raise ConfigurationError(
            "增量训练检测到决策片段、扰动、工况归属、状态或响应定义发生变化。"
            "旧 episode 无法可靠重算，请用 V1.8B 重新执行初次训练。"
        )


def _combine_tail_and_new(
    tail: pd.DataFrame,
    new_df: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
) -> pd.DataFrame:
    """将上一批末尾上下文与新增数据拼接，重新划分连续段和去抖。"""
    if tail.empty:
        return new_df

    ts_col = time_column(plant)
    tail = tail.copy()
    tail[ts_col] = pd.to_datetime(tail[ts_col], errors="coerce")
    combined = pd.concat([tail, new_df], ignore_index=True, sort=False)
    combined = combined[combined[ts_col].notna()].copy()
    already_sorted = bool(combined[ts_col].is_monotonic_increasing)
    if not (
        bool(training.get("performance", {}).get("skip_sort_when_already_ordered", True))
        and already_sorted
    ):
        combined.sort_values(ts_col, inplace=True, kind="stable")
    if combined[ts_col].duplicated(keep=False).any():
        combined.drop_duplicates(subset=[ts_col], keep="last", inplace=True)

    # 上一版尾部可能带内部中间列，必须删掉后按新拼接数据重新计算。
    internal_cols = [c for c in combined.columns if c.startswith("__clean_valve__")]
    combined.drop(columns=internal_cols, inplace=True, errors="ignore")
    combined = assign_continuous_segments(combined, plant, training)
    combined = add_clean_valve_columns(combined, plant, training)
    return combined.reset_index(drop=True)



def _concat_episode_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    """合并旧、新事件，避免空表参与 concat 产生 dtype 警告。"""
    non_empty = [frame for frame in frames if not frame.empty]
    if non_empty:
        return pd.concat(non_empty, ignore_index=True, sort=False)
    for frame in frames:
        if len(frame.columns):
            return frame.iloc[0:0].copy()
    return pd.DataFrame()

def _resolve_condition_index(
    condition_snapshot: str | None,
    plant: dict[str, Any],
):
    path = resolve_condition_snapshot_path(
        condition_snapshot,
        plant.get("paths", {}).get("condition_snapshots_dir"),
    )
    return load_condition_snapshot_index(path)


def _policy_version_from_snapshot_dir(
    path: Path,
    previous_effective: dict[str, Any] | None = None,
    old_valid: pd.DataFrame | None = None,
) -> str:
    version = path.name
    try:
        version_number(version)
        return version
    except Exception:
        pass
    alignment = (previous_effective or {}).get("condition_alignment", {})
    candidate = alignment.get("condition_snapshot_version")
    if candidate:
        version_number(str(candidate))
        return str(candidate)
    if old_valid is not None and "condition_snapshot_version" in old_valid.columns:
        values = sorted({str(v).strip() for v in old_valid["condition_snapshot_version"].dropna()})
        if len(values) == 1:
            version_number(values[0])
            return values[0]
    raise SnapshotError(
        f"无法从上一版目录 {path} 判断其对应的第一模块 v### 版本；"
        "V1.6 迁移时请确保 episode 中保留 condition_snapshot_version。"
    )


def _build_remap_report(
    *,
    source_policy_version: str | None,
    target_version: str,
    condition_previous_version: str | None,
    input_alignment: dict[str, Any],
    reports: list[dict[str, Any]],
    tail_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_reports = [
        item for item in reports
        if str(item.get("dataset", "")).startswith("final_")
    ] or reports
    migration_reports = [
        item for item in reports
        if str(item.get("dataset", "")).startswith("historical_")
    ] or reports
    return {
        "source_policy_version": source_policy_version,
        "target_version": target_version,
        "condition_previous_snapshot_version": condition_previous_version,
        "input_alignment": input_alignment,
        "tail_remap": tail_report or {},
        "datasets": reports,
        "final_episode_count": int(sum(item.get("episode_count", 0) for item in final_reports)),
        "final_resolved_episode_count": int(sum(item.get("resolved_episode_count", 0) for item in final_reports)),
        "final_unresolved_episode_count": int(sum(item.get("unresolved_episode_count", 0) for item in final_reports)),
        "historical_remapped_episode_count": int(sum(item.get("remapped_episode_count", 0) for item in migration_reports)),
        "condition_changes": [
            change
            for item in migration_reports
            for change in item.get("condition_changes", [])
        ],
    }


def run_initial_training(
    input_paths: Sequence[str] | str | None = None,
    output_root: str | None = None,
    condition_snapshot: str | None = None,
    config_spec: str | None = None,
    allow_existing_output: bool = False,
    progress_enabled: bool | None = None,
) -> Path:
    """执行与第一模块同版本的初次训练。"""
    plant, training = load_config(config_spec)
    progress = TrainingProgress.from_training_config(training, progress_enabled)
    recorder = PerformanceRecorder(
        enabled=bool(training.get("performance", {}).get("record_stage_timings", True))
    )
    progress.update(1.0, "加载并校验统一配置", force=True)
    try:
        inputs = _resolve_inputs(input_paths, plant, "INITIAL")
        output = Path(output_root or plant["paths"]["output_root"])
        condition_index = _resolve_condition_index(condition_snapshot, plant)
        version = condition_index.snapshot_version
        target = output / "snapshots" / version
        progress.update(3.0, f"第一模块目标版本：{version}")

        existing_versions = [
            item for item in (output / "snapshots").glob("v*")
            if item.is_dir()
        ] if (output / "snapshots").exists() else []
        if existing_versions and not allow_existing_output:
            raise SnapshotError(
                "输出目录已经存在第二模块历史版本。初次训练不会继承旧经验，"
                "已有模型继续学习必须使用增量训练；确需建立独立基线时请更换输出目录，"
                "或显式传入 --allow-existing-output。"
            )
        if target.exists() and not allow_existing_output:
            raise SnapshotError(
                f"第二模块 {version} 已存在: {target}。同一第一模块版本不能重复发布。"
            )

        with recorder.measure("initial_prepare_raw_data"):
            raw_df, warnings = prepare_raw_data(
                inputs, plant, training, progress=progress.child(5.0, 27.0)
            )
        recorder.add_counter("raw_row_count", len(raw_df))
        with recorder.measure("initial_input_alignment"):
            input_alignment = validate_input_frame_alignment(
                raw_df, condition_index, context="初次训练输入 CSV"
            )
        with recorder.measure("initial_episode_pipeline"):
            valid, invalid, effective, _ = run_episode_pipeline(
                raw_df,
                plant,
                training,
                aggregate_results=False,
                progress=progress.child(27.0, 70.0),
            )
        alignment_cfg = training.get("version_alignment", {})
        with recorder.measure("initial_condition_remap"):
            valid, valid_report, _ = remap_episode_conditions(
                valid,
                condition_index,
                strict=bool(alignment_cfg.get("fail_on_unresolved_valid_episode", True)),
                dataset_name="initial_valid",
            )
            invalid, invalid_report, unresolved_invalid = remap_episode_conditions(
                invalid,
                condition_index,
                strict=bool(alignment_cfg.get("fail_on_unresolved_invalid_episode", False)),
                dataset_name="initial_invalid",
            )
        recorder.add_counter("valid_episode_count", len(valid))
        recorder.add_counter("invalid_episode_count", len(invalid))
        with recorder.measure("initial_aggregate_all_levels"):
            aggregated = aggregate_all_levels(
                valid,
                plant,
                training,
                progress=progress.child(70.0, 82.0),
                condition_members=condition_index.condition_members,
                performance_recorder=recorder,
            )
        remap_report = _build_remap_report(
            source_policy_version=None,
            target_version=version,
            condition_previous_version=condition_index.previous_snapshot_version,
            input_alignment=input_alignment,
            reports=[valid_report, invalid_report],
        )
        if not unresolved_invalid.empty:
            warnings.append(
                f"INVALID episode 中有 {len(unresolved_invalid)} 条无法按当前 grid_id 映射，已保留审计。"
            )
        snapshot = write_snapshot(
            output,
            version,
            plant,
            training,
            effective,
            raw_df,
            valid,
            invalid,
            aggregated,
            training_mode="INITIAL",
            previous_snapshot=None,
            warnings=warnings,
            source_paths=_source_paths(inputs),
            condition_index=condition_index,
            remap_report=remap_report,
            performance_recorder=recorder,
            progress=progress.child(82.0, 99.0),
        )
        progress.update(100.0, f"初次离线训练完成：{snapshot}", force=True)
        return snapshot
    except Exception as exc:
        progress.fail(str(exc))
        raise


def run_incremental_training(
    input_paths: Sequence[str] | str | None = None,
    output_root: str | None = None,
    previous_snapshot: str | None = None,
    condition_snapshot: str | None = None,
    config_spec: str | None = None,
    recalibrate: bool = False,
    progress_enabled: bool | None = None,
) -> Path:
    """执行增量训练，并按当前第一模块映射重排全部历史 episode。"""
    plant, training = load_config(config_spec)
    progress = TrainingProgress.from_training_config(training, progress_enabled)
    recorder = PerformanceRecorder(
        enabled=bool(training.get("performance", {}).get("record_stage_timings", True))
    )
    progress.update(1.0, "加载并校验统一配置", force=True)
    try:
        inputs = _resolve_inputs(input_paths, plant, "INCREMENTAL")
        output = Path(output_root or plant["paths"]["output_root"])
        condition_index = _resolve_condition_index(condition_snapshot, plant)
        target_version = condition_index.snapshot_version
        previous = Path(previous_snapshot) if previous_snapshot else latest_snapshot_path(output)
        with recorder.measure("incremental_load_previous_snapshot"):
            old_valid, old_invalid, tail, previous_effective = load_previous_episodes(previous)
        source_version = _policy_version_from_snapshot_dir(
            previous, previous_effective, old_valid
        )

        if version_number(target_version) <= version_number(source_version):
            raise SnapshotError(
                f"第一模块目标版本 {target_version} 必须晚于第二模块上一版本 {source_version}"
            )
        alignment_cfg = training.get("version_alignment", {})
        if (
            not bool(alignment_cfg.get("allow_condition_version_jump", True))
            and condition_index.previous_snapshot_version != source_version
        ):
            raise SnapshotError(
                f"第一模块 {target_version} 的 previous_snapshot_version="
                f"{condition_index.previous_snapshot_version}，但第二模块上一版为 {source_version}；"
                "当前配置不允许跨版本追赶。"
            )
        progress.update(4.0, f"版本对齐：第二模块 {source_version} → 第一模块 {target_version}")

        _validate_incremental_compatibility(plant, training, previous_effective)
        progress.update(12.0, f"继承旧经验：VALID={len(old_valid)}，INVALID={len(old_invalid)}")

        with recorder.measure("incremental_remap_historical"):
            old_valid, old_valid_report, _ = remap_episode_conditions(
                old_valid,
                condition_index,
                strict=bool(alignment_cfg.get("fail_on_unresolved_valid_episode", True)),
                dataset_name="historical_valid",
            )
            old_invalid, old_invalid_report, unresolved_old_invalid = remap_episode_conditions(
                old_invalid,
                condition_index,
                strict=bool(alignment_cfg.get("fail_on_unresolved_invalid_episode", False)),
                dataset_name="historical_invalid",
            )
            tail, tail_report = remap_raw_condition_rows(
                tail,
                condition_index,
                strict=True,
                context="上一版 context_tail",
            )
        progress.update(20.0, f"历史 episode 已按 {target_version} 重新归属")

        with recorder.measure("incremental_prepare_new_raw_data"):
            new_df, warnings = prepare_raw_data(
                inputs, plant, training, progress=progress.child(20.0, 36.0)
            )
        with recorder.measure("incremental_input_alignment"):
            input_alignment = validate_input_frame_alignment(
                new_df, condition_index, context="增量训练输入 CSV"
            )
        with recorder.measure("incremental_combine_tail"):
            combined_raw = _combine_tail_and_new(tail, new_df, plant, training)
        with recorder.measure("incremental_episode_pipeline"):
            new_valid, new_invalid, effective, _ = run_episode_pipeline(
                combined_raw,
                plant,
                training,
                previous_effective_config=previous_effective,
                recalibrate=recalibrate,
                aggregate_results=False,
                progress=progress.child(36.0, 66.0),
            )
        with recorder.measure("incremental_remap_new"):
            new_valid, new_valid_report, _ = remap_episode_conditions(
                new_valid,
                condition_index,
                strict=bool(alignment_cfg.get("fail_on_unresolved_valid_episode", True)),
                dataset_name="new_valid",
            )
            new_invalid, new_invalid_report, unresolved_new_invalid = remap_episode_conditions(
                new_invalid,
                condition_index,
                strict=bool(alignment_cfg.get("fail_on_unresolved_invalid_episode", False)),
                dataset_name="new_invalid",
            )

        valid = _concat_episode_frames(old_valid, new_valid)
        invalid = _concat_episode_frames(old_invalid, new_invalid)
        if not valid.empty:
            # 历史 episode 是事实源；context_tail 重新提取出的同 ID 片段不覆盖其
            # original_condition_label 等审计来源。
            valid.drop_duplicates(subset=["episode_id"], keep="first", inplace=True)
            valid.sort_values("action_start_time", inplace=True)
            valid.reset_index(drop=True, inplace=True)
        if not invalid.empty:
            invalid.drop_duplicates(subset=["episode_id"], keep="last", inplace=True)
            if not valid.empty:
                invalid = invalid[~invalid["episode_id"].isin(set(valid["episode_id"]))].copy()
            invalid.sort_values("action_start_time", inplace=True)
            invalid.reset_index(drop=True, inplace=True)

        # 旧、新数据均已按目标版本重映射。去重后只做向量化一致性校验，
        # 不再把累计历史 DataFrame 完整改写第二次。
        with recorder.measure("incremental_final_mapping_validation"):
            final_valid_report, _ = validate_episode_current_mapping(
                valid,
                condition_index,
                strict=bool(alignment_cfg.get("fail_on_unresolved_valid_episode", True)),
                dataset_name="final_valid",
            )
            final_invalid_report, unresolved_final_invalid = validate_episode_current_mapping(
                invalid,
                condition_index,
                strict=bool(alignment_cfg.get("fail_on_unresolved_invalid_episode", False)),
                dataset_name="final_invalid",
            )
        progress.update(72.0, f"累计经验：VALID={len(valid)}，INVALID={len(invalid)}")

        recorder.add_counter("historical_valid_episode_count", len(old_valid))
        recorder.add_counter("new_valid_episode_count", len(new_valid))
        recorder.add_counter("final_valid_episode_count", len(valid))
        recorder.add_counter("final_invalid_episode_count", len(invalid))
        with recorder.measure("incremental_aggregate_all_levels"):
            aggregated = aggregate_all_levels(
                valid,
                plant,
                training,
                progress=progress.child(72.0, 84.0),
                condition_members=condition_index.condition_members,
                performance_recorder=recorder,
            )
        remap_report = _build_remap_report(
            source_policy_version=source_version,
            target_version=target_version,
            condition_previous_version=condition_index.previous_snapshot_version,
            input_alignment=input_alignment,
            reports=[
                old_valid_report,
                old_invalid_report,
                new_valid_report,
                new_invalid_report,
                final_valid_report,
                final_invalid_report,
            ],
            tail_report=tail_report,
        )
        unresolved_invalid_count = sum(
            len(frame)
            for frame in (
                unresolved_old_invalid,
                unresolved_new_invalid,
                unresolved_final_invalid,
            )
        )
        if unresolved_invalid_count:
            warnings.append(
                f"INVALID episode 共出现 {unresolved_invalid_count} 条无法映射记录，未用于策略聚合。"
            )

        snapshot = write_snapshot(
            output,
            target_version,
            plant,
            training,
            effective,
            new_df,
            valid,
            invalid,
            aggregated,
            training_mode=("INCREMENTAL_RECALIBRATED" if recalibrate else "INCREMENTAL"),
            previous_snapshot=str(previous.resolve()),
            warnings=warnings,
            source_paths=_source_paths(inputs),
            condition_index=condition_index,
            remap_report=remap_report,
            performance_recorder=recorder,
            progress=progress.child(84.0, 99.0),
        )
        progress.update(100.0, f"增量离线训练完成：{snapshot}", force=True)
        return snapshot
    except Exception as exc:
        progress.fail(str(exc))
        raise
