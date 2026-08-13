from __future__ import annotations

from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    source = read(path)
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, got {count}\n---OLD---\n{old[:500]}")
    write(path, source.replace(old, new, 1))


def remove_file(path: str) -> None:
    target = ROOT / path
    if target.exists():
        target.unlink()


# ---------------------------------------------------------------------------
# 1. FAST detector lifecycle/checkpoint support
# ---------------------------------------------------------------------------
replace_once(
    "system/model/map_control/fast_change_mode/fast_change_config.py",
    '''    "state_machine": {
        # 一旦进入 FAST_CHANGE，至少保持多久，单位：分钟。
        "minimum_fast_hold_minutes": 4.0,
        # 原始趋势不再是 FAST 后，连续多少个周期稳定才进入 FAST_RECOVERY。
        "exit_stable_cycles": 4,
        # FAST_RECOVERY 最少持续时间，单位：分钟。
        "recovery_hold_minutes": 2.0,
    },
}
''',
    '''    "state_machine": {
        # 一旦进入 FAST_CHANGE，至少保持多久，单位：分钟。
        "minimum_fast_hold_minutes": 4.0,
        # 原始趋势不再是 FAST 后，连续多少个周期稳定才进入 FAST_RECOVERY。
        "exit_stable_cycles": 4,
        # FAST_RECOVERY 最少持续时间，单位：分钟。
        "recovery_hold_minutes": 2.0,
    },

    # ------------------------------------------------------------------
    # 生命周期/存储。这里只保存小型 checkpoint、FAST 事件摘要和版本 manifest，
    # 不永久复制整份原始 CSV，避免历史数据越积越大。
    # ------------------------------------------------------------------
    "lifecycle": {
        # 离线 FAST 快照最多保留多少个版本；与第一/第二模块一样滚动清理旧版。
        "max_versions_to_keep": 5,
        # 在线每处理多少条数据覆盖写一次 runtime checkpoint；事件闭合时会立即落盘。
        "runtime_checkpoint_every_samples": 20,
        # 在线是否持久化闭合 FAST 事件的月度 JSONL 摘要。
        "persist_compact_events": True,
    },
}
''',
)

replace_once(
    "system/model/map_control/fast_change_mode/fast_change_mode_detector.py",
    '''    def get_state(self) -> Dict[str, Any]:
        return {
            "mode": self._mode,
            "fast_started_at": self._iso(self._fast_started_at),
            "last_fast_seen_at": self._iso(self._last_fast_seen_at),
            "recovery_until": self._iso(self._recovery_until),
            "exit_stable_count": int(self._exit_stable_count),
            "last_fast_direction": self._last_fast_direction,
            "last_fast_exact_mode": self._last_fast_exact_mode,
        }
''',
    '''    def get_state(self) -> Dict[str, Any]:
        return {
            "mode": self._mode,
            "fast_started_at": self._iso(self._fast_started_at),
            "last_fast_seen_at": self._iso(self._last_fast_seen_at),
            "recovery_until": self._iso(self._recovery_until),
            "exit_stable_count": int(self._exit_stable_count),
            "last_fast_direction": self._last_fast_direction,
            "last_fast_exact_mode": self._last_fast_exact_mode,
        }

    def export_checkpoint(self) -> Dict[str, Any]:
        """导出足以继续因果计算的轻量 checkpoint，不包含完整历史 CSV。"""
        detection_config = {
            key: copy.deepcopy(self.config.get(key))
            for key in ("enabled", "trend", "effect", "state_machine")
        }
        return {
            "schema_version": "1.0",
            "condition_axes": [
                {
                    "column": str(axis.get("column", "")),
                    "step": float(axis.get("step", 0.0)),
                }
                for axis in self.axes
            ],
            "detection_config": detection_config,
            "series_state": {
                key: {
                    "timestamp": self._iso(value.get("timestamp")),
                    "ema1": float(value.get("ema1", 0.0)),
                    "ema2": float(value.get("ema2", 0.0)),
                    "dema": float(value.get("dema", 0.0)),
                }
                for key, value in self._series_state.items()
            },
            "series_history": {
                key: [[self._iso(ts), float(value)] for ts, value in history]
                for key, history in self._series_history.items()
            },
            "state_machine": self.get_state(),
        }

    def load_checkpoint(self, checkpoint: Mapping[str, Any]) -> None:
        """恢复离线增量/在线重启所需状态；检测语义变化时拒绝混用。"""
        data = dict(checkpoint or {})
        expected_axes = [
            {"column": str(axis.get("column", "")), "step": float(axis.get("step", 0.0))}
            for axis in self.axes
        ]
        if list(data.get("condition_axes") or []) != expected_axes:
            raise FastChangeConfigurationError(
                "FAST checkpoint 的 condition_axes 与当前 plant_config 不一致，必须重新初次回放"
            )
        expected_config = {
            key: copy.deepcopy(self.config.get(key))
            for key in ("enabled", "trend", "effect", "state_machine")
        }
        if data.get("detection_config") != expected_config:
            raise FastChangeConfigurationError(
                "FAST 检测参数已变化，旧 checkpoint 不能继续增量使用，请重新初次回放"
            )

        self.reset()
        for key, value in dict(data.get("series_state") or {}).items():
            timestamp = value.get("timestamp")
            if not timestamp:
                continue
            self._series_state[str(key)] = {
                "timestamp": pd.Timestamp(timestamp),
                "ema1": float(value.get("ema1", 0.0)),
                "ema2": float(value.get("ema2", 0.0)),
                "dema": float(value.get("dema", 0.0)),
            }
        for key, values in dict(data.get("series_history") or {}).items():
            history: Deque[Tuple[pd.Timestamp, float]] = deque()
            for item in values or []:
                if not isinstance(item, (list, tuple)) or len(item) != 2 or not item[0]:
                    continue
                history.append((pd.Timestamp(item[0]), float(item[1])))
            self._series_history[str(key)] = history

        machine = dict(data.get("state_machine") or {})
        self._mode = str(machine.get("mode", REGULAR))
        self._fast_started_at = (
            pd.Timestamp(machine["fast_started_at"])
            if machine.get("fast_started_at")
            else None
        )
        self._last_fast_seen_at = (
            pd.Timestamp(machine["last_fast_seen_at"])
            if machine.get("last_fast_seen_at")
            else None
        )
        self._recovery_until = (
            pd.Timestamp(machine["recovery_until"])
            if machine.get("recovery_until")
            else None
        )
        self._exit_stable_count = int(machine.get("exit_stable_count", 0))
        self._last_fast_direction = str(machine.get("last_fast_direction", "NONE"))
        self._last_fast_exact_mode = str(machine.get("last_fast_exact_mode", "STEADY"))
''',
)

