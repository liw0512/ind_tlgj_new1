from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple


def profile_action(profile: Dict[str, Any]) -> Dict[str, Any]:
    return profile.get("action_profile") or profile.get("action_prior") or {}


def parse_action_family(family: str, plant: dict) -> Tuple[List[str], List[str]]:
    text = str(family or "")
    tower_ids: List[str] = []
    valve_ids: List[str] = []
    if text == "HOLD":
        return tower_ids, valve_ids
    if text.startswith("TOWER:"):
        first = text.split("|", 1)[0]
        tower_id = first.split(":", 1)[1]
        tower_ids.append(tower_id)
        if "|VALVE:" in text:
            remainder = text.split("|VALVE:", 1)[1]
            valve_ids.append(remainder.split("|", 1)[0])
        else:
            for tower in plant.get("towers", []):
                if str(tower.get("tower_id")) == tower_id:
                    valve_ids.extend(str(v["valve_id"]) for v in tower.get("valves", []))
                    break
    elif text.startswith("MULTI_TOWER:"):
        head = text.split("|", 1)[0].split(":", 1)[1]
        tower_ids.extend([x for x in head.split("+") if x])
        wanted = set(tower_ids)
        for tower in plant.get("towers", []):
            if str(tower.get("tower_id")) in wanted:
                valve_ids.extend(str(v["valve_id"]) for v in tower.get("valves", []))
    return tower_ids, valve_ids


def valve_lookup(plant: dict) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for tower in plant.get("towers", []):
        if not tower.get("enabled", True):
            continue
        for valve in tower.get("valves", []):
            item = dict(valve)
            item["tower_id"] = str(tower["tower_id"])
            output[str(valve["valve_id"])] = item
    return output


def normalize_blocked_valves(values: Any, plant: dict) -> Set[str]:
    raw = set(str(x) for x in (values or []))
    lookup = valve_lookup(plant)
    for valve_id, valve in lookup.items():
        if str(valve.get("column")) in raw:
            raw.add(valve_id)
    return raw
