# -*- coding: utf-8 -*-
"""Canonical plant facts consumed by the formal Scheme-2 MFAC runtime.

This module does not define a second set of plant parameters. It only validates
and exposes values already owned by ``plant_config.PLANT_CONFIG`` so the MFAC
runtime cannot silently drift from the plant topology/safety configuration.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping

from system.model.config.plant_config import PLANT_CONFIG
from system.model.config.standard_fields import TARGET_SO2_COLUMN


def _finite(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("%s must be finite" % field_name)
    if not math.isfinite(number):
        raise ValueError("%s must be finite" % field_name)
    return number


def _same(left: Any, right: Any) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)


def target_supply_flow_contract(
    plant_config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return the single plant-owned supply-flow target/feedback contract."""
    plant = dict(plant_config or PLANT_CONFIG)
    scheme2 = dict(plant.get("scheme2") or {})
    value = dict(scheme2.get("target_supply_flow") or {})
    if not value:
        raise ValueError("PLANT_CONFIG.scheme2.target_supply_flow is required")

    lower = _finite(value.get("minimum"), "target_supply_flow.minimum")
    upper = _finite(value.get("maximum"), "target_supply_flow.maximum")
    if lower >= upper:
        raise ValueError("target_supply_flow.minimum must be < maximum")
    feedback_column = str(value.get("feedback_column") or "").strip()
    if not feedback_column:
        raise ValueError("target_supply_flow.feedback_column is required")
    unit = str(value.get("unit") or "m3/h").strip() or "m3/h"
    return {
        "minimum": lower,
        "maximum": upper,
        "feedback_column": feedback_column,
        "unit": unit,
    }