history_manager = r'''"""FAST_CHANGE 离线/在线统一历史回放与轻量生命周期管理。

原则：
- 在线每来一条数据调用同一个 detector 一次，只在内存保留短窗口；
- 离线按时间顺序回放同一个 detector，保证与在线完全同语义且不使用未来值；
- 不永久复制完整原始 CSV，只保存 detector checkpoint、FAST 事件摘要和版本 manifest；
- 初次/增量可通过 checkpoint 接续，避免每次从历史第一行重新计算。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG as SITE_PLANT_CONFIG
from system.model.config.standard_fields import TARGET_SO2_COLUMN, TIME_COLUMN

from .fast_change_config import FAST_CHANGE_CONFIG
from .fast_change_mode_detector import FAST_CHANGE, FAST_RECOVERY, REGULAR, FastChangeModeDetector


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "files" / "fast_change_mode_output"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "files" / "fast_change_mode_runtime"

FAST_CONTEXT_COLUMNS = (
    "fast_change_mode",
    "fast_change_active",
    "fast_change_recovery_active",
    "fast_change_raw_trigger",
    "fast_change_direction",
    "fast_change_severity",
    "fast_change_exact_trend_mode",
    "fast_change_raw_exact_trend_mode",
    "fast_change_trend_risk_level",
    "fast_change_effect_risk_level",
    "fast_change_effect_state",
    "fast_change_effect_direction",
    "fast_change_overall_risk_level",
    "fast_change_axis_columns",
    "fast_change_axis_rates",
    "fast_change_axis_levels",
    "fast_change_axis_direction_ratios",
    "fast_change_trigger_axes",
    "fast_change_available_axis_count",
    "fast_change_trend_ready",
    "fast_change_current_so2",
    "fast_change_target_so2",
    "fast_change_target_error",
    "fast_change_emission_limit",
    "fast_change_outlet_so2_rate",
    "fast_change_outlet_so2_trend",
    "fast_change_input_valid",
    "fast_change_reason_codes",
)

_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "EMERGENCY": 3}


def _json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_number(name: str) -> int:
    text = str(name)
    return int(text[1:]) if text.startswith("v") and text[1:].isdigit() else -1


class FastChangeHistoryManager:
    """统一管理离线回放、增量 checkpoint 和在线轻量持久化。"""

    def __init__(
        self,
        *,
        config: Optional[Dict[str, Any]] = None,
        plant_config: Optional[Dict[str, Any]] = None,
        output_root: Optional[str | Path] = None,
        runtime_root: Optional[str | Path] = None,
        persist_runtime: bool = False,
    ) -> None:
        self.config = dict(FAST_CHANGE_CONFIG if config is None else config)
        self.plant = dict(SITE_PLANT_CONFIG if plant_config is None else plant_config)
        self.output_root = Path(output_root or DEFAULT_OUTPUT_ROOT)
        self.runtime_root = Path(runtime_root or DEFAULT_RUNTIME_ROOT)
        self.persist_runtime = bool(persist_runtime)
        self.detector = FastChangeModeDetector(self.config, self.plant)
        self._sample_count = 0
        self._open_event: Optional[Dict[str, Any]] = None
        self._completed_events: list[Dict[str, Any]] = []
        if self.persist_runtime:
            self._load_runtime_if_available()

    def export_checkpoint(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "detector": self.detector.export_checkpoint(),
            "sample_count": int(self._sample_count),
            "open_event": self._open_event,
        }

    def load_checkpoint(self, checkpoint: Mapping[str, Any]) -> None:
        data = dict(checkpoint or {})
        detector_state = data.get("detector") or data
        self.detector.load_checkpoint(detector_state)
        self._sample_count = int(data.get("sample_count", 0))
        self._open_event = dict(data["open_event"]) if data.get("open_event") else None

    def annotate_dataframe(
        self,
        frame: pd.DataFrame,
        *,
        target_column: str = TARGET_SO2_COLUMN,
        sort_by_time: bool = True,
    ) -> pd.DataFrame:
        """离线因果回放；返回本次训练用标签，不在这里永久保存整份 CSV。"""
        result = frame.copy()
        if result.empty:
            for column in FAST_CONTEXT_COLUMNS:
                if column not in result.columns:
                    result[column] = pd.Series(dtype="object")
            return result
        if TIME_COLUMN not in result.columns:
            raise ValueError(f"FAST 历史回放缺少时间字段 {TIME_COLUMN}")
        result[TIME_COLUMN] = pd.to_datetime(result[TIME_COLUMN], errors="coerce")
        if result[TIME_COLUMN].isna().any():
            raise ValueError("FAST 历史回放存在无法解析的时间戳")
        if sort_by_time and not result[TIME_COLUMN].is_monotonic_increasing:
            result.sort_values(TIME_COLUMN, inplace=True, kind="stable")
            result.reset_index(drop=True, inplace=True)

        outputs: list[Dict[str, Any]] = []
        for row in result.to_dict(orient="records"):
            target = row.get(target_column)
            context = self.detector.evaluate(row, target=target)
            compact = {key: context.get(key) for key in FAST_CONTEXT_COLUMNS}
            outputs.append(compact)
            self._observe(context)
            self._sample_count += 1
        annotations = pd.DataFrame(outputs, index=result.index)
        for column in annotations.columns:
            result[column] = annotations[column]
        return result

    def evaluate_online(
        self,
        row: Mapping[str, Any],
        *,
        target: Optional[Any] = None,
    ) -> Dict[str, Any]:
        context = self.detector.evaluate(row, target=target)
        closed = self._observe(context)
        self._sample_count += 1
        if self.persist_runtime:
            every = max(
                1,
                int(self.config.get("lifecycle", {}).get("runtime_checkpoint_every_samples", 20)),
            )
            if closed or self._sample_count % every == 0:
                self.flush_runtime()
            if closed and bool(
                self.config.get("lifecycle", {}).get("persist_compact_events", True)
            ):
                self._append_runtime_event(closed)
        return context

    def _observe(self, context: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        mode = str(context.get("fast_change_mode", REGULAR))
        state = dict(context.get("fast_change_state") or {})
        now = (
            state.get("last_fast_seen_at")
            or state.get("recovery_until")
            or pd.Timestamp.now().isoformat()
        )
        if mode == FAST_CHANGE and self._open_event is None:
            start = state.get("fast_started_at") or now
            self._open_event = {
                "event_id": "FAST_%s" % str(start).replace(":", "").replace("-", ""),
                "start_time": start,
                "fast_end_time": None,
                "recovery_end_time": None,
                "last_time": now,
                "directions": [],
                "exact_modes": [],
                "trigger_axes": [],
                "max_effect_risk": "LOW",
                "max_overall_risk": "LOW",
                "peak_outlet_so2": None,
                "maximum_abs_axis_rates": {},
            }
        event = self._open_event
        if event is None:
            return None

        event["last_time"] = now
        if mode == FAST_CHANGE:
            event["fast_end_time"] = now
        direction = str(context.get("fast_change_direction", "NONE"))
        exact = str(context.get("fast_change_exact_trend_mode", "STEADY"))
        if direction not in {"", "NONE"} and direction not in event["directions"]:
            event["directions"].append(direction)
        if exact not in {"", "STEADY"} and exact not in event["exact_modes"]:
            event["exact_modes"].append(exact)
        for axis in context.get("fast_change_trigger_axes") or []:
            if str(axis) not in event["trigger_axes"]:
                event["trigger_axes"].append(str(axis))
        for key in ("max_effect_risk", "max_overall_risk"):
            source = (
                context.get("fast_change_effect_risk_level")
                if key == "max_effect_risk"
                else context.get("fast_change_overall_risk_level")
            )
            source = str(source or "LOW")
            if _RISK_ORDER.get(source, 0) > _RISK_ORDER.get(str(event[key]), 0):
                event[key] = source
        so2 = context.get("fast_change_current_so2")
        try:
            so2_value = float(so2)
        except (TypeError, ValueError):
            so2_value = None
        if so2_value is not None:
            peak = event.get("peak_outlet_so2")
            event["peak_outlet_so2"] = so2_value if peak is None else max(float(peak), so2_value)
        for axis, rate in dict(context.get("fast_change_axis_rates") or {}).items():
            try:
                value = abs(float(rate))
            except (TypeError, ValueError):
                continue
            current = float(event["maximum_abs_axis_rates"].get(str(axis), 0.0))
            event["maximum_abs_axis_rates"][str(axis)] = max(current, value)

        if mode == REGULAR:
            event["recovery_end_time"] = now
            try:
                event["duration_minutes"] = (
                    pd.Timestamp(now) - pd.Timestamp(event["start_time"])
                ).total_seconds() / 60.0
            except Exception:
                event["duration_minutes"] = None
            closed = dict(event)
            self._completed_events.append(closed)
            self._open_event = None
            return closed
        return None

    def run_summary(self, frame: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        return {
            "sample_count": int(len(frame)) if frame is not None else int(self._sample_count),
            "completed_event_count": len(self._completed_events),
            "open_event": self._open_event,
            "detector_state": self.detector.get_state(),
        }

    def publish_snapshot(
        self,
        version: str,
        *,
        source_paths: Optional[Iterable[str]] = None,
        summary: Optional[Mapping[str, Any]] = None,
    ) -> Path:
        """发布轻量版本：checkpoint + 本批 FAST 事件 + summary + manifest。"""
        snapshot = self.output_root / "snapshots" / str(version)
        if snapshot.exists():
            raise FileExistsError(f"FAST_CHANGE 快照已存在: {snapshot}")
        snapshot.mkdir(parents=True)
        _write_json_atomic(snapshot / "checkpoint.json", self.export_checkpoint())
        _write_json_atomic(
            snapshot / "summary.json",
            {
                **dict(summary or {}),
                "version": str(version),
                "source_paths": [str(value) for value in (source_paths or [])],
                "completed_event_count": len(self._completed_events),
                "open_event": self._open_event,
            },
        )
        events = pd.DataFrame(self._completed_events)
        events.to_csv(snapshot / "fast_events.csv", index=False, encoding="utf-8-sig")
        files = [p for p in snapshot.iterdir() if p.is_file()]
        _write_json_atomic(
            snapshot / "manifest.json",
            {
                "version": str(version),
                "files": [
                    {"path": p.name, "size": p.stat().st_size, "sha256": _sha256(p)}
                    for p in sorted(files)
                ],
            },
        )
        self._cleanup_old_versions()
        return snapshot

    def _cleanup_old_versions(self) -> None:
        keep = int(self.config.get("lifecycle", {}).get("max_versions_to_keep", 5))
        if keep <= 0:
            return
        root = self.output_root / "snapshots"
        if not root.exists():
            return
        versions = sorted(
            [p for p in root.iterdir() if p.is_dir() and _version_number(p.name) >= 0],
            key=lambda p: _version_number(p.name),
        )
        for old in versions[:-keep]:
            shutil.rmtree(old)

    def load_snapshot_checkpoint(self, version: Optional[str] = None) -> Dict[str, Any]:
        root = self.output_root / "snapshots"
        if version is None:
            versions = sorted(
                [p for p in root.glob("v*") if p.is_dir() and _version_number(p.name) >= 0],
                key=lambda p: _version_number(p.name),
                reverse=True,
            )
            if not versions:
                raise FileNotFoundError("没有 FAST_CHANGE 历史快照")
            path = versions[0]
        else:
            path = root / str(version)
        return _read_json(path / "checkpoint.json")

    def flush_runtime(self) -> None:
        if not self.persist_runtime:
            return
        _write_json_atomic(self.runtime_root / "checkpoint.json", self.export_checkpoint())

    def _load_runtime_if_available(self) -> None:
        path = self.runtime_root / "checkpoint.json"
        if path.exists():
            self.load_checkpoint(_read_json(path))

    def _append_runtime_event(self, event: Mapping[str, Any]) -> None:
        try:
            ts = pd.Timestamp(event.get("start_time"))
            name = f"fast_events_{ts.year:04d}_{ts.month:02d}.jsonl"
        except Exception:
            name = "fast_events_unknown.jsonl"
        path = self.runtime_root / "events" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), ensure_ascii=False, default=_json_default) + "\n")

    def status(self) -> Dict[str, Any]:
        return {
            "sample_count": int(self._sample_count),
            "open_event": self._open_event,
            "detector_state": self.detector.get_state(),
            "output_root": str(self.output_root),
            "runtime_root": str(self.runtime_root),
        }
'''
write("system/model/map_control/fast_change_mode/fast_change_history_manager.py", history_manager)

replace_once(
    "system/model/map_control/fast_change_mode/__init__.py",
    '''from .fast_change_mode_detector import (
    FAST_CHANGE,
    FAST_RECOVERY,
    REGULAR,
    FastChangeConfigurationError,
    FastChangeModeDetector,
)
''',
    '''from .fast_change_mode_detector import (
    FAST_CHANGE,
    FAST_RECOVERY,
    REGULAR,
    FastChangeConfigurationError,
    FastChangeModeDetector,
)
from .fast_change_history_manager import (
    FAST_CONTEXT_COLUMNS,
    FastChangeHistoryManager,
)
''',
)
replace_once(
    "system/model/map_control/fast_change_mode/__init__.py",
    '''    "FAST_RECOVERY",
]
''',
    '''    "FAST_RECOVERY",
    "FAST_CONTEXT_COLUMNS",
    "FastChangeHistoryManager",
]
''',
)

# ---------------------------------------------------------------------------
# 2. Second-module semantics now consume upstream FAST context
# ---------------------------------------------------------------------------
replace_once(
    "system/model/map_control/slurry_policy_model/_engine/config_loader.py",
    'POLICY_SEMANTICS_VERSION = "TOWER_LEVEL_V3_PUMP_GATED"',
    'POLICY_SEMANTICS_VERSION = "TOWER_LEVEL_V4_FAST_CONTEXT"',
)

