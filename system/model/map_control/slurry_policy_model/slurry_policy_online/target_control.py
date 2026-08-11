from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd


class TargetError(ValueError):
    pass


class TargetManager:
    def __init__(self, online_config: dict, runtime_state: Dict[str, Any]) -> None:
        self.config = online_config["so2_control"]
        self.state = runtime_state.setdefault("target", {})

    def _commanded(self, runtime_target: Optional[float]) -> float:
        if runtime_target is None:
            # 运行过程中目标信号暂时缺失时保持上一次命令值，避免自动回跳默认目标。
            if self.state.get("commanded_target") is not None:
                value = float(self.state["commanded_target"])
            else:
                value = float(self.config["default_target"])
        else:
            value = float(runtime_target)
        lo, hi = [float(x) for x in self.config["allowed_target_range"]]
        if not lo <= value <= hi:
            raise TargetError("净烟气SO2目标 %.3f 超出允许范围 [%.3f, %.3f]" % (value, lo, hi))
        return value

    def resolve(self, runtime_target: Optional[float], timestamp: pd.Timestamp) -> Tuple[float, float, bool, bool]:
        commanded = self._commanded(runtime_target)
        old_commanded = self.state.get("commanded_target")
        old_effective = self.state.get("effective_target")
        target_changed = old_commanded is not None and abs(float(old_commanded) - commanded) > 1e-12

        if old_effective is None:
            effective = commanded
        elif not bool(self.config.get("target_transition_enabled", True)):
            effective = commanded
        else:
            last_text = self.state.get("last_update_time")
            last = pd.Timestamp(last_text) if last_text else timestamp
            elapsed_minutes = max(0.0, (timestamp - last).total_seconds() / 60.0)
            max_step = float(self.config["maximum_effective_target_change_per_minute"]) * elapsed_minutes
            delta = commanded - float(old_effective)
            if abs(delta) <= max_step or elapsed_minutes <= 0:
                effective = commanded if elapsed_minutes > 0 else float(old_effective)
            else:
                effective = float(old_effective) + (max_step if delta > 0 else -max_step)

        if target_changed:
            self.state["hold_cycles_remaining"] = int(
                self.config.get("hold_cycles_after_target_change", 0)
            )
        self.state["commanded_target"] = commanded
        self.state["effective_target"] = effective
        self.state["last_update_time"] = timestamp.isoformat()
        hold_required = int(self.state.get("hold_cycles_remaining", 0)) > 0
        return commanded, effective, target_changed, hold_required

    def consume_hold_cycle(self) -> None:
        remaining = int(self.state.get("hold_cycles_remaining", 0))
        self.state["hold_cycles_remaining"] = max(0, remaining - 1)
