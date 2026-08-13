"""FAST_CHANGE 独立风险识别器。

借鉴旧项目 fast_change_mode 的“两因子”思路：
- 趋势风险：入口/工况轴的快速变化，负责触发 FAST_CHANGE；
- 效果风险：净烟气 SO2 相对动态目标和排放上限的风险，负责描述当前控制效果。

与旧实现不同，本模块明确保证：
1. 净烟气 SO2 超标、WARNING 或离目标很远，不能单独把系统判成 FAST_CHANGE；
2. FAST_CHANGE 只能由 plant_config.condition_axes 的快速变化触发；
3. 出口风险可以把 overall_risk 升级到 HIGH/EMERGENCY，供后续动作层使用；
4. 这里只输出风险上下文，不生成阀门/供浆动作。
"""
from __future__ import annotations

import copy
import math
from collections import deque
from typing import Any, Deque, Dict, Iterable, Mapping, Optional, Tuple

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG as SITE_PLANT_CONFIG
from system.model.config.standard_fields import (
    OUTLET_SO2_COLUMN,
    TARGET_SO2_COLUMN,
    TIME_COLUMN,
)

from .fast_change_config import FAST_CHANGE_CONFIG


REGULAR = "REGULAR"
FAST_CHANGE = "FAST_CHANGE"
FAST_RECOVERY = "FAST_RECOVERY"


class FastChangeConfigurationError(ValueError):
    """FAST_CHANGE 配置错误。"""