pipeline = r'''from __future__ import annotations

import copy
from typing import Any, Callable

import pandas as pd

from .aggregator import aggregate_all_levels
from .calibration import (
    assign_action_magnitude_labels,
    assign_response_labels,
    calibrate_action_magnitude_bins,
    calibrate_response_settings,
)
from .data_loader import assign_continuous_segments, load_input_data
from .episode_extractor import extract_decision_episodes
from .schema import freeze_condition_axes
from .signal_processing import add_clean_valve_columns
from .tower_policy_projection import project_tower_policy_deltas


ProgressCallback = Callable[[float, str], None]
POLICY_SEMANTICS_VERSION = "TOWER_LEVEL_V4_FAST_CONTEXT"


def _emit_range(progress: ProgressCallback | None, start: float, end: float) -> ProgressCallback | None:
    if not progress:
        return None
    return lambda value, message: progress(
        start + (end - start) * min(1.0, max(0.0, float(value))), message
    )


def _normalized_training_semantics(training: dict[str, Any]) -> dict[str, Any]:
    result = freeze_condition_axes(training)
    result.setdefault("state", {})
    result["state"].setdefault("policy_state_mode", "COARSE_TOWER")
    result["policy_semantics_version"] = POLICY_SEMANTICS_VERSION
    return result


def _pump_topology_signature(plant: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tower in plant.get("towers", []):
        if not tower.get("enabled", True):
            continue
        pumps = []
        for pump in tower.get("supply_pumps", []) or []:
            pumps.append(
                {
                    "pump_id": str(pump.get("pump_id", "")),
                    "current_column": str(pump.get("current_column", "")),
                    "run_current_threshold": float(pump.get("run_current_threshold", 0.0)),
                    "served_valve_ids": sorted(str(v) for v in (pump.get("served_valve_ids", []) or [])),
                }
            )
        result.append({"tower_id": str(tower.get("tower_id", "")), "supply_pumps": sorted(pumps, key=lambda x: (x["pump_id"], x["current_column"]))})
    return sorted(result, key=lambda item: item["tower_id"])


def _validate_previous_semantics(previous_effective_config: dict[str, Any] | None, plant: dict[str, Any], training: dict[str, Any]) -> None:
    if not previous_effective_config:
        return
    mode = str(training.get("state", {}).get("policy_state_mode", "COARSE_TOWER")).upper()
    if mode == "LEGACY_DETAILED":
        return
    previous_training = previous_effective_config.get("training", {}) or {}
    previous_mode = str(previous_training.get("state", {}).get("policy_state_mode", "")).upper()
    previous_semantics = str(previous_training.get("policy_semantics_version", "")).upper()
    if previous_mode != "COARSE_TOWER" or previous_semantics != POLICY_SEMANTICS_VERSION:
        raise ValueError(
            "上一版第二模块仍是旧策略语义，不能直接增量混入 "
            f"{POLICY_SEMANTICS_VERSION}。请先用完整历史数据重新执行一次初次训练；"
            "新基线建立后，后续版本可继续正常增量训练。"
        )
    previous_axes = previous_training.get("_condition_axes")
    current_axes = training.get("_condition_axes")
    if previous_axes is not None and previous_axes != current_axes:
        raise ValueError("第一模块 condition axes 已变化，旧第二模块 episode 不能直接增量继承。请重新初次训练。")
    previous_plant = previous_effective_config.get("plant", {}) or {}
    if _pump_topology_signature(previous_plant) != _pump_topology_signature(plant):
        raise ValueError("供浆泵电流阈值或 pump→valve 拓扑已变化，请重新初次训练。")


def prepare_raw_data(input_specs: list[str] | str, plant: dict[str, Any], training: dict[str, Any], progress: ProgressCallback | None = None) -> tuple[pd.DataFrame, list[str]]:
    training = freeze_condition_axes(training)
    df, warnings = load_input_data(input_specs, plant, training, progress=_emit_range(progress, 0.00, 0.72))
    if progress:
        progress(0.78, "划分连续运行数据段")
    df = assign_continuous_segments(df, plant, training)
    if progress:
        segment_count = int(df["continuous_segment_id"].nunique()) if not df.empty else 0
        progress(0.86, f"连续运行段划分完成，共 {segment_count} 段")
        progress(0.90, "执行阀位短窗口中位数去抖")
    df = add_clean_valve_columns(df, plant, training)
    if progress:
        progress(1.0, f"原始数据预处理完成，共 {len(df)} 行")
    return df, warnings


def run_episode_pipeline(
    raw_df: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    previous_effective_config: dict[str, Any] | None = None,
    recalibrate: bool = False,
    aggregate_results: bool = True,
    progress: ProgressCallback | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    training = _normalized_training_semantics(training)
    _validate_previous_semantics(previous_effective_config, plant, training)
    if progress:
        progress(0.02, "读取上游 fast_change_mode 因果标签")

    episodes, _actions = extract_decision_episodes(
        raw_df, plant, training, progress=_emit_range(progress, 0.02, 0.68)
    )

    if progress:
        progress(0.70, "准备动作幅度与响应强度参数")
    if previous_effective_config and not recalibrate:
        effective_action = copy.deepcopy(previous_effective_config["action_magnitude"])
        effective_response = copy.deepcopy(previous_effective_config["response"])
        if progress:
            progress(0.76, "沿用上一版动作幅度和响应强度参数")
    else:
        effective_action = calibrate_action_magnitude_bins(episodes, training)
        effective_response = calibrate_response_settings(episodes, training)
        if progress:
            progress(0.76, "完成动作幅度和响应强度参数标定")

    if not episodes.empty:
        if progress:
            progress(0.79, "标记动作幅度与历史响应标签")
        episodes = assign_action_magnitude_labels(episodes, effective_action)
        episodes = assign_response_labels(episodes, effective_response)
        valid = episodes[episodes["valid"]].copy()
        invalid = episodes[~episodes["valid"]].copy()
        valid = project_tower_policy_deltas(valid, plant)
    else:
        valid = pd.DataFrame()
        invalid = pd.DataFrame()
    if progress:
        progress(0.84, f"决策片段校验完成：VALID={len(valid)}，INVALID={len(invalid)}")

    if aggregate_results:
        aggregated = aggregate_all_levels(valid, plant, training, progress=_emit_range(progress, 0.84, 1.00))
    else:
        aggregated = {
            "conditions": {},
            "condition_grids": {},
            "neighbor_state": {},
            "plant_action_prior": {},
            "transients": {},
            "transient_direction": {},
        }
        if progress:
            progress(1.0, "新增决策片段提取完成，等待与旧经验合并")

    effective = {
        "plant": copy.deepcopy(plant),
        "training": copy.deepcopy(training),
        "action_magnitude": effective_action,
        "response": effective_response,
    }
    return valid, invalid, effective, aggregated
'''
write("system/model/map_control/slurry_policy_model/_engine/pipeline.py", pipeline)

# Remove the old second-module disturbance config: FAST detection is now owned by fast_change_mode.
config_path = "system/model/map_control/slurry_policy_model/slurry_policy_config.py"
source = read(config_path)
start = source.find('    # ------------------------------------------------------------------------\n    # 工况快速变化 / 慢变化识别，用于区分 NORMAL 与 FAST_CHANGE 等场景。\n')
end = source.find('    # ------------------------------------------------------------------------\n    # 离线状态离散化。用于把连续过程状态转成可统计的状态键。\n', start)
if start < 0 or end < 0:
    raise RuntimeError("cannot locate TRAINING_CONFIG disturbance block")
source = source[:start] + source[end:]
write(config_path, source)

# ---------------------------------------------------------------------------
# 3. Episode extraction uses action-start FAST context, no future-based FAST classification
# ---------------------------------------------------------------------------
episode_path = "system/model/map_control/slurry_policy_model/_engine/episode_extractor.py"
source = read(episode_path)
source = source.replace("from .disturbance_classifier import classify_disturbance\n", "")
source = source.replace(",\n    effective_disturbance: dict[str, Any],\n) -> dict[str, Any]:", "\n) -> dict[str, Any]:", 1)
old_classify = '''    record["disturbance_mode"] = classify_disturbance(
        record["episode_condition_axis_1_rate"],
        (
            record["episode_condition_axis_2_rate"]
            if second_axis_col is not None
            else None
        ),
        effective_disturbance,
    )

'''
if old_classify not in source:
    raise RuntimeError("episode classify block not found")
source = source.replace(old_classify, "", 1)
old_outlet = '''    record["outlet_so2_sign_changes"] = _sign_changes(
        response[outlet_col],
        float(training["response"]["oscillation_diff_deadband"]),
    )

    safe_so2_lo, safe_so2_hi = map(float, plant["outlet_so2_safe_range"])
'''
new_outlet = '''    record["outlet_so2_sign_changes"] = _sign_changes(
        response[outlet_col],
        float(training["response"]["oscillation_diff_deadband"]),
    )
    record["after_outlet_so2_rate"] = robust_slope_per_minute(
        response[ts_col], response[outlet_col]
    )
    record["outlet_so2_rate_reduction"] = (
        record["before_outlet_so2_rate"] - record["after_outlet_so2_rate"]
    )

    safe_so2_lo, safe_so2_hi = map(float, plant["outlet_so2_safe_range"])
'''
if old_outlet not in source:
    raise RuntimeError("episode outlet block not found")
source = source.replace(old_outlet, new_outlet, 1)
old_after_hard = '''    record["outlet_so2_over_hard_max"] = bool(
        not outlet_values.empty and (outlet_values > safe_so2_hi).any()
    )

    for tower in enabled_towers(plant):
'''
new_after_hard = '''    record["outlet_so2_over_hard_max"] = bool(
        not outlet_values.empty and (outlet_values > safe_so2_hi).any()
    )
    record["post_outlet_so2_peak"] = (
        float(outlet_values.max()) if not outlet_values.empty else np.nan
    )
    record["post_outlet_so2_safe_ratio"] = (
        float(((outlet_values >= safe_so2_lo) & (outlet_values <= safe_so2_hi)).mean())
        if not outlet_values.empty else 0.0
    )
    effect_states = (
        response["fast_change_effect_state"].astype(str)
        if "fast_change_effect_state" in response.columns
        else pd.Series("UNKNOWN", index=response.index)
    )
    record["post_outlet_so2_warning_ratio"] = float(
        effect_states.isin(["WARNING", "EMERGENCY"]).mean()
    ) if len(effect_states) else 0.0
    record["post_outlet_so2_emergency_ratio"] = float(
        (effect_states == "EMERGENCY").mean()
    ) if len(effect_states) else 0.0

    for tower in enabled_towers(plant):
'''
if old_after_hard not in source:
    raise RuntimeError("episode hard max block not found")
