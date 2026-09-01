from __future__ import annotations

from dataclasses import dataclass


CACO3_MOLAR_MASS = 100.0
SO2_MOLAR_MASS = 64.0
MG_PER_KG = 1_000_000.0


@dataclass(frozen=True)
class BaselineSlurryResult:
    """Unit-auditable result of the physical baseline slurry calculation."""

    inlet_so2_mg_nm3: float
    outlet_target_so2_mg_nm3: float
    gas_flow_nm3_h: float
    slurry_density_kg_m3: float
    solids_mass_fraction: float
    limestone_purity: float
    ca_s_ratio: float
    removed_so2_kg_h: float
    stoich_caco3_kg_h: float
    theoretical_q0_m3_h: float
    baseline_q_m3_h: float
    outlet_target_clipped: bool

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "inlet_so2_mg_nm3": self.inlet_so2_mg_nm3,
            "outlet_target_so2_mg_nm3": self.outlet_target_so2_mg_nm3,
            "gas_flow_nm3_h": self.gas_flow_nm3_h,
            "slurry_density_kg_m3": self.slurry_density_kg_m3,
            "solids_mass_fraction": self.solids_mass_fraction,
            "solids_percent": self.solids_mass_fraction * 100.0,
            "limestone_purity": self.limestone_purity,
            "ca_s_ratio": self.ca_s_ratio,
            "removed_so2_kg_h": self.removed_so2_kg_h,
            "stoich_caco3_kg_h": self.stoich_caco3_kg_h,
            "theoretical_q0_m3_h": self.theoretical_q0_m3_h,
            "baseline_q_m3_h": self.baseline_q_m3_h,
            "outlet_target_clipped": self.outlet_target_clipped,
        }


def solids_fraction_from_density(
    density_kg_m3: float,
    *,
    k: float,
    c: float,
    relation_output_unit: str = "percent",
    minimum_fraction: float = 0.01,
    maximum_fraction: float = 0.60,
) -> float:
    """Convert ``omega = k * rho + c`` to a 0..1 solids mass fraction.

    The engineering sheet labels omega as percent, while the mass-balance
    denominator requires a dimensionless mass fraction.  This helper makes the
    conversion explicit instead of silently accepting a 100x unit error.

    ``relation_output_unit='percent'`` means a relation result of 20.0 is 20%,
    therefore the returned fraction is 0.20.  ``'fraction'`` means the relation
    already returns 0..1.
    """

    rho = float(density_kg_m3)
    if rho <= 0:
        raise ValueError("slurry density must be positive")
    raw = float(k) * rho + float(c)
    unit = str(relation_output_unit).strip().lower()
    if unit == "percent":
        fraction = raw / 100.0
    elif unit == "fraction":
        fraction = raw
    else:
        raise ValueError("relation_output_unit must be 'percent' or 'fraction'")
    if not minimum_fraction <= fraction <= maximum_fraction:
        raise ValueError(
            "density-to-solids result is physically implausible: %.6f; "
            "check k/C and percent-vs-fraction units" % fraction
        )
    return fraction


def cas_from_ph_table(ph: float) -> float:
    """Engineering-sheet pH -> Ca/S table with linear interpolation.

    This helper exists for offline sensitivity checks.  The non-predictive
    controller should normally keep Qbase at an engineering reference Ca/S and
    let pH enter the separate feedback/constraint layer, avoiding double use of
    pH inside both base feedforward and feedback.
    """

    points = (
        (4.8, 1.05),
        (5.0, 1.10),
        (5.2, 1.20),
        (5.4, 1.30),
        (5.6, 1.40),
        (5.8, 1.50),
        (6.0, 1.70),
    )
    value = float(ph)
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        if x0 <= value <= x1:
            ratio = (value - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    raise RuntimeError("unable to interpolate Ca/S table")


def calculate_baseline_slurry_flow(
    *,
    inlet_so2_mg_nm3: float,
    outlet_target_so2_mg_nm3: float,
    gas_flow_nm3_h: float,
    slurry_density_kg_m3: float,
    solids_mass_fraction: float,
    ca_s_ratio: float = 1.70,
    limestone_purity: float = 0.90,
) -> BaselineSlurryResult:
    """Calculate a continuous-equivalent baseline slurry flow in m3/h.

    Unit derivation::

        removed_SO2 [kg/h]
          = (c_in - c_out_target) [mg/Nm3] * G [Nm3/h] / 1e6

        stoich_CaCO3 [kg/h]
          = removed_SO2 * 100 / 64

        q0 [m3/h]
          = stoich_CaCO3 / (purity * solids_fraction * density [kg/m3])

        Qbase [m3/h]
          = Ca/S * q0

    ``solids_mass_fraction`` MUST be a 0..1 fraction (e.g. 0.20 for 20%).
    Concentration and gas flow must be on a mutually consistent standard/dry/O2
    basis.  This function deliberately performs no partial O2 correction.

    For control, ``outlet_target_so2_mg_nm3`` is a target/design concentration,
    not the current measured outlet SO2.  Using the current outlet value would
    make Qbase decrease when outlet SO2 rises, which is unsuitable as the base
    control feedforward; measured outlet SO2 belongs in the feedback layer.
    """

    c_in = float(inlet_so2_mg_nm3)
    c_out = float(outlet_target_so2_mg_nm3)
    gas = float(gas_flow_nm3_h)
    rho = float(slurry_density_kg_m3)
    solids = float(solids_mass_fraction)
    cas = float(ca_s_ratio)
    purity = float(limestone_purity)

    if c_in < 0 or c_out < 0:
        raise ValueError("SO2 concentrations must be non-negative")
    if gas <= 0:
        raise ValueError("gas flow must be positive")
    if rho <= 0:
        raise ValueError("slurry density must be positive")
    if not 0.0 < solids < 1.0:
        raise ValueError("solids_mass_fraction must be in (0, 1)")
    if cas <= 0:
        raise ValueError("Ca/S ratio must be positive")
    if not 0.0 < purity <= 1.0:
        raise ValueError("limestone purity must be in (0, 1]")

    effective_outlet = min(c_out, c_in)
    delta_c = max(c_in - effective_outlet, 0.0)
    removed_so2_kg_h = delta_c * gas / MG_PER_KG
    stoich_caco3_kg_h = removed_so2_kg_h * CACO3_MOLAR_MASS / SO2_MOLAR_MASS
    q0 = stoich_caco3_kg_h / (purity * solids * rho)
    qbase = q0 * cas

    return BaselineSlurryResult(
        inlet_so2_mg_nm3=c_in,
        outlet_target_so2_mg_nm3=c_out,
        gas_flow_nm3_h=gas,
        slurry_density_kg_m3=rho,
        solids_mass_fraction=solids,
        limestone_purity=purity,
        ca_s_ratio=cas,
        removed_so2_kg_h=removed_so2_kg_h,
        stoich_caco3_kg_h=stoich_caco3_kg_h,
        theoretical_q0_m3_h=q0,
        baseline_q_m3_h=qbase,
        outlet_target_clipped=(effective_outlet != c_out),
    )
