from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    source = read(path)
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, got {count}: {old[:160]!r}")
    write(path, source.replace(old, new, 1))


# 1) Synthetic FAST rule candidates must not enter historical-profile acceptance checks.
replace_once(
    "system/model/map_control/slurry_policy_model/slurry_policy_online/candidate_filter.py",
    '        if candidate.source != "RULE_BASELINE":\n',
    '        if not candidate.synthetic:\n',
)

# 2) FAST risk escalation only relaxes WAITING_EFFECT while FAST/RECOVERY is active.
replace_once(
    "system/model/map_control/slurry_policy_model/slurry_policy_online/fast_action_envelope.py",
    '    risk_escalation = effect_risk in {"HIGH", "EMERGENCY"} or outlet_trend == "RISING_FAST"\n',
    '''    risk_escalation = bool(
        mode in {"FAST_CHANGE", "FAST_RECOVERY"}
        and (effect_risk in {"HIGH", "EMERGENCY"} or outlet_trend == "RISING_FAST")
    )
''',
)

# 3) FAST must not be neutralized by ordinary target/condition transition HOLD cycles.
online_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/online_slurry_policy.py"
replace_once(
    online_path,
    '''            if target_hold and demand.safety_level != "EMERGENCY":
                self.target_manager.consume_hold_cycle()
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "HOLD",
                    "TARGET_TRANSITION",
                    state.disturbance_mode,
                    common_reasons + ["TARGET_TRANSITION_HOLD"],
                    demand,
                )
''',
    '''            if target_hold and demand.safety_level != "EMERGENCY":
                self.target_manager.consume_hold_cycle()
                if state.control_mode != "FAST_CHANGE":
                    return self._make_hold(
                        timestamp,
                        condition,
                        process,
                        "HOLD",
                        "TARGET_TRANSITION",
                        state.disturbance_mode,
                        common_reasons + ["TARGET_TRANSITION_HOLD"],
                        demand,
                    )
                common_reasons.append("TARGET_TRANSITION_HOLD_BYPASSED_BY_FAST")
''',
)
replace_once(
    online_path,
    '''            if (
                self.state_machine.condition_hold_required()
                and demand.safety_level != "EMERGENCY"
            ):
                self.state_machine.consume_condition_hold()
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "HOLD",
                    "CONDITION_TRANSITION",
                    state.disturbance_mode,
                    common_reasons + ["CONDITION_JUST_SWITCHED"],
                    demand,
                )
''',
    '''            if (
                self.state_machine.condition_hold_required()
                and demand.safety_level != "EMERGENCY"
            ):
                self.state_machine.consume_condition_hold()
                if state.control_mode != "FAST_CHANGE":
                    return self._make_hold(
                        timestamp,
                        condition,
                        process,
                        "HOLD",
                        "CONDITION_TRANSITION",
                        state.disturbance_mode,
                        common_reasons + ["CONDITION_JUST_SWITCHED"],
                        demand,
                    )
                common_reasons.append("CONDITION_TRANSITION_HOLD_BYPASSED_BY_FAST")
''',
)

# 4) FAST lifecycle: actual row timestamp for event summaries, stale runtime checkpoint reset,
#    monotonic incremental continuation, and monthly event archive retention.
config_path = "system/model/map_control/fast_change_mode/fast_change_config.py"
replace_once(
    config_path,
    '''        # 在线是否持久化闭合 FAST 事件的月度 JSONL 摘要。
        "persist_compact_events": True,
''',
    '''        # 在线是否持久化闭合 FAST 事件的月度 JSONL 摘要。
        "persist_compact_events": True,
        # 在线 FAST 事件月度 JSONL 最多保留多少个月；<=0 表示不自动清理。
        "runtime_event_months_to_keep": 24,
''',
)