source = source.replace(old_after_hard, new_after_hard, 1)
old_identity = '''    identity_window = full[
        full[ts_col] >= pd.Timestamp(record["action_start_time"])
    ]
    if identity_window.empty:
        identity_window = full
    attribution = analyze_condition_attribution(
        identity_window,
        str(record["episode_type"]),
        str(record["disturbance_mode"]),
        training,
    )
'''
new_identity = '''    identity_window = full[
        full[ts_col] >= pd.Timestamp(record["action_start_time"])
    ]
    if identity_window.empty:
        identity_window = full
    if identity_window.empty:
        raise ValueError("episode 无法找到 action_start 对应的 FAST 上下文")
    fast_row = identity_window.iloc[0]
    required_fast_fields = (
        "fast_change_mode",
        "fast_change_direction",
        "fast_change_exact_trend_mode",
        "fast_change_effect_risk_level",
        "fast_change_overall_risk_level",
        "fast_change_outlet_so2_rate",
    )
    missing_fast = [name for name in required_fast_fields if name not in identity_window.columns]
    if missing_fast:
        raise KeyError("第二模块训练输入缺少 FAST 标签: %s" % missing_fast)
    record["fast_change_mode"] = str(fast_row.get("fast_change_mode", "REGULAR"))
    record["fast_change_direction"] = str(fast_row.get("fast_change_direction", "NONE"))
    record["fast_change_exact_trend_mode"] = str(
        fast_row.get("fast_change_exact_trend_mode", "STEADY")
    )
    record["fast_change_severity"] = str(fast_row.get("fast_change_severity", "STEADY"))
    record["fast_change_effect_risk_level"] = str(
        fast_row.get("fast_change_effect_risk_level", "LOW")
    )
    record["fast_change_overall_risk_level"] = str(
        fast_row.get("fast_change_overall_risk_level", "LOW")
    )
    record["fast_change_effect_state"] = str(
        fast_row.get("fast_change_effect_state", "UNKNOWN")
    )
    record["disturbance_mode"] = record["fast_change_exact_trend_mode"]
    attribution = analyze_condition_attribution(
        identity_window,
        str(record["episode_type"]),
        str(record["disturbance_mode"]),
        training,
    )
'''
if old_identity not in source:
    raise RuntimeError("episode identity block not found")
source = source.replace(old_identity, new_identity, 1)
# Remove effective_disturbance from remaining signatures/calls.
source = source.replace("    effective_disturbance: dict[str, Any],\n", "")
source = source.replace("            effective_disturbance,\n", "")
source = source.replace("        effective_disturbance,\n", "")
write(episode_path, source)

# Add new output columns.
schema_path = "system/model/map_control/slurry_policy_model/_engine/schema.py"
replace_once(
    schema_path,
    '''            "before_outlet_so2_rate",
            "episode_condition_axis_1_rate",
            "episode_condition_axis_2_rate",
            "disturbance_mode",
''',
    '''            "before_outlet_so2_rate",
            "after_outlet_so2_rate",
            "outlet_so2_rate_reduction",
            "episode_condition_axis_1_rate",
            "episode_condition_axis_2_rate",
            "disturbance_mode",
            "fast_change_mode",
            "fast_change_direction",
            "fast_change_exact_trend_mode",
            "fast_change_severity",
            "fast_change_effect_risk_level",
            "fast_change_overall_risk_level",
            "fast_change_effect_state",
''',
)
replace_once(
    schema_path,
    '''            "post_outlet_so2_range",
            "outlet_so2_sign_changes",
''',
    '''            "post_outlet_so2_range",
            "post_outlet_so2_peak",
            "post_outlet_so2_safe_ratio",
            "post_outlet_so2_warning_ratio",
            "post_outlet_so2_emergency_ratio",
            "outlet_so2_sign_changes",
''',
)

# ---------------------------------------------------------------------------
# 4. Transient-specific aggregation: exact + same-direction pooled experience
# ---------------------------------------------------------------------------
agg_path = "system/model/map_control/slurry_policy_model/_engine/aggregator.py"
source = read(agg_path)
marker = '''\n\ndef aggregate_plant_action_prior(\n'''
if marker not in source:
    raise RuntimeError("aggregate_plant_action_prior marker missing")
transient_func = r'''

def _weighted_numeric_mean(series: pd.Series, weights: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce")
    mask = values.notna()
    if not mask.any():
        return None
    selected = values[mask].astype(float)
    selected_weights = weights.loc[selected.index].astype(float)
    total = float(selected_weights.sum())
    if total <= 0:
        return float(selected.mean())
    return float((selected * selected_weights).sum() / total)


def aggregate_transient_action_profile(
    group: pd.DataFrame, plant: dict[str, Any], training: dict[str, Any]
) -> dict[str, Any]:
    """FAST 专属历史效果：保留普通安全/可靠性，同时评价趋势抑制与峰值风险。"""
    profile = aggregate_action_profile(group, plant, training)
    weights = _weight_series(group)
    profile["transient_effect"] = {
        "fast_direction": _weighted_mode(
            group.get("fast_change_direction", pd.Series("UNKNOWN", index=group.index)),
            weights,
        ),
        "before_outlet_so2_rate": _distribution(group["before_outlet_so2_rate"], weights),
        "after_outlet_so2_rate": _distribution(group["after_outlet_so2_rate"], weights),
        "outlet_so2_rate_reduction": _distribution(group["outlet_so2_rate_reduction"], weights),
        "post_outlet_so2_peak": _distribution(group["post_outlet_so2_peak"], weights),
        "mean_safe_ratio": _weighted_numeric_mean(group["post_outlet_so2_safe_ratio"], weights),
        "mean_warning_ratio": _weighted_numeric_mean(group["post_outlet_so2_warning_ratio"], weights),
        "mean_emergency_ratio": _weighted_numeric_mean(group["post_outlet_so2_emergency_ratio"], weights),
        "effect_state_counts": group.get(
            "fast_change_effect_state", pd.Series("UNKNOWN", index=group.index)
        ).astype("object").value_counts().to_dict(),
    }
    return profile
'''
source = source.replace(marker, transient_func + marker, 1)
source = source.replace(
    '''        "disturbance_mode",\n        "so2_effect_direction",''',
    '''        "disturbance_mode",\n        "fast_change_direction",\n        "so2_effect_direction",''',
    1,
)
source = source.replace(
    '''        "plant_action_prior": {},\n        "transients": {},\n    }''',
    '''        "plant_action_prior": {},\n        "transients": {},\n        "transient_direction": {},\n    }''',
    1,
)
old_transient = '''    transient["aggregation_weight"] = 1.0
    transients = build_nested_profiles(
        transient,
        "disturbance_mode",
        "policy_state_key_no_grid",
        plant,
        training,
        emit(0.82, 1.00),
    )
    if progress:
        progress(1.0, f"全部层级聚合完成：本地工况={len(conditions)}，临近策略={len(neighbor_state)}")
    return {
        "conditions": conditions,
        "condition_grids": condition_grids,
        "neighbor_state": neighbor_state,
        "plant_action_prior": plant_action_prior,
        "transients": transients,
    }
'''
new_transient = '''    transient["aggregation_weight"] = 1.0
    transients = build_nested_profiles(
        transient,
        "disturbance_mode",
        "policy_state_key_no_grid",
        plant,
        training,
        emit(0.82, 0.91),
        profile_builder=aggregate_transient_action_profile,
    )
    transient_direction = build_nested_profiles(
        transient,
        "fast_change_direction",
        "policy_state_key_no_grid",
        plant,
        training,
        emit(0.91, 1.00),
        profile_builder=aggregate_transient_action_profile,
    )
    if progress:
        progress(1.0, f"全部层级聚合完成：本地工况={len(conditions)}，临近策略={len(neighbor_state)}")
    return {
        "conditions": conditions,
        "condition_grids": condition_grids,
        "neighbor_state": neighbor_state,
        "plant_action_prior": plant_action_prior,
        "transients": transients,
        "transient_direction": transient_direction,
    }
'''
if old_transient not in source:
    raise RuntimeError("transient aggregation block missing")
source = source.replace(old_transient, new_transient, 1)
write(agg_path, source)

# Snapshot writer writes both transient levels.
snapshot_path = "system/model/map_control/slurry_policy_model/_engine/snapshot_store.py"
replace_once(
    snapshot_path,
    '''    _write_owner_collection(
        snapshot_dir,
        "transients",
        aggregated["transients"],
        snapshot_version,
        condition_version,
        write_pickle_only,
        transient_progress,
    )

    member_grid_count = sum(
''',
    '''    _write_owner_collection(
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
''',
)

# ---------------------------------------------------------------------------
# 5. Offline second-module initial/incremental annotation and FAST snapshot lifecycle
# ---------------------------------------------------------------------------
core_path = "system/model/map_control/slurry_policy_model/slurry_policy_core.py"
source = read(core_path)
source = source.replace(
    "import pandas as pd\n\nfrom _engine.aggregator",
    "import pandas as pd\n\nfrom system.model.map_control.fast_change_mode import FastChangeHistoryManager\n\nfrom _engine.aggregator",
    1,
)
source = source.replace(
    '''        with recorder.measure("initial_input_alignment"):
            input_alignment = validate_input_frame_alignment(
                raw_df, condition_index, context="初次训练输入 CSV"
            )
''',
    '''        fast_manager = FastChangeHistoryManager()
        with recorder.measure("initial_fast_change_replay"):
            raw_df = fast_manager.annotate_dataframe(raw_df)
        fast_summary = fast_manager.run_summary(raw_df)
        with recorder.measure("initial_input_alignment"):
            input_alignment = validate_input_frame_alignment(
                raw_df, condition_index, context="初次训练输入 CSV"
            )
''',
    1,
)
source = source.replace(
    '''        alignment_cfg = training.get("version_alignment", {})
''',
    '''        effective["fast_change"] = {
            "checkpoint": fast_manager.export_checkpoint(),
            "summary": fast_summary,
        }
        alignment_cfg = training.get("version_alignment", {})
''',
    1,
)
source = source.replace(
    '''        progress.update(100.0, f"初次离线训练完成：{snapshot}", force=True)
        return snapshot
''',
    '''        fast_manager.publish_snapshot(
            version,
            source_paths=_source_paths(inputs),
            summary=fast_summary,
        )
        progress.update(100.0, f"初次离线训练完成：{snapshot}", force=True)
        return snapshot
''',
    1,
)
# Incremental: load previous checkpoint, annotate only new_df. Tail already carries old FAST labels.
source = source.replace(
    '''        with recorder.measure("incremental_prepare_new_raw_data"):
            new_df, warnings = prepare_raw_data(
                inputs, plant, training, progress=progress.child(20.0, 36.0)
            )
        with recorder.measure("incremental_input_alignment"):
''',
    '''        with recorder.measure("incremental_prepare_new_raw_data"):
            new_df, warnings = prepare_raw_data(
                inputs, plant, training, progress=progress.child(20.0, 36.0)
            )
        fast_manager = FastChangeHistoryManager()
        previous_fast = (previous_effective.get("fast_change") or {}).get("checkpoint")
        if not previous_fast:
            raise ConfigurationError(
                "上一版第二模块没有 FAST_CHANGE checkpoint。V4 首次升级必须重新执行初次训练。"
            )
        fast_manager.load_checkpoint(previous_fast)
        with recorder.measure("incremental_fast_change_replay"):
            new_df = fast_manager.annotate_dataframe(new_df)
        fast_summary = fast_manager.run_summary(new_df)
        with recorder.measure("incremental_input_alignment"):
''',
    1,
)
# The second occurrence of effective is incremental.
needle = '''        with recorder.measure("incremental_remap_new"):
            new_valid, new_valid_report, _ = remap_episode_conditions(
'''
insert = '''        effective["fast_change"] = {
            "checkpoint": fast_manager.export_checkpoint(),
            "summary": fast_summary,
        }
        with recorder.measure("incremental_remap_new"):
            new_valid, new_valid_report, _ = remap_episode_conditions(
'''
if needle not in source:
    raise RuntimeError("incremental effective insertion point missing")
