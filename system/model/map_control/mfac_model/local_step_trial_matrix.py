# -*- coding: utf-8 -*-
"""Review-gated matrix contract for controlled LOCAL_GAIN identification.

The matrix never schedules or executes a plant action.  It records which step
levels have been reviewed, how much independent evidence is required, and
whether a later level may even be considered. Automatic step escalation is
forbidden.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional, Tuple


LOCAL_STEP_TRIAL_MATRIX_VERSION = (
    "SCHEME2_LOCAL_STEP_TRIAL_MATRIX_V1_REVIEW_GATED"
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class LocalStepTrialLevel:
    level_id: str
    step_up_m3_h: float
    max_step_up_m3_h: float
    required_valid_trials: Optional[int] = None
    required_independent_days: Optional[int] = None
    review_status: str = "REVIEW_REQUIRED"
    automatic_escalation_allowed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.level_id or "").strip():
            raise ValueError("level_id is required")
        step = _finite(self.step_up_m3_h)
        cap = _finite(self.max_step_up_m3_h)
        if step is None or step <= 0.0:
            raise ValueError("step_up_m3_h must be finite and > 0")
        if cap is None or cap <= 0.0 or step > cap:
            raise ValueError("max_step_up_m3_h must be >= step_up_m3_h")
        for name in ("required_valid_trials", "required_independent_days"):
            value = getattr(self, name)
            if value is not None and int(value) <= 0:
                raise ValueError("%s must be > 0 when provided" % name)
        if bool(self.automatic_escalation_allowed):
            raise ValueError("automatic step escalation is forbidden")

    @property
    def evidence_requirements_complete(self) -> bool:
        return (
            self.required_valid_trials is not None
            and self.required_independent_days is not None
        )

    @property
    def ready_for_manual_session(self) -> bool:
        return (
            str(self.review_status) == "REVIEWED"
            and self.evidence_requirements_complete
        )

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["evidence_requirements_complete"] = self.evidence_requirements_complete
        value["ready_for_manual_session"] = self.ready_for_manual_session
        return value


@dataclass(frozen=True)
class LocalStepTrialMatrix:
    matrix_id: str
    levels: Tuple[LocalStepTrialLevel, ...]
    activation_status: str = "NOT_ACTIVATABLE"
    automatic_execution_allowed: bool = False
    automatic_escalation_allowed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_TRIAL_MATRIX_VERSION

    def __post_init__(self) -> None:
        if not str(self.matrix_id or "").strip():
            raise ValueError("matrix_id is required")
        if not self.levels:
            raise ValueError("at least one trial level is required")
        steps = [float(level.step_up_m3_h) for level in self.levels]
        if steps != sorted(steps) or len(set(steps)) != len(steps):
            raise ValueError("trial levels must have unique ascending step sizes")
        if str(self.activation_status) != "NOT_ACTIVATABLE":
            raise ValueError("trial matrix cannot activate runtime control")
        if bool(self.automatic_execution_allowed):
            raise ValueError("automatic trial execution is forbidden")
        if bool(self.automatic_escalation_allowed):
            raise ValueError("automatic step escalation is forbidden")

    @property
    def ready_level_ids(self) -> Tuple[str, ...]:
        return tuple(
            level.level_id for level in self.levels if level.ready_for_manual_session
        )

    def can_consider_next_level(
        self,
        current_level_id: str,
        *,
        valid_trial_count: int,
        independent_days: int,
        human_review_approved: bool,
    ) -> bool:
        """Evidence gate only; never mutates or schedules the next level."""
        current = next(
            (item for item in self.levels if item.level_id == current_level_id),
            None,
        )
        if current is None or not current.ready_for_manual_session:
            return False
        if current.required_valid_trials is None or current.required_independent_days is None:
            return False
        if int(valid_trial_count) < int(current.required_valid_trials):
            return False
        if int(independent_days) < int(current.required_independent_days):
            return False
        if not bool(human_review_approved):
            return False
        index = self.levels.index(current)
        return index + 1 < len(self.levels)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matrix_id": self.matrix_id,
            "levels": [level.to_dict() for level in self.levels],
            "activation_status": self.activation_status,
            "automatic_execution_allowed": self.automatic_execution_allowed,
            "automatic_escalation_allowed": self.automatic_escalation_allowed,
            "ready_level_ids": list(self.ready_level_ids),
            "metadata": dict(self.metadata),
            "semantics_version": self.semantics_version,
        }


__all__ = [
    "LOCAL_STEP_TRIAL_MATRIX_VERSION",
    "LocalStepTrialLevel",
    "LocalStepTrialMatrix",
]