def primary_tower_contract(
    plant_config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve the tower associated with the canonical supply-flow feedback.

    The configured feedback column must match exactly one enabled tower whenever
    tower ``supply_flows`` are declared. A single enabled tower is only accepted
    without a match when it has no explicit supply-flow mapping at all. This
    prevents two descriptions of the same physical signal from drifting silently.
    """
    plant = dict(plant_config or PLANT_CONFIG)
    target = target_supply_flow_contract(plant)
    feedback_column = target["feedback_column"]
    towers = [
        dict(item)
        for item in plant.get("towers", []) or []
        if item.get("enabled", True)
    ]
    if not towers:
        raise ValueError("PLANT_CONFIG requires at least one enabled tower")

    matches = []
    tower_flow_columns: Dict[str, set[str]] = {}
    for tower in towers:
        tower_id = str(tower.get("tower_id") or "").strip()
        flow_columns = {
            str(item.get("column") or "").strip()
            for item in tower.get("supply_flows", []) or []
            if str(item.get("column") or "").strip()
        }
        tower_flow_columns[tower_id] = flow_columns
        if feedback_column in flow_columns:
            matches.append(tower)

    if len(matches) == 1:
        tower = matches[0]
    elif len(matches) == 0 and len(towers) == 1:
        only_tower = towers[0]
        only_id = str(only_tower.get("tower_id") or "").strip()
        if tower_flow_columns.get(only_id):
            raise ValueError(
                "target supply-flow feedback_column does not match primary tower supply_flows"
            )
        tower = only_tower
    else:
        raise ValueError(
            "target supply-flow feedback must identify exactly one enabled tower"
        )

    tower_id = str(tower.get("tower_id") or "").strip()
    ph_column = str(tower.get("ph_column") or "").strip()
    if not tower_id:
        raise ValueError("primary tower_id is required")
    if not ph_column:
        raise ValueError("primary tower ph_column is required")

    safe = tower.get("ph_safe_range")
    operating = tower.get("ph_operating_range")
    if not isinstance(safe, (list, tuple)) or len(safe) != 2:
        raise ValueError("primary tower ph_safe_range must be [low, high]")
    if not isinstance(operating, (list, tuple)) or len(operating) != 2:
        raise ValueError("primary tower ph_operating_range must be [low, high]")
    safe_min = _finite(safe[0], "ph_safe_range[0]")
    safe_max = _finite(safe[1], "ph_safe_range[1]")
    operating_min = _finite(operating[0], "ph_operating_range[0]")
    operating_max = _finite(operating[1], "ph_operating_range[1]")
    guard_band = _finite(tower.get("ph_guard_band", 0.0), "ph_guard_band")
    if safe_min >= safe_max:
        raise ValueError("ph_safe_range must satisfy low < high")
    if operating_min >= operating_max:
        raise ValueError("ph_operating_range must satisfy low < high")
    if not safe_min <= operating_min < operating_max <= safe_max:
        raise ValueError("ph_operating_range must lie inside ph_safe_range")
    if guard_band < 0.0:
        raise ValueError("ph_guard_band must be >= 0")

    return {
        "tower_id": tower_id,
        "ph_column": ph_column,
        "safe_min": safe_min,
        "safe_max": safe_max,
        "operating_min": operating_min,
        "operating_max": operating_max,
        "guard_band": guard_band,
        "feedback_column": feedback_column,
    }


def ph_arbitration_plant_values(
    plant_config: Mapping[str, Any] | None = None,
) -> Dict[str, float]:
    """Map plant-owned pH safety facts to the arbiter constructor fields."""
    tower = primary_tower_contract(plant_config)
    return {
        "operating_min": float(tower["operating_min"]),
        "operating_max": float(tower["operating_max"]),
        "safe_min": float(tower["safe_min"]),
        "safe_max": float(tower["safe_max"]),
        "guard_band": float(tower["guard_band"]),
    }


def plant_contract_snapshot(
    plant_config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return an audit-only snapshot of the current authoritative plant facts."""
    target = target_supply_flow_contract(plant_config)
    tower = primary_tower_contract(plant_config)
    return {
        "authority": "PLANT_CONFIG_SNAPSHOT",
        "target_so2_column": TARGET_SO2_COLUMN,
        "target_supply_flow": {
            "minimum": float(target["minimum"]),
            "maximum": float(target["maximum"]),
            "feedback_column": str(target["feedback_column"]),
            "unit": str(target["unit"]),
        },
        "primary_tower": {
            "tower_id": str(tower["tower_id"]),
            "ph_column": str(tower["ph_column"]),
            "safe_min": float(tower["safe_min"]),
            "safe_max": float(tower["safe_max"]),
            "operating_min": float(tower["operating_min"]),
            "operating_max": float(tower["operating_max"]),
            "guard_band": float(tower["guard_band"]),
        },
    }


def validate_plant_contract_snapshot(
    snapshot: Mapping[str, Any],
    plant_config: Mapping[str, Any] | None = None,
) -> None:
    """Reject an artifact snapshot that no longer matches current plant facts."""
    if not isinstance(snapshot, Mapping):
        raise ValueError("plant contract snapshot must be a mapping")
    current = plant_contract_snapshot(plant_config)
    if str(snapshot.get("target_so2_column") or "") != current["target_so2_column"]:
        raise ValueError("plant contract target_so2_column has changed")

    observed_target = snapshot.get("target_supply_flow")
    observed_tower = snapshot.get("primary_tower")
    if not isinstance(observed_target, Mapping):
        raise ValueError("plant contract target_supply_flow snapshot is missing")
    if not isinstance(observed_tower, Mapping):
        raise ValueError("plant contract primary_tower snapshot is missing")

    for name in ("minimum", "maximum"):
        if name not in observed_target or not _same(
            observed_target[name], current["target_supply_flow"][name]
        ):
            raise ValueError("plant contract target_supply_flow.%s has changed" % name)
    for name in ("feedback_column", "unit"):
        if str(observed_target.get(name) or "") != str(
            current["target_supply_flow"][name]
        ):
            raise ValueError("plant contract target_supply_flow.%s has changed" % name)

    for name in ("tower_id", "ph_column"):
        if str(observed_tower.get(name) or "") != str(current["primary_tower"][name]):
            raise ValueError("plant contract primary_tower.%s has changed" % name)
    for name in (
        "safe_min",
        "safe_max",
        "operating_min",
        "operating_max",
        "guard_band",
    ):
        if name not in observed_tower or not _same(
            observed_tower[name], current["primary_tower"][name]
        ):
            raise ValueError("plant contract primary_tower.%s has changed" % name)


def validate_runtime_plant_contract(
    continuous_target_config: Any,
    ph_arbitration_config: Any,
    plant_config: Mapping[str, Any] | None = None,
) -> None:
    """Reject manually constructed runtime configs that drift from plant facts."""
    target = target_supply_flow_contract(plant_config)
    expected_ph = ph_arbitration_plant_values(plant_config)

    target_fields = {
        "hard_min_supply_flow": target["minimum"],
        "hard_max_supply_flow": target["maximum"],
    }
    for name, expected in target_fields.items():
        actual = getattr(continuous_target_config, name, None)
        if actual is None or not _same(actual, expected):
            raise ValueError(
                "formal MFAC %s must match PLANT_CONFIG (%s)"
                % (name, expected)
            )

    if ph_arbitration_config is None:
        raise ValueError("formal MFAC runtime requires pH residual arbitration")
    for name, expected in expected_ph.items():
        actual = getattr(ph_arbitration_config, name, None)
        if actual is None or not _same(actual, expected):
            raise ValueError(
                "formal MFAC ph_arbitration.%s must match PLANT_CONFIG (%s)"
                % (name, expected)
            )


__all__ = [
    "target_supply_flow_contract",
    "primary_tower_contract",
    "ph_arbitration_plant_values",
    "plant_contract_snapshot",
    "validate_plant_contract_snapshot",
    "validate_runtime_plant_contract",
]