source = source.replace(needle, insert, 1)
source = source.replace(
    '''        progress.update(100.0, f"增量离线训练完成：{snapshot}", force=True)
        return snapshot
''',
    '''        fast_manager.publish_snapshot(
            target_version,
            source_paths=_source_paths(inputs),
            summary=fast_summary,
        )
        progress.update(100.0, f"增量离线训练完成：{snapshot}", force=True)
        return snapshot
''',
    1,
)
# Disturbance no longer belongs to second-module event signature.
source = source.replace('        "disturbance": copy.deepcopy(training.get("disturbance", {})),\n', '')
write(core_path, source)

# ---------------------------------------------------------------------------
# 6. Online integrated pipeline computes FAST before condition stabilization
# ---------------------------------------------------------------------------
online_condition = "system/model/map_control/condition_model/online_condition_classifier.py"
source = read(online_condition)
source = source.replace(
    '''from system.model.map_control.condition_model.integrated_version_manager import (
    IntegratedVersionError,
    IntegratedVersionManager,
    IntegratedVersionPointer,
)
''',
    '''from system.model.map_control.condition_model.integrated_version_manager import (
    IntegratedVersionError,
    IntegratedVersionManager,
    IntegratedVersionPointer,
)
from system.model.map_control.fast_change_mode import FastChangeHistoryManager
''',
    1,
)
source = source.replace(
    '''        self.classifier = OnlineConditionClassifier(config, snapshot)
        self.version_manager = version_manager
''',
    '''        self.classifier = OnlineConditionClassifier(config, snapshot)
        # FAST detector is independent from condition/policy model hot reload and therefore
        # keeps its short-window/runtime state across integrated model version switches.
        self.fast_change_manager = FastChangeHistoryManager(persist_runtime=True)
        self.version_manager = version_manager
''',
    1,
)
old_process = '''            original = dict(realtime)
            condition_result = self.classifier.classify(original)
'''
new_process = '''            original = dict(realtime)
            # FAST must be evaluated before condition majority stabilization.  It is an
            # upstream disturbance fact, not a second-module internal classifier.
            fast_context = self.fast_change_manager.evaluate_online(original, target=target)
            original = _preserving_update(original, fast_context)
            condition_result = self.classifier.classify(original)
'''
if old_process not in source:
    raise RuntimeError("online pipeline process block missing")
source = source.replace(old_process, new_process, 1)
source = source.replace(
    '''                "slurry_policy": self.policy_bridge.status(),
            }
''',
    '''                "slurry_policy": self.policy_bridge.status(),
                "fast_change": self.fast_change_manager.status(),
            }
''',
    1,
)
write(online_condition, source)

# ---------------------------------------------------------------------------
# 7. Online second module: consume fast context, exact+direction pool, action envelope
# ---------------------------------------------------------------------------
fast_adapter = r'''from __future__ import annotations

import json
from typing import Any, Dict


class FastContextError(ValueError):
    pass


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def extract_fast_context(process: Dict[str, Any]) -> Dict[str, Any]:
    required = (
        "fast_change_mode",
        "fast_change_direction",
        "fast_change_exact_trend_mode",
        "fast_change_effect_risk_level",
        "fast_change_overall_risk_level",
        "fast_change_outlet_so2_rate",
    )
    missing = [name for name in required if name not in process]
    if missing:
        raise FastContextError(
            "实时输入缺少上游 fast_change_mode 结果: %s" % missing
        )
    context = {key: value for key, value in process.items() if str(key).startswith("fast_change_")}
    context["fast_change_axis_rates"] = _mapping(process.get("fast_change_axis_rates"))
    return context
'''
write("system/model/map_control/slurry_policy_model/slurry_policy_online/fast_context_adapter.py", fast_adapter)

fast_envelope = r'''from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .demand_analyzer import MAGNITUDE_ORDER
from .types import ControlDemand


@dataclass
class FastActionEnvelope:
    control_mode: str
    fast_direction: str
    allowed_slurry_directions: List[str]
    acceptable_effect_directions: List[str]
    maximum_action_magnitude: str
    preferred_effect_direction: str
    allow_preemptive_increase: bool = False
    risk_escalation: bool = False
    reason_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _max_magnitude(left: str, right: str) -> str:
    return max(
        (str(left).upper(), str(right).upper()),
        key=lambda value: MAGNITUDE_ORDER.get(value, 0),
    )


def build_fast_action_envelope(
    fast: Dict[str, Any], demand: ControlDemand, online: dict
) -> FastActionEnvelope:
    cfg = online.get("fast_policy", {})
    mode = str(fast.get("fast_change_mode", "REGULAR")).upper()
    direction = str(fast.get("fast_change_direction", "NONE")).upper()
    effect_state = str(fast.get("fast_change_effect_state", "UNKNOWN")).upper()
    effect_risk = str(fast.get("fast_change_effect_risk_level", "LOW")).upper()
    outlet_trend = str(fast.get("fast_change_outlet_so2_trend", "STABLE")).upper()
    exact = str(fast.get("fast_change_exact_trend_mode", "STEADY")).upper()
    reasons: List[str] = []

    acceptable = list(demand.acceptable_effect_directions)
    allowed_slurry = ["HOLD", "INCREASE", "DECREASE"]
    maximum = str(demand.maximum_action_magnitude).upper()
    preferred = acceptable[0] if acceptable else "NEUTRAL"
    allow_preemptive = False
    risk_escalation = effect_risk in {"HIGH", "EMERGENCY"} or outlet_trend == "RISING_FAST"

    if demand.safety_level in {"WARNING", "EMERGENCY"}:
        allowed_slurry = ["HOLD", "INCREASE"]
        preferred = "DECREASE"
        if "DECREASE" not in acceptable:
            acceptable.insert(0, "DECREASE")
        reasons.append("FAST_ENVELOPE_EMISSION_GUARD")
    elif mode == "FAST_CHANGE":
        allowed_slurry = ["HOLD", "INCREASE"]
        if direction == "RISE":
            reasons.append("FAST_RISE_BLOCKS_ECONOMIC_DECREASE")
            allow_preemptive = bool(cfg.get("allow_preemptive_increase", True))
            if effect_state in {"ABOVE_TARGET", "ABOVE_TARGET_FAR"}:
                maximum = _max_magnitude(maximum, "SMALL" if effect_state == "ABOVE_TARGET" else "MEDIUM")
                preferred = "DECREASE"
            elif effect_state == "TARGET_BAND" and allow_preemptive:
                cap = str(cfg.get("target_band_preemptive_max_magnitude", "SMALL")).upper()
                if outlet_trend == "RISING_FAST" and "AND" in exact:
                    cap = str(cfg.get("combined_rise_max_magnitude", "MEDIUM")).upper()
                maximum = _max_magnitude(maximum, cap)
                if "DECREASE" not in acceptable:
                    acceptable.append("DECREASE")
                if outlet_trend in {"RISING", "RISING_FAST"} or "AND" in exact:
                    acceptable = ["DECREASE"] + [x for x in acceptable if x != "DECREASE"]
                    preferred = "DECREASE"
                    reasons.append("FAST_RISE_PREEMPTIVE_INCREASE_PREFERRED")
            elif effect_state in {"BELOW_TARGET", "BELOW_TARGET_FAR"}:
                if allow_preemptive and (outlet_trend in {"RISING", "RISING_FAST"} or "AND" in exact):
                    maximum = _max_magnitude(maximum, "SMALL")
                    if "DECREASE" not in acceptable:
                        acceptable.append("DECREASE")
                    preferred = "DECREASE" if outlet_trend == "RISING_FAST" else preferred
                    reasons.append("FAST_RISE_LOW_SO2_PROTECTIVE_OPTION")
                else:
                    maximum = "HOLD"
                    acceptable = ["NEUTRAL"]
                    preferred = "NEUTRAL"
        elif direction == "DROP":
            reasons.append("FAST_DROP_HOLDS_ECONOMIC_DECREASE")
            if effect_state in {"BELOW_TARGET", "BELOW_TARGET_FAR", "TARGET_BAND"}:
                maximum = "HOLD"
                acceptable = ["NEUTRAL"]
                preferred = "NEUTRAL"
        else:
            reasons.append("FAST_MIXED_CONSERVATIVE")
            if effect_state in {"BELOW_TARGET", "BELOW_TARGET_FAR", "TARGET_BAND"}:
                maximum = "HOLD"
                acceptable = ["NEUTRAL"]
                preferred = "NEUTRAL"
    elif mode == "FAST_RECOVERY":
        if direction == "DROP" and effect_state in {"BELOW_TARGET", "BELOW_TARGET_FAR"} and outlet_trend in {"STABLE", "FALLING", "FALLING_FAST"}:
            allowed_slurry = ["HOLD", "DECREASE", "INCREASE"]
            cap = str(cfg.get("recovery_drop_max_decrease_magnitude", "SMALL")).upper()
            maximum = cap if MAGNITUDE_ORDER.get(maximum, 0) > MAGNITUDE_ORDER.get(cap, 0) else maximum
            reasons.append("FAST_DROP_RECOVERY_ECONOMIC_DECREASE_ALLOWED")
        else:
            allowed_slurry = ["HOLD", "INCREASE"]
            if effect_state in {"TARGET_BAND", "BELOW_TARGET", "BELOW_TARGET_FAR"}:
                maximum = "HOLD"
                acceptable = ["NEUTRAL"]
            reasons.append("FAST_RECOVERY_CONSERVATIVE")

    acceptable = list(dict.fromkeys(acceptable))
    return FastActionEnvelope(
        control_mode=mode,
        fast_direction=direction,
        allowed_slurry_directions=allowed_slurry,
        acceptable_effect_directions=acceptable,
        maximum_action_magnitude=maximum,
        preferred_effect_direction=preferred,
        allow_preemptive_increase=allow_preemptive,
        risk_escalation=risk_escalation,
        reason_codes=reasons,
    )


def apply_fast_action_envelope(demand: ControlDemand, envelope: FastActionEnvelope) -> ControlDemand:
    return ControlDemand(
        commanded_target=demand.commanded_target,
        effective_target=demand.effective_target,
        current_so2=demand.current_so2,
        error=demand.error,
        demand_level=demand.demand_level,
        desired_so2_response=demand.desired_so2_response,
        acceptable_effect_directions=list(envelope.acceptable_effect_directions),
        maximum_action_magnitude=envelope.maximum_action_magnitude,
        safety_level=demand.safety_level,
        target_changed=demand.target_changed,
        reason_codes=list(demand.reason_codes) + list(envelope.reason_codes),
    )
'''
write("system/model/map_control/slurry_policy_model/slurry_policy_online/fast_action_envelope.py", fast_envelope)