class FastChangeModeDetector:
    """入口趋势触发 + 出口效果风险的独立 FAST_CHANGE 检测器。

    典型用法::

        detector = FastChangeModeDetector()
        result = detector.evaluate(row, target=20.0)

    ``result`` 只描述 FAST 风险状态，不包含任何供浆动作。
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        plant_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.config = copy.deepcopy(FAST_CHANGE_CONFIG)
        if config:
            self._deep_update(self.config, config)
        self.plant = copy.deepcopy(plant_config or SITE_PLANT_CONFIG)
        self._validate_config()

        self.axes = [dict(item) for item in self.plant["condition_axes"]]
        self._series_state: Dict[str, Dict[str, Any]] = {}
        self._series_history: Dict[str, Deque[Tuple[pd.Timestamp, float]]] = {}

        self._mode = REGULAR
        self._fast_started_at: Optional[pd.Timestamp] = None
        self._last_fast_seen_at: Optional[pd.Timestamp] = None
        self._recovery_until: Optional[pd.Timestamp] = None
        self._exit_stable_count = 0
        self._last_fast_direction = "NONE"
        self._last_fast_exact_mode = "STEADY"

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def evaluate(
        self,
        data_point: Mapping[str, Any],
        target: Optional[Any] = None,
        timestamp: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """处理一条在线数据并返回 FAST_CHANGE 风险上下文。"""
        row = dict(data_point)
        ts = self._resolve_timestamp(timestamp if timestamp is not None else row.get(TIME_COLUMN))

        if not bool(self.config.get("enabled", True)):
            return self._disabled_result(ts, row, target)

        trend = self._evaluate_trend(row, ts)
        effect = self._evaluate_effect(row, ts, target)
        state_reasons = self._advance_state(ts, trend)

        active_direction = trend["fast_direction"]
        active_exact = trend["exact_trend_mode"]
        if self._mode in {FAST_CHANGE, FAST_RECOVERY} and active_direction == "NONE":
            active_direction = self._last_fast_direction
            active_exact = self._last_fast_exact_mode

        reasons = list(trend["reason_codes"]) + list(effect["reason_codes"]) + state_reasons
        if effect["effect_risk_level"] in {"HIGH", "EMERGENCY"} and not trend["raw_fast_change"]:
            reasons.append("EFFECT_RISK_DOES_NOT_TRIGGER_FAST_CHANGE")

        overall_risk = self._overall_risk(trend, effect)
        input_valid = bool(trend["available_axis_count"] > 0 and effect["outlet_so2_valid"])
        if trend["available_axis_count"] < len(self.axes):
            reasons.append("FAST_CHANGE_INPUT_DEGRADED")

        return {
            "fast_change_mode": self._mode,
            "fast_change_active": self._mode == FAST_CHANGE,
            "fast_change_recovery_active": self._mode == FAST_RECOVERY,
            "fast_change_raw_trigger": bool(trend["raw_fast_change"]),
            "fast_change_direction": active_direction,
            "fast_change_severity": trend["trend_severity"],
            "fast_change_exact_trend_mode": active_exact,
            "fast_change_raw_exact_trend_mode": trend["exact_trend_mode"],
            "fast_change_trend_risk_level": trend["trend_risk_level"],
            "fast_change_effect_risk_level": effect["effect_risk_level"],
            "fast_change_effect_state": effect["effect_state"],
            "fast_change_effect_direction": effect["effect_direction"],
            "fast_change_overall_risk_level": overall_risk,
            "fast_change_axis_columns": [str(axis["column"]) for axis in self.axes],
            "fast_change_axis_rates": trend["axis_rates"],
            "fast_change_axis_levels": trend["axis_levels"],
            "fast_change_axis_direction_ratios": trend["axis_direction_ratios"],
            "fast_change_trigger_axes": trend["trigger_axes"],
            "fast_change_available_axis_count": trend["available_axis_count"],
            "fast_change_trend_ready": trend["trend_ready"],
            "fast_change_current_so2": effect["current_so2"],
            "fast_change_target_so2": effect["target_so2"],
            "fast_change_target_error": effect["target_error"],
            "fast_change_emission_limit": effect["emission_limit"],
            "fast_change_outlet_so2_rate": effect["outlet_so2_rate"],
            "fast_change_outlet_so2_trend": effect["outlet_so2_trend"],
            "fast_change_input_valid": input_valid,
            "fast_change_reason_codes": list(dict.fromkeys(reasons)),
            "fast_change_state": self.get_state(),
            "fast_change_debug": {
                "axis_details": trend["axis_details"],
                "effect": effect,
            },
        }

    def judge_fast_change_mode(
        self,
        data_point: Mapping[str, Any],
        target: Optional[Any] = None,
        timestamp: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """兼容旧模块命名的公开别名。"""
        return self.evaluate(data_point, target=target, timestamp=timestamp)

    def reset(self) -> None:
        """清空趋势历史和 FAST 状态机。"""
        self._series_state.clear()
        self._series_history.clear()
        self._mode = REGULAR
        self._fast_started_at = None
        self._last_fast_seen_at = None
        self._recovery_until = None
        self._exit_stable_count = 0
        self._last_fast_direction = "NONE"
        self._last_fast_exact_mode = "STEADY"

    def get_state(self) -> Dict[str, Any]:
        return {
            "mode": self._mode,
            "fast_started_at": self._iso(self._fast_started_at),
            "last_fast_seen_at": self._iso(self._last_fast_seen_at),
            "recovery_until": self._iso(self._recovery_until),
            "exit_stable_count": int(self._exit_stable_count),
            "last_fast_direction": self._last_fast_direction,
            "last_fast_exact_mode": self._last_fast_exact_mode,
        }

    # ------------------------------------------------------------------
    # trend factor: only this factor may trigger FAST_CHANGE
    # ------------------------------------------------------------------
    def _evaluate_trend(self, row: Dict[str, Any], ts: pd.Timestamp) -> Dict[str, Any]:
        axis_details: Dict[str, Any] = {}
        active_items = []
        fast_items = []
        available = 0
        ready_count = 0

        for index, axis in enumerate(self.axes, start=1):
            column = str(axis["column"])
            value = self._number(row.get(column))
            if value is None:
                axis_details[column] = {
                    "axis_index": index,
                    "column": column,
                    "value": None,
                    "ready": False,
                    "level": "MISSING",
                }
                continue

            available += 1
            detail = self._analyze_axis(index, axis, value, ts)
            axis_details[column] = detail
            if detail["ready"]:
                ready_count += 1
            if detail["level"] not in {"STEADY", "WARMING_UP", "MISSING"}:
                active_items.append(detail)
            if detail["level"].endswith("_FAST"):
                fast_items.append(detail)

        exact_mode, direction, severity = self._aggregate_axis_modes(active_items)
        raw_fast = bool(fast_items)
        trigger_axes = [item["column"] for item in fast_items]

        if raw_fast:
            trend_risk = "HIGH"
        elif active_items:
            trend_risk = "MEDIUM"
        else:
            trend_risk = "LOW"

        reasons = []
        if raw_fast:
            reasons.append("FAST_TREND_DETECTED")
            reasons.append("FAST_DIRECTION:%s" % direction)
        elif active_items:
            reasons.append("SLOW_TREND_DETECTED")
        if available == 0:
            reasons.append("NO_CONDITION_AXIS_AVAILABLE")
        elif ready_count == 0:
            reasons.append("FAST_TREND_WARMING_UP")

        return {
            "raw_fast_change": raw_fast,
            "fast_direction": direction if raw_fast else "NONE",
            "trend_severity": severity,
            "exact_trend_mode": exact_mode,
            "trend_risk_level": trend_risk,
            "trigger_axes": trigger_axes,
            "available_axis_count": available,
            "trend_ready": bool(ready_count > 0),
            "axis_rates": {
                column: detail.get("rate_per_minute")
                for column, detail in axis_details.items()
            },
            "axis_levels": {
                column: detail.get("level")
                for column, detail in axis_details.items()
            },
            "axis_direction_ratios": {
                column: {
                    "rise": detail.get("rise_ratio"),
                    "drop": detail.get("drop_ratio"),
                }
                for column, detail in axis_details.items()
            },
            "axis_details": axis_details,
            "reason_codes": reasons,
        }

    def _analyze_axis(
        self,
        index: int,
        axis: Dict[str, Any],
        value: float,
        ts: pd.Timestamp,
    ) -> Dict[str, Any]:
        column = str(axis["column"])
        step = abs(float(axis.get("step", 0.0)))
        thresholds = self._axis_thresholds(column, step)
        smoothed = self._update_dema(column, value, ts)
        points = self._window_points(column, ts)
        minimum_points = int(self.config["trend"].get("minimum_points", 4))

        base = {
            "axis_index": index,
            "column": column,
            "value": float(value),
            "smoothed_value": float(smoothed),
            "slow_rate_threshold": thresholds["slow_rate"],
            "fast_rate_threshold": thresholds["fast_rate"],
            "direction_deadband": thresholds["direction_deadband"],
            "sample_count": len(points),
        }
        if len(points) < minimum_points:
            return {
                **base,
                "ready": False,
                "rate_per_minute": 0.0,
                "rise_ratio": 0.0,
                "drop_ratio": 0.0,
                "level": "WARMING_UP",
            }

        rate = self._slope_per_minute(points)
        rise_ratio, drop_ratio = self._direction_ratios(
            points,
            thresholds["direction_deadband"],
        )
        ratio_threshold = float(self.config["trend"]["direction_ratio_threshold"])

        if rate >= thresholds["fast_rate"] and rise_ratio >= ratio_threshold:
            level = "RISE_FAST"
        elif rate <= -thresholds["fast_rate"] and drop_ratio >= ratio_threshold:
            level = "DROP_FAST"
        elif rate >= thresholds["slow_rate"] and rise_ratio >= ratio_threshold:
            level = "RISE_SLOW"
        elif rate <= -thresholds["slow_rate"] and drop_ratio >= ratio_threshold:
            level = "DROP_SLOW"
        else:
            level = "STEADY"

        return {
            **base,
            "ready": True,
            "rate_per_minute": float(rate),
            "rise_ratio": float(rise_ratio),
            "drop_ratio": float(drop_ratio),
            "level": level,
        }

    def _aggregate_axis_modes(
        self, active_items: Iterable[Dict[str, Any]]
    ) -> Tuple[str, str, str]:
        items = list(active_items)
        if not items:
            return "STEADY", "NONE", "STEADY"

        directions = {
            "RISE" if str(item["level"]).startswith("RISE") else "DROP"
            for item in items
        }
        severity = "FAST" if any(str(item["level"]).endswith("FAST") for item in items) else "SLOW"
        if len(directions) > 1:
            return "MIXED_DISTURBANCE_%s" % severity, "MIXED", severity

        direction = next(iter(directions))
        if len(items) == 1:
            item = items[0]
            exact = "AXIS%d_%s_%s" % (int(item["axis_index"]), direction, severity)
        else:
            axis_names = "_AND_".join("AXIS%d" % int(item["axis_index"]) for item in items)
            exact = "%s_%s_%s" % (axis_names, direction, severity)
        return exact, direction, severity

    # ------------------------------------------------------------------
    # effect factor: outlet feedback / emission risk, never triggers FAST by itself
    # ------------------------------------------------------------------
    def _evaluate_effect(
        self,
        row: Dict[str, Any],
        ts: pd.Timestamp,
        explicit_target: Optional[Any],
    ) -> Dict[str, Any]:
        cfg = self.config["effect"]
        current = self._number(row.get(OUTLET_SO2_COLUMN))
        target = self._resolve_target(row, explicit_target)
        emission_limit = float(self.plant["outlet_so2_safe_range"][1])
        warning = emission_limit - float(cfg["emission_warning_margin"])
        emergency = emission_limit - float(cfg["emission_emergency_margin"])

        reasons = []
        if current is None:
            return {
                "outlet_so2_valid": False,
                "current_so2": None,
                "target_so2": target,
                "target_error": None,
                "emission_limit": emission_limit,
                "effect_state": "UNKNOWN",
                "effect_direction": "UNKNOWN",
                "effect_risk_level": "LOW",
                "outlet_so2_rate": 0.0,
                "outlet_so2_trend": "UNKNOWN",
                "reason_codes": ["OUTLET_SO2_MISSING"],
            }

        outlet_key = "__FAST_CHANGE_OUTLET_SO2__"
        self._update_dema(outlet_key, current, ts)
        outlet_points = self._window_points(outlet_key, ts)
        minimum_points = int(self.config["trend"].get("minimum_points", 4))
        outlet_rate = self._slope_per_minute(outlet_points) if len(outlet_points) >= minimum_points else 0.0
        outlet_trend = self._outlet_trend(outlet_rate)

        error = float(current) - float(target)
        deadband = float(cfg["target_deadband"])
        far = float(cfg["far_from_target_threshold"])

        if current >= emergency:
            state = "EMERGENCY"
            direction = "SO2_DOWN"
            risk = "EMERGENCY"
            reasons.append("OUTLET_SO2_EMERGENCY")
        elif current >= warning:
            state = "WARNING"
            direction = "SO2_DOWN"
            risk = "HIGH"
            reasons.append("OUTLET_SO2_WARNING")
        elif error > far:
            state = "ABOVE_TARGET_FAR"
            direction = "SO2_DOWN"
            risk = "HIGH"
            reasons.append("OUTLET_SO2_FAR_ABOVE_TARGET")
        elif error > deadband:
            state = "ABOVE_TARGET"
            direction = "SO2_DOWN"
            risk = "MEDIUM"
            reasons.append("OUTLET_SO2_ABOVE_TARGET")
        elif error < -far:
            state = "BELOW_TARGET_FAR"
            direction = "SO2_UP"
            risk = "MEDIUM"
            reasons.append("OUTLET_SO2_FAR_BELOW_TARGET")
        elif error < -deadband:
            state = "BELOW_TARGET"
            direction = "SO2_UP"
            risk = "LOW"
            reasons.append("OUTLET_SO2_BELOW_TARGET")
        else:
            state = "TARGET_BAND"
            direction = "SO2_HOLD"
            risk = "LOW"
            reasons.append("OUTLET_SO2_INSIDE_TARGET_BAND")

        # 出口变化率只用于把效果风险升级，不反向触发 FAST_CHANGE。
        if outlet_trend == "RISING_FAST" and current >= target - deadband:
            risk = self._raise_risk(risk, minimum="MEDIUM")
            reasons.append("OUTLET_SO2_FAST_RISE_EFFECT_RISK")
        elif outlet_trend == "FALLING_FAST" and current <= target + deadband:
            risk = self._raise_risk(risk, minimum="MEDIUM")
            reasons.append("OUTLET_SO2_FAST_DROP_EFFECT_RISK")

        return {
            "outlet_so2_valid": True,
            "current_so2": float(current),
            "target_so2": float(target),
            "target_error": float(error),
            "emission_limit": emission_limit,
            "warning_threshold": warning,
            "emergency_threshold": emergency,
            "effect_state": state,
            "effect_direction": direction,
            "effect_risk_level": risk,
            "outlet_so2_rate": float(outlet_rate),
            "outlet_so2_trend": outlet_trend,
            "reason_codes": reasons,
        }

    # ------------------------------------------------------------------
    # FAST state machine
    # ------------------------------------------------------------------
    def _advance_state(self, ts: pd.Timestamp, trend: Dict[str, Any]) -> list[str]:
        raw_fast = bool(trend["raw_fast_change"])
        reasons = []

        if raw_fast:
            if self._mode != FAST_CHANGE:
                self._fast_started_at = ts
                reasons.append("FAST_CHANGE_ENTERED")
            self._mode = FAST_CHANGE
            self._last_fast_seen_at = ts
            self._exit_stable_count = 0
            self._recovery_until = None
            self._last_fast_direction = str(trend["fast_direction"])
            self._last_fast_exact_mode = str(trend["exact_trend_mode"])
            return reasons

        if self._mode == FAST_CHANGE:
            started = self._fast_started_at or ts
            held_minutes = max(0.0, (ts - started).total_seconds() / 60.0)
            minimum_hold = float(self.config["state_machine"]["minimum_fast_hold_minutes"])
            if held_minutes < minimum_hold:
                reasons.append("FAST_MINIMUM_HOLD_ACTIVE")
                return reasons

            self._exit_stable_count += 1
            if self._exit_stable_count >= int(self.config["state_machine"]["exit_stable_cycles"]):
                self._mode = FAST_RECOVERY
                self._recovery_until = ts + pd.Timedelta(
                    minutes=float(self.config["state_machine"]["recovery_hold_minutes"])
                )
                reasons.append("FAST_RECOVERY_ENTERED")
            return reasons

        if self._mode == FAST_RECOVERY:
            until = self._recovery_until or ts
            if ts >= until:
                self._mode = REGULAR
                self._fast_started_at = None
                self._recovery_until = None
                self._exit_stable_count = 0
                self._last_fast_direction = "NONE"
                self._last_fast_exact_mode = "STEADY"
                reasons.append("FAST_RECOVERY_COMPLETED")
            else:
                reasons.append("FAST_RECOVERY_HOLD_ACTIVE")
            return reasons

        self._mode = REGULAR
        return reasons

    # ------------------------------------------------------------------
    # signal helpers
    # ------------------------------------------------------------------
    def _update_dema(self, key: str, value: float, ts: pd.Timestamp) -> float:
        cfg = self.config["trend"]
        halflife = max(1e-6, float(cfg["dema_halflife_seconds"]))
        state = self._series_state.get(key)
        if not state:
            ema1 = ema2 = float(value)
        else:
            previous_ts = state["timestamp"]
            dt = max(0.0, (ts - previous_ts).total_seconds())
            alpha = 1.0 - math.pow(0.5, dt / halflife) if dt > 0 else 1.0
            alpha = min(1.0, max(0.0, alpha))
            ema1 = alpha * float(value) + (1.0 - alpha) * float(state["ema1"])
            ema2 = alpha * ema1 + (1.0 - alpha) * float(state["ema2"])
        dema = 2.0 * ema1 - ema2
        self._series_state[key] = {
            "timestamp": ts,
            "ema1": ema1,
            "ema2": ema2,
            "dema": dema,
        }
        history = self._series_history.setdefault(key, deque())
        history.append((ts, float(dema)))
        keep_minutes = max(10.0, float(cfg["window_minutes"]) * 2.0)
        cutoff = ts - pd.Timedelta(minutes=keep_minutes)
        while history and history[0][0] < cutoff:
            history.popleft()
        return float(dema)

    def _window_points(self, key: str, ts: pd.Timestamp) -> list[Tuple[pd.Timestamp, float]]:
        history = self._series_history.get(key, deque())
        cutoff = ts - pd.Timedelta(minutes=float(self.config["trend"]["window_minutes"]))
        return [(t, v) for t, v in history if t >= cutoff and t <= ts]

    @staticmethod
    def _slope_per_minute(points: Iterable[Tuple[pd.Timestamp, float]]) -> float:
        values = list(points)
        if len(values) < 2:
            return 0.0
        start = values[0][0]
        xs = [(t - start).total_seconds() / 60.0 for t, _ in values]
        ys = [float(v) for _, v in values]
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        denominator = sum((x - x_mean) ** 2 for x in xs)
        if denominator <= 1e-12:
            return 0.0
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        return float(numerator / denominator)

    @staticmethod
    def _direction_ratios(
        points: Iterable[Tuple[pd.Timestamp, float]],
        deadband: float,
    ) -> Tuple[float, float]:
        values = [float(value) for _, value in points]
        if len(values) < 2:
            return 0.0, 0.0
        diffs = [right - left for left, right in zip(values[:-1], values[1:])]
        effective = [diff for diff in diffs if abs(diff) > deadband]
        if not effective:
            return 0.0, 0.0
        rise = sum(1 for diff in effective if diff > 0) / len(effective)
        drop = sum(1 for diff in effective if diff < 0) / len(effective)
        return float(rise), float(drop)

    def _axis_thresholds(self, column: str, step: float) -> Dict[str, float]:
        trend = self.config["trend"]
        overrides = trend.get("axis_overrides", {}) or {}
        override = dict(overrides.get(column, {}) or {})
        if step <= 0 and not override:
            raise FastChangeConfigurationError(
                "condition axis %s step 必须大于0，或在 axis_overrides 中显式配置阈值" % column
            )
        slow = float(
            override.get(
                "slow_rate",
                step * float(trend["slow_step_ratio_per_minute"]),
            )
        )
        fast = float(
            override.get(
                "fast_rate",
                step * float(trend["fast_step_ratio_per_minute"]),
            )
        )
        deadband = float(
            override.get(
                "direction_deadband",
                step * float(trend["direction_deadband_step_ratio"]),
            )
        )
        if slow <= 0 or fast <= 0 or fast < slow:
            raise FastChangeConfigurationError(
                "axis %s 变化率阈值无效: slow=%s fast=%s" % (column, slow, fast)
            )
        return {
            "slow_rate": slow,
            "fast_rate": fast,
            "direction_deadband": max(0.0, deadband),
        }

    def _outlet_trend(self, rate: float) -> str:
        cfg = self.config["effect"]
        slow = float(cfg["outlet_slow_rate"])
        fast = float(cfg["outlet_fast_rate"])
        if rate >= fast:
            return "RISING_FAST"
        if rate >= slow:
            return "RISING"
        if rate <= -fast:
            return "FALLING_FAST"
        if rate <= -slow:
            return "FALLING"
        return "STABLE"

    # ------------------------------------------------------------------
    # generic helpers
    # ------------------------------------------------------------------
    def _resolve_target(self, row: Dict[str, Any], explicit_target: Optional[Any]) -> float:
        for value in (
            explicit_target,
            row.get(TARGET_SO2_COLUMN),
            self.config["effect"].get("default_target", 20.0),
        ):
            number = self._number(value)
            if number is not None:
                return float(number)
        raise FastChangeConfigurationError("无法解析净烟气 SO2 目标值")

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _resolve_timestamp(value: Any) -> pd.Timestamp:
        if value is None or value == "":
            return pd.Timestamp.now()
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return pd.Timestamp.now()
        if ts.tzinfo is not None:
            ts = ts.tz_convert(None)
        return ts

    @staticmethod
    def _iso(value: Optional[pd.Timestamp]) -> Optional[str]:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _deep_update(base: Dict[str, Any], override: Dict[str, Any]) -> None:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                FastChangeModeDetector._deep_update(base[key], value)
            else:
                base[key] = copy.deepcopy(value)

    @staticmethod
    def _raise_risk(current: str, minimum: str) -> str:
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "EMERGENCY": 3}
        return current if order.get(current, 0) >= order.get(minimum, 0) else minimum

    def _overall_risk(self, trend: Dict[str, Any], effect: Dict[str, Any]) -> str:
        effect_risk = str(effect["effect_risk_level"])
        if effect_risk == "EMERGENCY":
            return "EMERGENCY"
        if bool(trend["raw_fast_change"]):
            return "HIGH"
        if self._mode == FAST_RECOVERY:
            return self._raise_risk(effect_risk, "MEDIUM")
        return self._raise_risk(effect_risk, str(trend["trend_risk_level"]))

    def _validate_config(self) -> None:
        axes = self.plant.get("condition_axes") or []
        if len(axes) not in {1, 2}:
            raise FastChangeConfigurationError("plant_config.condition_axes 只支持 1 或 2 个轴")
        for axis in axes:
            if not str(axis.get("column", "")).strip():
                raise FastChangeConfigurationError("condition axis column 不能为空")
        safe = self.plant.get("outlet_so2_safe_range") or []
        if len(safe) != 2 or float(safe[0]) >= float(safe[1]):
            raise FastChangeConfigurationError("outlet_so2_safe_range 配置无效")
        trend = self.config.get("trend", {})
        if float(trend.get("window_minutes", 0)) <= 0:
            raise FastChangeConfigurationError("trend.window_minutes 必须大于0")
        if int(trend.get("minimum_points", 0)) < 2:
            raise FastChangeConfigurationError("trend.minimum_points 必须至少为2")
        ratio = float(trend.get("direction_ratio_threshold", 0))
        if not 0 < ratio <= 1:
            raise FastChangeConfigurationError("trend.direction_ratio_threshold 必须在 (0,1] 内")
        effect = self.config.get("effect", {})
        warning = float(effect.get("emission_warning_margin", 0))
        emergency = float(effect.get("emission_emergency_margin", 0))
        if warning <= 0 or emergency < 0 or emergency > warning:
            raise FastChangeConfigurationError("effect emission margin 配置无效")

    def _disabled_result(
        self,
        ts: pd.Timestamp,
        row: Dict[str, Any],
        target: Optional[Any],
    ) -> Dict[str, Any]:
        current = self._number(row.get(OUTLET_SO2_COLUMN))
        return {
            "fast_change_mode": REGULAR,
            "fast_change_active": False,
            "fast_change_recovery_active": False,
            "fast_change_raw_trigger": False,
            "fast_change_direction": "NONE",
            "fast_change_severity": "STEADY",
            "fast_change_exact_trend_mode": "STEADY",
            "fast_change_raw_exact_trend_mode": "STEADY",
            "fast_change_trend_risk_level": "LOW",
            "fast_change_effect_risk_level": "LOW",
            "fast_change_effect_state": "DISABLED",
            "fast_change_effect_direction": "UNKNOWN",
            "fast_change_overall_risk_level": "LOW",
            "fast_change_axis_columns": [str(axis["column"]) for axis in self.axes],
            "fast_change_axis_rates": {},
            "fast_change_axis_levels": {},
            "fast_change_axis_direction_ratios": {},
            "fast_change_trigger_axes": [],
            "fast_change_available_axis_count": 0,
            "fast_change_trend_ready": False,
            "fast_change_current_so2": current,
            "fast_change_target_so2": self._resolve_target(row, target),
            "fast_change_target_error": None,
            "fast_change_emission_limit": float(self.plant["outlet_so2_safe_range"][1]),
            "fast_change_outlet_so2_rate": 0.0,
            "fast_change_outlet_so2_trend": "UNKNOWN",
            "fast_change_input_valid": False,
            "fast_change_reason_codes": ["FAST_CHANGE_DISABLED"],
            "fast_change_state": self.get_state(),
            "fast_change_debug": {"timestamp": ts.isoformat()},
        }