history_path = "system/model/map_control/fast_change_mode/fast_change_history_manager.py"
source = read(history_path)
source = source.replace(
    'from .fast_change_mode_detector import FAST_CHANGE, FAST_RECOVERY, REGULAR, FastChangeModeDetector\n',
    '''from .fast_change_mode_detector import (
    FAST_CHANGE,
    FAST_RECOVERY,
    REGULAR,
    FastChangeConfigurationError,
    FastChangeModeDetector,
)
''',
    1,
)
source = source.replace(
    '''        self._completed_events: list[Dict[str, Any]] = []
        if self.persist_runtime:
''',
    '''        self._completed_events: list[Dict[str, Any]] = []
        self._runtime_checkpoint_reset_reason: Optional[str] = None
        if self.persist_runtime:
''',
    1,
)
source = source.replace(
    '''        if sort_by_time and not result[TIME_COLUMN].is_monotonic_increasing:
            result.sort_values(TIME_COLUMN, inplace=True, kind="stable")
            result.reset_index(drop=True, inplace=True)

        outputs: list[Dict[str, Any]] = []
''',
    '''        if sort_by_time and not result[TIME_COLUMN].is_monotonic_increasing:
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
''',
    1,
)
source = source.replace(
    '''            outputs.append(compact)
            self._observe(context)
            self._sample_count += 1
''',
    '''            outputs.append(compact)
            self._observe(context, timestamp=row.get(TIME_COLUMN))
            self._sample_count += 1
''',
    1,
)
source = source.replace(
    '''        context = self.detector.evaluate(row, target=target)
        closed = self._observe(context)
''',
    '''        context = self.detector.evaluate(row, target=target)
        closed = self._observe(context, timestamp=row.get(TIME_COLUMN))
''',
    1,
)
source = source.replace(
    '''    def _observe(self, context: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        mode = str(context.get("fast_change_mode", REGULAR))
        state = dict(context.get("fast_change_state") or {})
        now = (
            state.get("last_fast_seen_at")
            or state.get("recovery_until")
            or pd.Timestamp.now().isoformat()
        )
''',
    '''    def _observe(
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
''',
    1,
)
source = source.replace(
    '''    def _load_runtime_if_available(self) -> None:
        path = self.runtime_root / "checkpoint.json"
        if path.exists():
            self.load_checkpoint(_read_json(path))
''',
    '''    def _load_runtime_if_available(self) -> None:
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
''',
    1,
)
source = source.replace(
    '''    def _append_runtime_event(self, event: Mapping[str, Any]) -> None:
''',
    '''    def reset_runtime_state(self) -> None:
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
''',
    1,
)
source = source.replace(
    '''        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), ensure_ascii=False, default=_json_default) + "\\n")

    def status(self) -> Dict[str, Any]:
''',
    '''        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), ensure_ascii=False, default=_json_default) + "\\n")
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
''',
    1,
)
source = source.replace(
    '''            "runtime_root": str(self.runtime_root),
        }
''',
    '''            "runtime_root": str(self.runtime_root),
            "runtime_checkpoint_reset_reason": self._runtime_checkpoint_reset_reason,
        }
''',
    1,
)
write(history_path, source)

# 5) Retire the old second-module FAST classifier/calibration implementation entirely.
old_classifier = ROOT / "system/model/map_control/slurry_policy_model/_engine/disturbance_classifier.py"
if old_classifier.exists():
    old_classifier.unlink()

calibration_path = "system/model/map_control/slurry_policy_model/_engine/calibration.py"
calibration = read(calibration_path)
start = calibration.find("def _legacy_minimums(")
end = calibration.find("def calibrate_action_magnitude_bins(")
if start < 0 or end < 0 or end <= start:
    raise RuntimeError("cannot locate retired disturbance calibration section")
calibration = calibration[:start] + calibration[end:]
calibration = calibration.replace(
    "from .schema import condition_axis_specs, time_column\nfrom .utils import quantiles, robust_slope_per_minute\n\n",
    "",
    1,
)
write(calibration_path, calibration)

# 6) Clean obsolete online-loader compatibility property now that no runtime consumer exists.
loader_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/policy_snapshot_loader.py"
loader = read(loader_path)
obsolete = '''    @property
    def effective_disturbance(self) -> dict:
        return self.effective_config.get("disturbance", {})

'''
if obsolete in loader:
    loader = loader.replace(obsolete, "", 1)
write(loader_path, loader)

# 7) Remove an unused legacy local variable from the state-machine FAST escalation path.
sm_path = "system/model/map_control/slurry_policy_model/slurry_policy_online/decision_state_machine.py"
sm = read(sm_path)
sm = sm.replace('        fast_cfg = self.regular_config.get("fast_policy", {})\n', '', 1)
write(sm_path, sm)

print("FAST hardening fixes applied")