# RealtimeState keeps backward-compatible rate attributes but now also owns upstream fast_context.
types_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/types.py"
replace_once(
    types_path,
    '''    control_mode: str
    policy_state_key: str
''',
    '''    control_mode: str
    fast_context: Dict[str, Any]
    policy_state_key: str
''',
)

realtime_builder = r'''from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

try:
    from _engine.config_loader import enabled_towers
    from _engine.schema import OUTLET_SO2_COLUMN, condition_axis_columns
    from _engine.state_builder import build_policy_state
except ImportError:  # pragma: no cover
    from .._engine.config_loader import enabled_towers
    from .._engine.schema import OUTLET_SO2_COLUMN, condition_axis_columns
    from .._engine.state_builder import build_policy_state

from .types import ConditionContext, RealtimeState


class RealtimeDataError(ValueError):
    pass


def _number(data: Dict[str, Any], key: str, required: bool = True) -> float:
    value = data.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if required and not np.isfinite(number):
        raise RealtimeDataError("实时字段缺失或不是有限数值: %s" % key)
    return number


class RealtimeStateBuilder:
    def __init__(self, plant: dict, training: dict) -> None:
        self.plant = plant
        self.training = training

    def validate_and_build(
        self,
        timestamp: pd.Timestamp,
        process: Dict[str, Any],
        condition: ConditionContext,
        fast_context: Dict[str, Any],
    ) -> RealtimeState:
        outlet = _number(process, OUTLET_SO2_COLUMN)
        axes = condition_axis_columns(self.training)
        rates = dict(fast_context.get("fast_change_axis_rates") or {})
        first_rate = float(rates.get(axes[0], 0.0) or 0.0)
        second_rate = float(rates.get(axes[1], 0.0) or 0.0) if len(axes) > 1 else 0.0
        outlet_rate = float(fast_context.get("fast_change_outlet_so2_rate", 0.0) or 0.0)
        disturbance_mode = str(fast_context.get("fast_change_exact_trend_mode", "STEADY"))
        control_mode = str(fast_context.get("fast_change_mode", "REGULAR"))

        row: Dict[str, Any] = {
            "anchor_grid_id": condition.raw_grid_id,
            "condition_state_key": condition.state_key,
            "before_outlet_so2": outlet,
            "before_outlet_so2_rate": outlet_rate,
            "disturbance_mode": disturbance_mode,
        }
        for tower in enabled_towers(self.plant):
            tower_id = str(tower["tower_id"])
            row["before_ph__%s" % tower_id] = _number(process, str(tower["ph_column"]))
            for valve in tower.get("valves", []):
                valve_id = str(valve["valve_id"])
                row["before_valve__%s" % valve_id] = _number(process, str(valve["column"]))
        policy_state, no_grid = build_policy_state(row, self.plant, self.training)
        return RealtimeState(
            timestamp=timestamp.isoformat(),
            condition=condition,
            process=dict(process),
            load_rate=first_rate,
            inlet_so2_rate=second_rate,
            outlet_so2_rate=outlet_rate,
            disturbance_mode=disturbance_mode,
            control_mode=control_mode,
            fast_context=dict(fast_context),
            policy_state_key=policy_state,
            policy_state_key_no_grid=no_grid,
        )
'''
write("system/model/map_control/slurry_policy_model/slurry_policy_online/realtime_state_builder.py", realtime_builder)

# Candidate retriever: exact transient -> direction pool -> FAST rule.
retriever_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/candidate_retriever.py"
source = read(retriever_path)
source = source.replace(
    '''SOURCE_PRIORITY = {
    "TRANSIENT": 4,
    "LOCAL_CONDITION": 4,
''',
    '''SOURCE_PRIORITY = {
    "TRANSIENT_EXACT": 5,
    "TRANSIENT_DIRECTION_POOL": 4,
    "LOCAL_CONDITION": 4,
''',
    1,
)
source = source.replace('    "RULE_BASELINE": 1,\n}', '    "FAST_RULE_BASELINE": 2,\n    "RULE_BASELINE": 1,\n}', 1)
source = source.replace(
    '''    def transient(self, state: RealtimeState) -> List[Candidate]:
        states = self.loader.load_transient(state.disturbance_mode)
        actions = self._actions_for_state(states, state.policy_state_key_no_grid)
        return self._wrap("TRANSIENT", state.disturbance_mode, state.policy_state_key_no_grid, actions)
''',
    '''    def transient(self, state: RealtimeState) -> List[Candidate]:
        states = self.loader.load_transient(state.disturbance_mode)
        actions = self._actions_for_state(states, state.policy_state_key_no_grid)
        return self._wrap("TRANSIENT_EXACT", state.disturbance_mode, state.policy_state_key_no_grid, actions)

    def transient_direction(self, state: RealtimeState) -> List[Candidate]:
        direction = str(state.fast_context.get("fast_change_direction", "NONE"))
        states = self.loader.load_transient_direction(direction)
        actions = self._actions_for_state(states, state.policy_state_key_no_grid)
        return self._wrap("TRANSIENT_DIRECTION_POOL", direction, state.policy_state_key_no_grid, actions)
''',
    1,
)
source = source.replace(
    '''    def rule(
        self,
        demand: ControlDemand,
        state: RealtimeState,
        preferred_effect_direction: str = "",
    ) -> Candidate:
''',
    '''    def rule(
        self,
        demand: ControlDemand,
        state: RealtimeState,
        preferred_effect_direction: str = "",
        source: str = "RULE_BASELINE",
    ) -> Candidate:
''',
    1,
)
source = source.replace(
    '''            source="RULE_BASELINE",
            owner_id="RULE",
''',
    '''            source=source,
            owner_id="FAST_RULE" if source == "FAST_RULE_BASELINE" else "RULE",
''',
    1,
)
source = source.replace('            source_priority=SOURCE_PRIORITY["RULE_BASELINE"],', '            source_priority=SOURCE_PRIORITY[source],', 1)
write(retriever_path, source)

# Loader supports direction pool.
loader_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/policy_snapshot_loader.py"
source = read(loader_path)
source = source.replace(
    '''        self._transient_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._plant_prior''',
    '''        self._transient_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._transient_direction_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._plant_prior''',
    1,
)
source = source.replace(
    '''        self._transient_cache.clear()
        self._plant_prior = None
''',
    '''        self._transient_cache.clear()
        self._transient_direction_cache.clear()
        self._plant_prior = None
''',
    1,
)
marker = '''    def load_plant_prior(self) -> Dict[str, Any]:
'''
method = r'''    def load_transient_direction(self, fast_direction: str) -> Dict[str, Any]:
        direction = str(fast_direction)
        if direction in self._transient_direction_cache:
            value = self._transient_direction_cache[direction]
            self._transient_direction_cache.move_to_end(direction)
            return value
        if self.snapshot_dir is None:
            self.load_active()
        path = self.snapshot_dir / "transient_direction" / safe_name(direction) / "policy.pkl"
        states = self._read_pickle(path).get("state_action_profiles", {}) if path.exists() else {}
        self._put_lru(self._transient_direction_cache, direction, states)
        return states

'''
if marker not in source:
    raise RuntimeError("loader plant prior marker missing")
source = source.replace(marker, method + marker, 1)
write(loader_path, source)

# Candidate filter consumes envelope and special transient protection metrics.
filter_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/candidate_filter.py"
source = read(filter_path)
source = source.replace(
    '''        "TRANSIENT": "transient_allowed_status",
''',
    '''        "TRANSIENT_EXACT": "transient_allowed_status",
        "TRANSIENT_DIRECTION_POOL": "transient_allowed_status",
''',
    1,
)
source = source.replace(
    '''        stability_context: Dict[str, Any],
    ) -> Tuple[List[Candidate], Dict[str, List[str]]]:
''',
    '''        stability_context: Dict[str, Any],
        fast_envelope: Any | None = None,
    ) -> Tuple[List[Candidate], Dict[str, List[str]]]:
''',
    1,
)
source = source.replace(
    '''            reasons = self._reasons(candidate, state, demand, execution_context, stability_context)
''',
    '''            reasons = self._reasons(candidate, state, demand, execution_context, stability_context, fast_envelope)
''',
    1,
)
source = source.replace(
    '''        stability: Dict[str, Any],
    ) -> List[str]:
''',
    '''        stability: Dict[str, Any],
        fast_envelope: Any | None = None,
    ) -> List[str]:
''',
    1,
)
old_direction_check = '''            so2_effect = profile.get("so2_effect", {})
            if str(so2_effect.get("dominant_direction", "UNKNOWN")) not in demand.acceptable_effect_directions:
                reasons.append("SO2_EFFECT_DIRECTION_MISMATCH")
            if float(so2_effect.get("direction_consistency", 0.0)) < float(acceptance["minimum_direction_consistency"]):
                reasons.append("DIRECTION_CONSISTENCY_TOO_LOW")
'''
new_direction_check = '''            so2_effect = profile.get("so2_effect", {})
            is_transient = candidate.source in {"TRANSIENT_EXACT", "TRANSIENT_DIRECTION_POOL"}
            transient_effect = profile.get("transient_effect", {}) or {}
            protective_ok = False
            if is_transient and fast_envelope is not None:
                safe_ratio = transient_effect.get("mean_safe_ratio")
                rate_reduction = (transient_effect.get("outlet_so2_rate_reduction", {}) or {}).get("median")
                try:
                    safe_ratio = float(safe_ratio)
                except (TypeError, ValueError):
                    safe_ratio = 0.0
                try:
                    rate_reduction = float(rate_reduction)
                except (TypeError, ValueError):
                    rate_reduction = -999.0
                fast_cfg = self.online.get("fast_policy", {})
                protective_ok = bool(
                    str(state.fast_context.get("fast_change_direction", "NONE")) == "RISE"
                    and direction in set(fast_envelope.allowed_slurry_directions)
                    and safe_ratio >= float(fast_cfg.get("minimum_transient_safe_ratio", 0.85))
                    and rate_reduction >= float(fast_cfg.get("minimum_transient_rate_reduction", -0.10))
                )
                candidate.evaluation["transient_protective_ok"] = protective_ok
            if (
                str(so2_effect.get("dominant_direction", "UNKNOWN"))
                not in demand.acceptable_effect_directions
                and not protective_ok
            ):
                reasons.append("SO2_EFFECT_DIRECTION_MISMATCH")
            if (
                float(so2_effect.get("direction_consistency", 0.0))
                < float(acceptance["minimum_direction_consistency"])
                and not protective_ok
            ):
                reasons.append("DIRECTION_CONSISTENCY_TOO_LOW")
'''
if old_direction_check not in source:
    raise RuntimeError("candidate filter direction block missing")
