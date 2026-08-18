"""FAST_CHANGE 离线/在线统一历史回放与轻量生命周期管理。

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
from .fast_change_mode_detector import (
    FAST_CHANGE,
    FAST_RECOVERY,
    REGULAR,
    FastChangeConfigurationError,
    FastChangeModeDetector,
)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "slurry_policy_model" / "slurry_policy_model_output"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT  / "system" / "model" / "map_control" / "slurry_policy_model" / "fast_change_mode_runtime"

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
    "fast_change_state_advanced",
    "fast_change_input_guard_reason",
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
        self._runtime_checkpoint_reset_reason: Optional[str] = None
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

    @staticmethod
    def _guard_value_matches(value: Any, expected: Any) -> bool:
        if value is None:
            return expected is None
        try:
            left = float(value)
            right = float(expected)
            if pd.notna(left) and pd.notna(right):
                return left == right
        except (TypeError, ValueError):
            pass
        return str(value).strip() == str(expected).strip()

    def input_guard_reason(self, row: Mapping[str, Any]) -> Optional[str]:
        """返回阻断原因；None 表示该帧允许推进 FAST/condition 在线状态。"""
        guard = dict(self.config.get("input_guard") or {})
        if not bool(guard.get("enabled", True)):
            return None
        missing_is_valid = bool(guard.get("missing_field_is_valid", True))
        for field, invalid_values in dict(guard.get("invalid_field_values") or {}).items():
            if field not in row or row.get(field) in (None, ""):
                if missing_is_valid:
                    continue
                return f"FAST_INPUT_GUARD_MISSING_FIELD:{field}"
            value = row.get(field)
            for invalid in list(invalid_values or []):
                if self._guard_value_matches(value, invalid):
                    return f"FAST_INPUT_GUARD_INVALID_VALUE:{field}={value}"
        return None

    def blocked_online_context(
        self,
        row: Mapping[str, Any],
        *,
        target: Optional[Any] = None,
        reason: str,
    ) -> Dict[str, Any]:
        """返回不推进 detector 的冻结上下文，用于校验/无效实时帧。"""
        state = self.detector.get_state()
        mode = str(state.get("mode", REGULAR))
        direction = (
            str(state.get("last_fast_direction", "NONE"))
            if mode in {FAST_CHANGE, FAST_RECOVERY}
            else "NONE"
        )
        exact = (
            str(state.get("last_fast_exact_mode", "STEADY"))
            if mode in {FAST_CHANGE, FAST_RECOVERY}
            else "STEADY"
        )
        axes = [str(axis.get("column", "")) for axis in self.detector.axes]
        try:
            emission_limit = float(self.plant.get("outlet_so2_safe_range", [0.0, 35.0])[1])
        except Exception:
            emission_limit = 35.0
        try:
            target_value = float(target) if target not in (None, "") else None
        except (TypeError, ValueError):
            target_value = None
        return {
            "fast_change_mode": mode,
            "fast_change_active": mode == FAST_CHANGE,
            "fast_change_recovery_active": mode == FAST_RECOVERY,
            "fast_change_raw_trigger": False,
            "fast_change_direction": direction,
            "fast_change_severity": "BLOCKED",
            "fast_change_exact_trend_mode": exact,
            "fast_change_raw_exact_trend_mode": "STEADY",
            "fast_change_trend_risk_level": "UNKNOWN",
            "fast_change_effect_risk_level": "UNKNOWN",
            "fast_change_effect_state": "INPUT_BLOCKED",
            "fast_change_effect_direction": "UNKNOWN",
            "fast_change_overall_risk_level": "UNKNOWN",
            "fast_change_axis_columns": axes,
            "fast_change_axis_rates": {column: None for column in axes},
            "fast_change_axis_levels": {column: "INPUT_BLOCKED" for column in axes},
            "fast_change_axis_direction_ratios": {
                column: {"rise": None, "drop": None} for column in axes
            },
            "fast_change_trigger_axes": [],
            "fast_change_available_axis_count": 0,
            "fast_change_trend_ready": False,
            "fast_change_current_so2": None,
            "fast_change_target_so2": target_value,
            "fast_change_target_error": None,
            "fast_change_emission_limit": emission_limit,
            "fast_change_outlet_so2_rate": None,
            "fast_change_outlet_so2_trend": "UNKNOWN",
            "fast_change_input_valid": False,
            "fast_change_state_advanced": False,
            "fast_change_input_guard_reason": str(reason),
            "fast_change_reason_codes": [
                "FAST_INPUT_GUARD_BLOCKED",
                str(reason),
                "FAST_STATE_NOT_ADVANCED",
            ],
            "fast_change_state": state,
            "fast_change_debug": {"input_guard_reason": str(reason)},
        }

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

        # load_checkpoint() 之后只允许继续处理更晚的数据。若增量 CSV 与上一批
        # 时间重叠，直接失败而不是把 DEMA/状态机倒着重放。
        boundary = self.last_processed_timestamp()
        if self._sample_count > 0 and boundary is not None:
            first_time = pd.Timestamp(result[TIME_COLUMN].iloc[0])
            if first_time <= boundary:
                raise ValueError(
                    "FAST 增量数据必须严格晚于上一 checkpoint："
                    f"first={first_time.isoformat()} checkpoint={boundary.isoformat()}"
                )

        outputs: list[Dict[str, Any]] = []
        for row in result.to_dict(orient="records"):
            target = row.get(target_column)
            context = self.detector.evaluate(row, target=target)
            compact = {key: context.get(key) for key in FAST_CONTEXT_COLUMNS}
            outputs.append(compact)
            self._observe(context, timestamp=row.get(TIME_COLUMN))
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
        guard_reason = self.input_guard_reason(row)
        if guard_reason is not None:
            return self.blocked_online_context(
                row, target=target, reason=guard_reason
            )
        context = self.detector.evaluate(row, target=target)
        context["fast_change_state_advanced"] = True
        context["fast_change_input_guard_reason"] = ""
        closed = self._observe(context, timestamp=row.get(TIME_COLUMN))
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

    def _observe(
        self,
        context: Mapping[str, Any],
        *,
        timestamp: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        mode = str(context.get("fast_change_mode", REGULAR))
        state = dict(context.get("fast_change_state") or {})
        try:
            now = pd.Timestamp(timestamp).isoformat() if timestamp is not None else pd.Timestamp.now().isoformat()
        except Exception:
            now = pd.Timestamp.now().isoformat()
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
        if not path.exists():
            return
        try:
            self.load_checkpoint(_read_json(path))
        except (FastChangeConfigurationError, ValueError, TypeError, KeyError) as exc:
            # 在线部署修改 FAST 配置后，旧短窗口状态不应阻断服务启动；离线增量仍
            # 通过显式 load_checkpoint 严格拒绝语义变化。
            self.reset_runtime_state()
            self._runtime_checkpoint_reset_reason = "STALE_RUNTIME_CHECKPOINT_RESET:%s" % exc
            try:
                path.unlink()
            except OSError:
                pass

    def reset_runtime_state(self) -> None:
        self.detector.reset()
        self._sample_count = 0
        self._open_event = None
        self._completed_events = []

    def last_processed_timestamp(self) -> Optional[pd.Timestamp]:
        checkpoint = self.detector.export_checkpoint()
        timestamps = []
        for value in dict(checkpoint.get("series_state") or {}).values():
            if value.get("timestamp"):
                try:
                    timestamps.append(pd.Timestamp(value["timestamp"]))
                except Exception:
                    continue
        return max(timestamps) if timestamps else None

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
        self._cleanup_runtime_event_archives()

    def _cleanup_runtime_event_archives(self) -> None:
        keep = int(self.config.get("lifecycle", {}).get("runtime_event_months_to_keep", 24))
        if keep <= 0:
            return
        root = self.runtime_root / "events"
        if not root.exists():
            return
        files = sorted(root.glob("fast_events_????_??.jsonl"))
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass

    def status(self) -> Dict[str, Any]:
        return {
            "sample_count": int(self._sample_count),
            "open_event": self._open_event,
            "detector_state": self.detector.get_state(),
            "output_root": str(self.output_root),
            "runtime_root": str(self.runtime_root),
            "runtime_checkpoint_reset_reason": self._runtime_checkpoint_reset_reason,
        }
