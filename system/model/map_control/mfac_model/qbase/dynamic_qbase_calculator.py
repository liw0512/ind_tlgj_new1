# -*- coding: utf-8 -*-
"""Auditable online Dynamic Qbase calculation from the craftsman formula."""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from system.model.config.plant_config import PLANT_CONFIG

from ..mfac_schema import QbaseResult


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


class DynamicQbaseCalculator:
    """Calculate one tower's theoretical slurry-flow centre in m3/h.

    This component is calculation-only.  It never reads actual slurry flow,
    writes DCS state, enables MFAC learning, or applies a residual correction.
    """

    def __init__(
        self,
        tower_id: str,
        *,
        plant_config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.plant = dict(plant_config or PLANT_CONFIG)
        self.tower_id = str(tower_id).strip()
        towers = {
            str(item.get("tower_id")): dict(item)
            for item in self.plant.get("towers", [])
            if item.get("enabled", True)
        }
        if self.tower_id not in towers:
            raise ValueError("enabled tower not found: %s" % self.tower_id)
        self.tower = towers[self.tower_id]
        scheme2 = dict(self.plant.get("scheme2") or {})
        self.config = dict(scheme2.get("qbase") or {})
        self.so2_control = dict(scheme2.get("so2_control") or {})
        if not self.config:
            raise ValueError("PLANT_CONFIG.scheme2.qbase is required")
        self._validate_config()
        self._ca_s_curve = self._curve()
        solid_curve = dict(self.config.get("solid_fraction_curve") or {})
        self._solid_slope = float(solid_curve["slope"])
        self._solid_intercept = float(solid_curve["intercept"])
        self._solid_valid_low, self._solid_valid_high = map(
            float, self.config["solid_fraction_valid_range"]
        )
        self._target_low, self._target_high = map(
            float, self.so2_control.get("allowed_target_range", [5.0, 30.0])
        )

    def _validate_config(self) -> None:
        for key in (
            "inlet_so2_column",
            "target_so2_column",
            "gas_flow_column",
            "formula_version",
        ):
            if not str(self.config.get(key, "")).strip():
                raise ValueError("qbase config is missing %s" % key)
        curve = self._curve()
        if len(curve) < 2:
            raise ValueError("ca_s_curve requires at least two points")
        if len({point[0] for point in curve}) != len(curve):
            raise ValueError("ca_s_curve contains duplicate pH values")
        solid_curve = self.config.get("solid_fraction_curve")
        if not isinstance(solid_curve, Mapping):
            raise ValueError("solid_fraction_curve is required")
        if _finite(solid_curve.get("slope")) is None:
            raise ValueError("solid_fraction_curve.slope must be finite")
        if _finite(solid_curve.get("intercept")) is None:
            raise ValueError("solid_fraction_curve.intercept must be finite")
        solid_range = self.config.get("solid_fraction_valid_range")
        if not isinstance(solid_range, (list, tuple)) or len(solid_range) != 2:
            raise ValueError("solid_fraction_valid_range must be [low, high]")
        solid_low, solid_high = map(float, solid_range)
        if not 0.0 < solid_low < solid_high <= 1.0:
            raise ValueError("solid_fraction_valid_range must be within (0, 1]")
        if str(self.config.get("density_missing_policy", "BLOCK")).upper() != "BLOCK":
            raise ValueError("only density_missing_policy=BLOCK is supported")
        if str(self.config.get("ca_s_ph_source", "")).upper() != "CONFIG_REFERENCE":
            raise ValueError("only ca_s_ph_source=CONFIG_REFERENCE is supported")
        if _finite(self.config.get("ca_s_reference_ph")) is None:
            raise ValueError("ca_s_reference_ph is required")
        target_range = self.so2_control.get("allowed_target_range")
        if not isinstance(target_range, (list, tuple)) or len(target_range) != 2:
            raise ValueError("allowed_target_range must be [low, high]")
        target_low, target_high = map(float, target_range)
        if target_low >= target_high:
            raise ValueError("allowed_target_range must satisfy low < high")

    def _curve(self) -> list[tuple[float, float]]:
        points = []
        for raw in self.config.get("ca_s_curve", []):
            if not isinstance(raw, Sequence) or len(raw) != 2:
                raise ValueError("each ca_s_curve point must be [pH, Ca/S]")
            ph_value = _finite(raw[0])
            ratio = _finite(raw[1])
            if ph_value is None or ratio is None or ratio <= 0.0:
                raise ValueError("ca_s_curve contains an invalid point")
            points.append((ph_value, ratio))
        return sorted(points)

    def ca_s_ratio(self, ph_value: float) -> tuple[float, str]:
        ph = float(ph_value)
        curve = self._ca_s_curve
        if ph <= curve[0][0]:
            return curve[0][1], "CLAMPED_LOW" if ph < curve[0][0] else "EXACT"
        if ph >= curve[-1][0]:
            return curve[-1][1], "CLAMPED_HIGH" if ph > curve[-1][0] else "EXACT"
        for left, right in zip(curve, curve[1:]):
            if ph == left[0]:
                return left[1], "EXACT"
            if left[0] < ph < right[0]:
                span = right[0] - left[0]
                ratio = left[1] + (ph - left[0]) * (right[1] - left[1]) / span
                return ratio, "INTERPOLATED"
        raise RuntimeError("unable to resolve Ca/S curve")

    def calculate(
        self,
        process: Mapping[str, Any],
        *,
        target_so2: Optional[float] = None,
    ) -> QbaseResult:
        inlet_column = str(self.config["inlet_so2_column"])
        target_column = str(self.config["target_so2_column"])
        gas_flow_column = str(self.config["gas_flow_column"])
        ph_column = str(self.tower.get("ph_column", ""))
        density_column = str(self.tower.get("slurry_density_column", ""))

        inlet_so2 = _finite(process.get(inlet_column))
        resolved_target = _finite(
            target_so2 if target_so2 is not None else process.get(target_column)
        )
        gas_flow = _finite(process.get(gas_flow_column))
        ph_value = _finite(process.get(ph_column))
        density = _finite(process.get(density_column)) if density_column else None

        missing = []
        for name, value in (
            (inlet_column, inlet_so2),
            (target_column, resolved_target),
            (gas_flow_column, gas_flow),
            (density_column or "slurry_density", density),
        ):
            if value is None:
                missing.append(name)
        if missing:
            return self._invalid(
                "INPUT_INVALID",
                tuple("MISSING_OR_NONFINITE:%s" % name for name in missing),
                inlet_so2,
                resolved_target,
                gas_flow,
                density,
                ph_value,
            )
        if gas_flow <= 0.0:
            return self._invalid(
                "INPUT_INVALID", ("GAS_FLOW_NOT_POSITIVE",), inlet_so2,
                resolved_target, gas_flow, density, ph_value,
            )
        if density <= 0.0:
            return self._invalid(
                "INPUT_INVALID", ("SLURRY_DENSITY_NOT_POSITIVE",), inlet_so2,
                resolved_target, gas_flow, density, ph_value,
            )
        if not self._target_low <= resolved_target <= self._target_high:
            return self._invalid(
                "INPUT_INVALID", ("SO2_TARGET_OUT_OF_ALLOWED_RANGE",), inlet_so2,
                resolved_target, gas_flow, density, ph_value,
            )

        solid_fraction = self._solid_slope * density + self._solid_intercept
        if not self._solid_valid_low <= solid_fraction <= self._solid_valid_high:
            return self._invalid(
                "PHYSICAL_RANGE_INVALID",
                ("SOLID_FRACTION_OUT_OF_RANGE",),
                inlet_so2,
                resolved_target,
                gas_flow,
                density,
                ph_value,
                solid_fraction=solid_fraction,
            )

        ca_s_reference_ph = float(self.config["ca_s_reference_ph"])
        ca_s, ca_s_status = self.ca_s_ratio(ca_s_reference_ph)
        removal_demand = max(0.0, inlet_so2 - resolved_target)
        effective_fraction = float(self.config["limestone_effective_fraction"])
        if effective_fraction <= 0.0 or effective_fraction > 1.0:
            return self._invalid(
                "CONFIG_INVALID", ("LIMESTONE_EFFECTIVE_FRACTION_INVALID",),
                inlet_so2, resolved_target, gas_flow, density, ph_value,
                solid_fraction=solid_fraction, ca_s_ratio=ca_s,
            )
        numerator = (
            removal_demand
            * gas_flow
            * float(self.config["caco3_molar_mass"])
            / float(self.config["so2_molar_mass"])
        )
        q0 = numerator / (
            solid_fraction * 1_000_000.0 * effective_fraction * density
        )
        qbase = q0 * ca_s
        reasons = ["CA_S:%s" % ca_s_status]
        status = "OK"
        if removal_demand <= 0.0:
            status = "NO_REMOVAL_DEMAND"
            reasons.append("INLET_SO2_NOT_ABOVE_TARGET")
        return QbaseResult(
            tower_id=self.tower_id,
            valid=True,
            status=status,
            qbase_raw=qbase,
            qbase_effective=qbase,
            inlet_so2=inlet_so2,
            target_so2=resolved_target,
            gas_flow=gas_flow,
            slurry_density=density,
            solid_fraction=solid_fraction,
            ca_s_ratio=ca_s,
            ph_value=ph_value,
            formula_version=str(self.config["formula_version"]),
            reason_codes=tuple(reasons),
            metadata={
                "density_source": density_column,
                "inlet_so2_column": inlet_column,
                "target_so2_column": target_column,
                "gas_flow_column": gas_flow_column,
                "ph_column": ph_column,
                "ca_s_ph_source": "CONFIG_REFERENCE",
                "ca_s_reference_ph": ca_s_reference_ph,
                "measured_ph_role": "SAFETY_SUPERVISION_ONLY",
                "target_allowed_range": [self._target_low, self._target_high],
                "solid_fraction_valid_range": [
                    self._solid_valid_low,
                    self._solid_valid_high,
                ],
                "calibration_status": str(
                    self.config.get("calibration_status", "UNCONFIRMED")
                ),
                "control_permission": "SHADOW_ONLY",
            },
        )

    def _invalid(
        self,
        status: str,
        reasons: tuple[str, ...],
        inlet_so2: Optional[float],
        target_so2: Optional[float],
        gas_flow: Optional[float],
        density: Optional[float],
        ph_value: Optional[float],
        *,
        solid_fraction: Optional[float] = None,
        ca_s_ratio: Optional[float] = None,
    ) -> QbaseResult:
        return QbaseResult(
            tower_id=self.tower_id,
            valid=False,
            status=status,
            qbase_raw=None,
            qbase_effective=None,
            inlet_so2=inlet_so2,
            target_so2=target_so2,
            gas_flow=gas_flow,
            slurry_density=density,
            solid_fraction=solid_fraction,
            ca_s_ratio=ca_s_ratio,
            ph_value=ph_value,
            formula_version=str(self.config.get("formula_version", "UNKNOWN")),
            reason_codes=reasons,
            metadata={
                "calibration_status": str(
                    self.config.get("calibration_status", "UNCONFIRMED")
                ),
                "control_permission": "SHADOW_ONLY",
            },
        )