source = source.replace(old_direction_check, new_direction_check, 1)
# Replace generic fast decrease block with envelope allowed-directions gate.
old_fast_block = '''        if (
            direction == "DECREASE"
            and state.control_mode == "FAST_CHANGE"
            and bool(self.online["fast_mode"].get("block_economic_slurry_decrease", True))
        ):
            reasons.append("SLURRY_DECREASE_BLOCKED_IN_FAST_MODE")
'''
new_fast_block = '''        if fast_envelope is not None and direction not in set(fast_envelope.allowed_slurry_directions):
            reasons.append("ACTION_DIRECTION_BLOCKED_BY_FAST_ENVELOPE")
'''
if old_fast_block not in source:
    raise RuntimeError("old fast decrease block missing")
source = source.replace(old_fast_block, new_fast_block, 1)
write(filter_path, source)

# Ranker puts FAST protection metrics before target-delta matching for transient evidence.
ranker_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/candidate_ranker.py"
source = read(ranker_path)
old_key = '''            candidate.rank_key = (
                effect_priority.get(effect, 0),
                float(target_match_score),
                -abs(float(residual_error)),
                float(reliability.get("safety_history_score", 0.0)),
'''
new_key = '''            transient = profile.get("transient_effect", {}) or {}
            safe_ratio = transient.get("mean_safe_ratio")
            rate_reduction = (transient.get("outlet_so2_rate_reduction", {}) or {}).get("median")
            try:
                safe_ratio_value = float(safe_ratio)
            except (TypeError, ValueError):
                safe_ratio_value = -1.0
            try:
                rate_reduction_value = float(rate_reduction)
            except (TypeError, ValueError):
                rate_reduction_value = -999.0
            transient_priority = 1 if candidate.source in {"TRANSIENT_EXACT", "TRANSIENT_DIRECTION_POOL"} else 0
            candidate.rank_key = (
                effect_priority.get(effect, 0),
                transient_priority,
                safe_ratio_value,
                rate_reduction_value,
                float(target_match_score),
                -abs(float(residual_error)),
                float(reliability.get("safety_history_score", 0.0)),
'''
if old_key not in source:
    raise RuntimeError("rank key block missing")
source = source.replace(old_key, new_key, 1)
write(ranker_path, source)

# Decision state machine allows limited FAST risk escalation while waiting for previous effect.
sm_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/decision_state_machine.py"
source = read(sm_path)
source = source.replace(
    '''    def blocking_reasons(self, now: pd.Timestamp, safety_level: str) -> List[str]:
        if safety_level == "EMERGENCY":
            return []
        reasons: List[str] = []
''',
    '''    def blocking_reasons(
        self,
        now: pd.Timestamp,
        safety_level: str,
        fast_context: Dict[str, Any] | None = None,
    ) -> List[str]:
        if safety_level == "EMERGENCY":
            return []
        fast_context = dict(fast_context or {})
        fast_cfg = self.regular_config.get("fast_policy", {})
        # fast_policy lives at ONLINE_POLICY_CONFIG top level; OnlineSlurryPolicy also
        # passes an explicit flag in fast_context for backward-safe lookup below.
        allow_escalation = bool(fast_context.get("_allow_waiting_effect_risk_escalation", False))
        risk_escalation = bool(fast_context.get("_risk_escalation", False))
        escalation_active = allow_escalation and risk_escalation
        reasons: List[str] = []
''',
    1,
)
source = source.replace(
    '''        if (
            self.state.get("state") in {"WAITING_EFFECT", "EVALUATING_EFFECT"}
            and bool(self.config.get("block_normal_actions_while_waiting_effect", True))
        ):
            reasons.append("WAITING_PREVIOUS_ACTION_EFFECT")
''',
    '''        if (
            self.state.get("state") in {"WAITING_EFFECT", "EVALUATING_EFFECT"}
            and bool(self.config.get("block_normal_actions_while_waiting_effect", True))
            and not escalation_active
        ):
            reasons.append("WAITING_PREVIOUS_ACTION_EFFECT")
''',
    1,
)
source = source.replace(
    '''            if elapsed < float(self.config["minimum_action_interval_minutes"]):
                reasons.append("MINIMUM_ACTION_INTERVAL_ACTIVE")
''',
    '''            interval = float(self.config["minimum_action_interval_minutes"])
            if escalation_active:
                interval = min(
                    interval,
                    float(fast_context.get("_risk_escalation_minimum_action_interval_minutes", interval)),
                )
            if elapsed < interval:
                reasons.append("MINIMUM_ACTION_INTERVAL_ACTIVE")
''',
    1,
)
write(sm_path, source)

# Online config: remove fast detector state-machine params, keep only second-module FAST action policy.
config_path = "system/model/map_control/slurry_policy_model/slurry_policy_config.py"
source = read(config_path)
start = source.find('    # ------------------------------------------------------------------------\n    # FAST_CHANGE 快变场景的进入保持与退出恢复策略。\n')
end = source.find('    # ------------------------------------------------------------------------\n    # 塔级动作最终转换为实际阀门指令时的硬限幅。\n', start)
if start < 0 or end < 0:
    raise RuntimeError("online fast_mode block missing")
fast_policy_block = '''    # ------------------------------------------------------------------------
    # FAST_CHANGE 动作策略。FAST 的识别/状态机参数不在这里，统一由
    # fast_change_mode/fast_change_config.py 管理；这里仅定义第二模块如何消费 FAST。
    # ------------------------------------------------------------------------
    "fast_policy": {
        "transient_exact_enabled": True,
        "transient_direction_pool_enabled": True,
        "allow_regular_policy_fallback": False,
        "allow_preemptive_increase": True,
        "target_band_preemptive_max_magnitude": "SMALL",
        "combined_rise_max_magnitude": "MEDIUM",
        "recovery_drop_max_decrease_magnitude": "SMALL",
        # FAST_RISE 历史动作即使净烟气绝对值仍上涨，只要安全且上涨速度被明显压制，
        # 仍可作为有效保护经验进入候选。
        "minimum_transient_safe_ratio": 0.85,
        "minimum_transient_rate_reduction": -0.10,
        # FAST 风险继续升级时，允许突破普通 WAITING_EFFECT，但仍保留执行反馈、
        # 反向锁和每小时动作次数等硬节流。
        "allow_waiting_effect_risk_escalation": True,
        "risk_escalation_minimum_action_interval_minutes": 1.0,
    },

'''
source = source[:start] + fast_policy_block + source[end:]
write(config_path, source)

# Online config validator no longer validates fast_mode state machine.
online_cfg_loader = "system/model/map_control/slurry_policy_model/slurry_policy_online/config_loader.py"
source = read(online_cfg_loader)
source = source.replace(
    '''    fast = online.get("fast_mode", {})
    if int(fast.get("exit_stable_cycles", 0)) < 1:
        raise OnlineConfigurationError("fast_mode.exit_stable_cycles 必须至少为1")

''',
    '''    fast = online.get("fast_policy", {})
    if float(fast.get("minimum_transient_safe_ratio", 0.0)) < 0 or float(
        fast.get("minimum_transient_safe_ratio", 0.0)
    ) > 1:
        raise OnlineConfigurationError("fast_policy.minimum_transient_safe_ratio 必须位于 [0,1]")

''',
    1,
)
write(online_cfg_loader, source)

# Main online policy consumes upstream context and envelope; no internal DisturbanceMonitor.
online_policy = "system/model/map_control/slurry_policy_model/slurry_policy_online/online_slurry_policy.py"
source = read(online_policy)
source = source.replace("from .disturbance_monitor import DisturbanceMonitor\n", "")
source = source.replace(
    '''from .demand_analyzer import analyze_demand
''',
    '''from .demand_analyzer import analyze_demand
from .fast_action_envelope import apply_fast_action_envelope, build_fast_action_envelope
from .fast_context_adapter import FastContextError, extract_fast_context
''',
    1,
)
source = source.replace(
    '''        self.state_builder = RealtimeStateBuilder(plant, training)
        self.disturbance = DisturbanceMonitor(
            self.loader.effective_disturbance, self.online, self.store.state
        )
        self.target_manager = TargetManager(self.online, self.store.state)
''',
    '''        self.state_builder = RealtimeStateBuilder(plant, training)
        self.target_manager = TargetManager(self.online, self.store.state)
''',
    1,
)
# Candidate sources method.
old_sources = '''    def _candidate_sources(
        self, state: RealtimeState
    ) -> List[Tuple[str, Any]]:
        if state.control_mode == "FAST_CHANGE":
            sources: List[Tuple[str, Any]] = [
                ("TRANSIENT", lambda: self.retriever.transient(state))
            ]
            if bool(
                self.online["fast_mode"].get(
                    "allow_regular_policy_fallback", False
                )
            ):
                sources.extend(
                    [
                        (
                            "LOCAL_CONDITION",
                            lambda: self.retriever.local(state),
                        ),
                        (
                            "NEIGHBOR_STATE",
                            lambda: self.retriever.neighbor(state),
                        ),
                        ("PLANT_ACTION_PRIOR", self.retriever.plant_prior),
                    ]
                )
            sources.append(("RULE_BASELINE", None))
            return sources
        return [
'''
new_sources = '''    def _candidate_sources(
        self, state: RealtimeState
    ) -> List[Tuple[str, Any]]:
        fast_cfg = self.online.get("fast_policy", {})
        if state.control_mode == "FAST_CHANGE":
            # condition 尚未稳定时仍允许 FAST 安全保护，但只使用规则基线，避免
            # 在工况归属尚未稳定时读取局部/历史精细策略。
            if not state.condition.condition_stable:
                return [("FAST_RULE_BASELINE", None)]
            sources: List[Tuple[str, Any]] = []
            if bool(fast_cfg.get("transient_exact_enabled", True)):
                sources.append(("TRANSIENT_EXACT", lambda: self.retriever.transient(state)))
            if bool(fast_cfg.get("transient_direction_pool_enabled", True)):
                sources.append(("TRANSIENT_DIRECTION_POOL", lambda: self.retriever.transient_direction(state)))
            if bool(fast_cfg.get("allow_regular_policy_fallback", False)):
                sources.extend([
                    ("LOCAL_CONDITION", lambda: self.retriever.local(state)),
                    ("NEIGHBOR_STATE", lambda: self.retriever.neighbor(state)),
                    ("PLANT_ACTION_PRIOR", self.retriever.plant_prior),
                ])
            sources.append(("FAST_RULE_BASELINE", None))
            return sources
        if state.control_mode == "FAST_RECOVERY":
            return [
                ("TRANSIENT_DIRECTION_POOL", lambda: self.retriever.transient_direction(state)),
                ("FAST_RULE_BASELINE", None),
            ]
        return [
'''
if old_sources not in source:
    raise RuntimeError("candidate sources old block missing")
