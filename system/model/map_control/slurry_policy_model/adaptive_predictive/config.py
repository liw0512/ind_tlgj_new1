from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

try:
    from .._engine.schema import OUTLET_SO2_COLUMN
except ImportError:  # pragma: no cover
    from system.model.map_control.slurry_policy_model._engine.schema import OUTLET_SO2_COLUMN


DEFAULT_SAMPLE_SECONDS = 10
DEFAULT_PREDICTION_HORIZON_MINUTES = 10.0
DEFAULT_CONTROL_HORIZON_MINUTES = 2.0


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def measured_disturbance_columns(plant: dict[str, Any]) -> tuple[str, ...]:
    """Return the plant-defined causal disturbance axes.

    The predictive layer deliberately does not hard-code power-plant ``jzfh``
    or auto-add noisy ``yyq_LL``.  A steel plant may expose only ``yyq_SO2``;
    a power plant may expose ``jzfh`` and ``yyq_SO2``.  The same code path is
    therefore reused and only plant parameters change.
    """

    return _unique(
        str(axis.get("column", ""))
        for axis in (plant.get("condition_axes", []) or [])
        if axis.get("column")
    )


def enabled_tower_channels(plant: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    channels: list[dict[str, Any]] = []
    for tower in plant.get("towers", []) or []:
        if not tower.get("enabled", True):
            continue
        supply_columns = _unique(
            str(flow.get("column", ""))
            for flow in (tower.get("supply_flows", []) or [])
            if flow.get("column")
        )
        channels.append(
            {
                "tower_id": str(tower.get("tower_id", "")),
                "ph_column": str(tower.get("ph_column", "")),
                "ph_safe_range": tuple(float(v) for v in tower.get("ph_safe_range", [])),
                "supply_flow_columns": supply_columns,
            }
        )
    return tuple(channels)


@dataclass(frozen=True)
class PredictiveFoundationSpec:
    sample_seconds: int
    prediction_horizon_minutes: float
    control_horizon_minutes: float
    outlet_so2_column: str
    disturbance_columns: tuple[str, ...]
    tower_channels: tuple[dict[str, Any], ...]
    shadow_only: bool

    @property
    def prediction_steps(self) -> int:
        return max(
            1,
            int(round(self.prediction_horizon_minutes * 60.0 / self.sample_seconds)),
        )

    @property
    def control_steps(self) -> int:
        return max(
            1,
            int(round(self.control_horizon_minutes * 60.0 / self.sample_seconds)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_seconds": self.sample_seconds,
            "prediction_horizon_minutes": self.prediction_horizon_minutes,
            "control_horizon_minutes": self.control_horizon_minutes,
            "prediction_steps": self.prediction_steps,
            "control_steps": self.control_steps,
            "outlet_so2_column": self.outlet_so2_column,
            "disturbance_columns": list(self.disturbance_columns),
            "tower_channels": [dict(item) for item in self.tower_channels],
            "shadow_only": self.shadow_only,
        }


def build_foundation_spec(
    plant: dict[str, Any],
    predictive_config: dict[str, Any] | None = None,
) -> PredictiveFoundationSpec:
    cfg = dict(predictive_config or {})
    sample_seconds = int(cfg.get("sample_seconds", DEFAULT_SAMPLE_SECONDS))
    if sample_seconds <= 0:
        raise ValueError("sample_seconds must be positive")

    disturbances = measured_disturbance_columns(plant)
    if not disturbances:
        raise ValueError("plant_config.condition_axes must define at least one disturbance column")

    towers = enabled_tower_channels(plant)
    if not towers:
        raise ValueError("plant_config must define at least one enabled tower")
    for tower in towers:
        if not tower["ph_column"]:
            raise ValueError("enabled tower is missing ph_column")
        if not tower["supply_flow_columns"]:
            raise ValueError(
                "predictive control requires actual supply-flow feedback for tower %s"
                % tower["tower_id"]
            )

    return PredictiveFoundationSpec(
        sample_seconds=sample_seconds,
        prediction_horizon_minutes=float(
            cfg.get("prediction_horizon_minutes", DEFAULT_PREDICTION_HORIZON_MINUTES)
        ),
        control_horizon_minutes=float(
            cfg.get("control_horizon_minutes", DEFAULT_CONTROL_HORIZON_MINUTES)
        ),
        outlet_so2_column=str(cfg.get("outlet_so2_column", OUTLET_SO2_COLUMN)),
        disturbance_columns=disturbances,
        tower_channels=towers,
        # The new path is explicitly shadow-only until P5 acceptance.
        shadow_only=bool(cfg.get("shadow_only", True)),
    )
