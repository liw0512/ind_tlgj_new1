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
DEFAULT_CONDITION_LABEL_COLUMN = "condition_label"


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
    """Return the plant-defined hard causal disturbance axes.

    These are the first-module ``condition_axes``.  For the current steel plant
    this remains ``yyq_SO2`` only.  Operating-context predictors such as
    ``yyq_LL`` are deliberately kept separate so adding them to module 2 never
    changes the stable condition grid.
    """

    return _unique(
        str(axis.get("column", ""))
        for axis in (plant.get("condition_axes", []) or [])
        if axis.get("column")
    )


def operating_context_columns(
    plant: dict[str, Any],
    predictive_config: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return slow/auxiliary predictors used only by the response model.

    Plant-specific context must be explicit.  The caller may provide
    ``predictive_config['context_columns']``; otherwise the optional
    ``plant['predictive_context_columns']`` list is used.  No realtime monitor
    signal is auto-promoted into the model merely because it exists.
    """

    cfg = dict(predictive_config or {})
    configured = cfg.get("context_columns")
    if configured is None:
        configured = plant.get("predictive_context_columns", []) or []
    disturbances = set(measured_disturbance_columns(plant))
    return tuple(
        column for column in _unique(configured) if column not in disturbances
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
    context_columns: tuple[str, ...]
    condition_label_column: str
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
            "context_columns": list(self.context_columns),
            "condition_label_column": self.condition_label_column,
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
    contexts = operating_context_columns(plant, cfg)

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

    condition_label_column = str(
        cfg.get("condition_label_column", DEFAULT_CONDITION_LABEL_COLUMN)
    ).strip()
    if not condition_label_column:
        raise ValueError("condition_label_column cannot be empty")

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
        context_columns=contexts,
        condition_label_column=condition_label_column,
        tower_channels=towers,
        # The new path remains explicitly shadow-only until predictive-control
        # acceptance gates are completed.
        shadow_only=bool(cfg.get("shadow_only", True)),
    )