source = source.replace(old_sources, new_sources, 1)
# Remove early condition_not_stable return; keep invalid block and version check.
early = '''            if not condition.condition_stable:
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "INITIALIZING",
                    "INITIALIZING",
                    "UNKNOWN",
                    reload_reasons + ["CONDITION_NOT_STABLE"],
                )
'''
if early not in source:
    raise RuntimeError("condition stable early block missing")
source = source.replace(early, "", 1)
# Replace disturbance computation inside try.
old_disturbance = '''                axes = condition_axis_columns(self.training)
                first_axis_value = float(process[axes[0]])
                second_axis_value = (
                    float(process[axes[1]]) if len(axes) > 1 else None
                )
                outlet = float(process[OUTLET_SO2_COLUMN])
                disturbance = self.disturbance.update(
                    timestamp,
                    first_axis_value,
                    second_axis_value,
                    outlet,
                )
                state = self.state_builder.validate_and_build(
                    timestamp, process, condition, disturbance
                )
'''
new_disturbance = '''                axes = condition_axis_columns(self.training)
                outlet = float(process[OUTLET_SO2_COLUMN])
                fast_context = extract_fast_context(process)
                state = self.state_builder.validate_and_build(
                    timestamp, process, condition, fast_context
                )
'''
if old_disturbance not in source:
    raise RuntimeError("online disturbance calculation block missing")
source = source.replace(old_disturbance, new_disturbance, 1)
# Add FastContextError to except tuple.
source = source.replace(
    '''                RealtimeDataError,
                TargetError,
''',
    '''                RealtimeDataError,
                FastContextError,
                TargetError,
''',
    1,
)
# Build envelope after demand.
needle = '''                demand = analyze_demand(
                    outlet,
                    commanded,
                    effective,
                    target_changed,
                    self.plant,
                    self.online,
                )
'''
replacement = needle + '''                fast_envelope = build_fast_action_envelope(
                    fast_context, demand, self.online
                )
                demand = apply_fast_action_envelope(demand, fast_envelope)
'''
if needle not in source:
    raise RuntimeError("demand block missing")
source = source.replace(needle, replacement, 1)
# Common reasons used old disturbance. Replace.
source = source.replace(
    '''                + list(disturbance.get("reason_codes", []))
                + demand.reason_codes
''',
    '''                + list(fast_context.get("fast_change_reason_codes", []))
                + demand.reason_codes
''',
    1,
)
# After common_reasons, hold regular warmup only. Find next model reload if.
marker = '''            if (
                "MODEL_VERSION_RELOADED" in reload_reasons
'''
warmup = '''            if (
                not condition.condition_stable
                and state.control_mode != "FAST_CHANGE"
                and demand.safety_level != "EMERGENCY"
            ):
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "INITIALIZING",
                    state.control_mode,
                    state.disturbance_mode,
                    common_reasons + ["CONDITION_NOT_STABLE"],
                    demand,
                )
            if not condition.condition_stable and state.control_mode == "FAST_CHANGE":
                common_reasons.append("FAST_PROTECTION_DURING_CONDITION_WARMUP")

'''
if marker not in source:
    raise RuntimeError("model reload marker missing")
source = source.replace(marker, warmup + marker, 1)
# Remove unconditional FAST_RECOVERY HOLD block.
recovery_hold = '''            if (
                state.control_mode == "FAST_RECOVERY"
                and demand.safety_level != "EMERGENCY"
            ):
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "HOLD",
                    "FAST_RECOVERY",
                    state.disturbance_mode,
                    common_reasons + ["FAST_RECOVERY_HOLD"],
                    demand,
                )

'''
if recovery_hold not in source:
    raise RuntimeError("old recovery hold block missing")
source = source.replace(recovery_hold, "", 1)
# blocking reasons now receives FAST risk-escalation flags.
source = source.replace(
    '''            blocking = self.state_machine.blocking_reasons(
                timestamp, demand.safety_level
            )
''',
    '''            blocking_fast_context = dict(fast_context)
            blocking_fast_context["_allow_waiting_effect_risk_escalation"] = bool(
                self.online.get("fast_policy", {}).get(
                    "allow_waiting_effect_risk_escalation", True
                )
            )
            blocking_fast_context["_risk_escalation"] = bool(fast_envelope.risk_escalation)
            blocking_fast_context["_risk_escalation_minimum_action_interval_minutes"] = float(
                self.online.get("fast_policy", {}).get(
                    "risk_escalation_minimum_action_interval_minutes", 1.0
                )
            )
            blocking = self.state_machine.blocking_reasons(
                timestamp, demand.safety_level, blocking_fast_context
            )
''',
    1,
)
# Rule source identity and filter envelope.
source = source.replace(
    '''                            self.retriever.rule(
                                effect_demand, state, effect_direction
                            )
''',
    '''                            self.retriever.rule(
                                effect_demand,
                                state,
                                effect_direction,
                                source=(
                                    "FAST_RULE_BASELINE"
                                    if source_name == "FAST_RULE_BASELINE"
                                    else "RULE_BASELINE"
                                ),
                            )
''',
    1,
)
source = source.replace(
    '''                        stability_context,
                    )
''',
    '''                        stability_context,
                        fast_envelope,
                    )
''',
    1,
)
# Add envelope to debug and reason codes.
source = source.replace(
    '''                    "demand_level": demand.demand_level,
                    "candidate_rank_key": selected.rank_key,
''',
    '''                    "demand_level": demand.demand_level,
                    "fast_action_envelope": fast_envelope.to_dict(),
                    "candidate_rank_key": selected.rank_key,
''',
    1,
)
write(online_policy, source)
remove_file("system/model/map_control/slurry_policy_model/slurry_policy_online/disturbance_monitor.py")

# ---------------------------------------------------------------------------
# 8. Database stores the independent FAST context fields from integrated output
# ---------------------------------------------------------------------------
db_path = "system/model/config/database_schema.py"
replace_once(
    db_path,
    '''_CONDITION_RESULT_FIELDS = OrderedDict([
    ("condition_snapshot_version", "varchar(32)"),
''',
    '''_CONDITION_RESULT_FIELDS = OrderedDict([
    # 独立 FAST_CHANGE 上游上下文；复杂轴速率/原因使用 jsonb。
    ("fast_change_mode", "varchar(32)"),
    ("fast_change_active", "boolean"),
    ("fast_change_recovery_active", "boolean"),
    ("fast_change_raw_trigger", "boolean"),
    ("fast_change_direction", "varchar(32)"),
    ("fast_change_severity", "varchar(32)"),
    ("fast_change_exact_trend_mode", "varchar(96)"),
    ("fast_change_trend_risk_level", "varchar(32)"),
    ("fast_change_effect_risk_level", "varchar(32)"),
    ("fast_change_effect_state", "varchar(48)"),
    ("fast_change_overall_risk_level", "varchar(32)"),
    ("fast_change_axis_rates", "jsonb"),
    ("fast_change_trigger_axes", "jsonb"),
    ("fast_change_outlet_so2_rate", "float8"),
    ("fast_change_outlet_so2_trend", "varchar(32)"),
    ("fast_change_reason_codes", "jsonb"),
    ("condition_snapshot_version", "varchar(32)"),
''',
)

# ---------------------------------------------------------------------------
# 9. Documentation updates
# ---------------------------------------------------------------------------
readme = read("system/model/map_control/fast_change_mode/README.md")
readme += '''

## 9. 离线/在线生命周期与容量控制

FAST 模块不会长期保存一份不断膨胀的完整标注 CSV。

- 在线：每条数据只更新 detector 的短时间窗口，`runtime/checkpoint.json` 定期覆盖写；
- FAST 事件只在闭合后写入月度 JSONL 摘要，不保存每个普通采样点；
- 离线初次：历史数据按 `date` 排序后因果回放同一个 detector；
- 离线增量：读取上一版 checkpoint，只回放新增数据；
- 与第二模块一起发布时，FAST 使用同一个 `v###` 版本号，并滚动保留最近配置数量的快照；
- 第二模块训练过程中需要的逐行 FAST 标签只存在于本次训练 DataFrame/context tail 中，不额外永久复制原始 CSV。

因此“逐行调用同一个 FastChangeModeDetector”既用于真实在线，也用于离线历史回放；
离线逐行的目的，是严格模拟在线因果状态机，而不是永久积累逐行文件。

## 10. 与 slurry_policy_model 的关系

第二模块从 V4 开始不再拥有独立的 `DisturbanceMonitor`。FAST 唯一事实源为本模块：

```text
fast_change_mode
  -> FAST exact/direction/effect risk
  -> slurry_policy_model
     -> TRANSIENT_EXACT
     -> TRANSIENT_DIRECTION_POOL
     -> FAST_RULE_BASELINE
```

TRANSIENT 历史评价额外统计动作前后净烟气 SO2 变化率、变化率抑制量、响应期峰值、
安全时间占比以及 WARNING/EMERGENCY 时间占比。因此 FAST_RISE 时即使 SO2 绝对值仍有
上升，只要上涨速度被压制且过程保持安全，也不会简单按“SO2 没下降”判成无效动作。
'''
write("system/model/map_control/fast_change_mode/README.md", readme)

print("FAST integration refactor applied successfully")
