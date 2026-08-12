from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd


def configured_supply_pumps(plant: dict[str, Any]) -> list[dict[str, Any]]:
    """Return configured fixed-speed slurry supply pumps of enabled towers.

    ``supply_pumps`` is optional for backward compatibility.  When a tower has
    no configured supply pumps, pump-based valve gating is disabled for that
    tower and all of its configured valves remain available.
    """

    pumps: list[dict[str, Any]] = []
    for tower in plant.get("towers", []):
        if not tower.get("enabled", True):
            continue
        tower_id = str(tower.get("tower_id", ""))
        for pump in tower.get("supply_pumps", []) or []:
            item = dict(pump)
            item["tower_id"] = tower_id
            pumps.append(item)
    return pumps


def supply_pump_current_columns(plant: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(pump["current_column"])
            for pump in configured_supply_pumps(plant)
        )
    )


def pump_state_from_current(value: Any, run_current_threshold: Any) -> int:
    """Convert fixed-speed pump current to a strict 0/1 running state.

    State is 1 only when the current is finite and strictly greater than the
    configured threshold.  Missing/NaN/invalid current is fail-safe state 0.
    """

    try:
        current = float(value)
        threshold = float(run_current_threshold)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(current) or not math.isfinite(threshold):
        return 0
    return int(current > threshold)


def evaluate_supply_pump_availability(
    plant: dict[str, Any],
    process: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve pump 0/1 states and valve availability for one realtime frame.

    Topology rule:
    - one pump may serve one or many valves;
    - one valve may be served by one or many pumps;
    - a valve is available when ANY configured serving pump is running;
    - missing/invalid current means that pump is not available;
    - towers without ``supply_pumps`` keep legacy behaviour: all configured
      valves are available and no pump current field is required.
    """

    pump_states: dict[str, int] = {}
    pump_currents: dict[str, float | None] = {}
    invalid_pump_ids: list[str] = []
    valve_availability: dict[str, bool] = {}
    valve_serving_pump_ids: dict[str, list[str]] = {}
    tower_available_valve_ids: dict[str, list[str]] = {}
    topology_configured_towers: list[str] = []

    for tower in plant.get("towers", []):
        if not tower.get("enabled", True):
            continue
        tower_id = str(tower.get("tower_id", ""))
        valve_ids = [str(v["valve_id"]) for v in tower.get("valves", [])]
        pumps = list(tower.get("supply_pumps", []) or [])

        if not pumps:
            for valve_id in valve_ids:
                valve_availability[valve_id] = True
                valve_serving_pump_ids[valve_id] = []
            tower_available_valve_ids[tower_id] = list(valve_ids)
            continue

        topology_configured_towers.append(tower_id)
        for valve_id in valve_ids:
            valve_availability[valve_id] = False
            valve_serving_pump_ids[valve_id] = []

        for pump in pumps:
            pump_id = str(pump["pump_id"])
            current_column = str(pump["current_column"])
            raw_current = process.get(current_column)
            try:
                current = float(raw_current)
                if not math.isfinite(current):
                    raise ValueError("non-finite current")
                pump_currents[pump_id] = current
            except (TypeError, ValueError, OverflowError):
                pump_currents[pump_id] = None
                invalid_pump_ids.append(pump_id)

            state = pump_state_from_current(
                raw_current,
                pump["run_current_threshold"],
            )
            pump_states[pump_id] = state
            for valve_id in pump.get("served_valve_ids", []) or []:
                valve_id = str(valve_id)
                valve_serving_pump_ids.setdefault(valve_id, []).append(pump_id)
                if state == 1:
                    valve_availability[valve_id] = True

        tower_available_valve_ids[tower_id] = [
            valve_id
            for valve_id in valve_ids
            if valve_availability.get(valve_id, False)
        ]

    available_valve_ids = sorted(
        valve_id
        for valve_id, available in valve_availability.items()
        if available
    )
    unavailable_valve_ids = sorted(
        valve_id
        for valve_id, available in valve_availability.items()
        if not available
    )
    return {
        "pump_states": pump_states,
        "pump_currents": pump_currents,
        "invalid_pump_ids": sorted(set(invalid_pump_ids)),
        "valve_availability": valve_availability,
        "valve_serving_pump_ids": valve_serving_pump_ids,
        "available_valve_ids": available_valve_ids,
        "unavailable_valve_ids": unavailable_valve_ids,
        "tower_available_valve_ids": tower_available_valve_ids,
        "topology_configured_towers": topology_configured_towers,
    }


def detect_supply_pump_state_change(
    identity_window: pd.DataFrame,
    plant: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Detect actual 0/1 pump-state changes inside an offline episode window.

    Normal running-current fluctuation does not invalidate an episode.  Only a
    threshold crossing that changes the derived 0/1 state is reported.
    Invalid/NaN current is fail-safe state 0, consistent with online logic.
    """

    changed_columns: list[str] = []
    for pump in configured_supply_pumps(plant):
        column = str(pump["current_column"])
        if column not in identity_window.columns:
            continue
        threshold = pump["run_current_threshold"]
        states = {
            pump_state_from_current(value, threshold)
            for value in identity_window[column].tolist()
        }
        if len(states) > 1:
            changed_columns.append(column)
    return bool(changed_columns), changed_columns
