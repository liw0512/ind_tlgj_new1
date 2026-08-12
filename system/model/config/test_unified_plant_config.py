"""Regression test for the single plant-specific configuration source."""
from __future__ import annotations

from system.model.config.plant_config import PLANT_CONFIG as SITE
from system.model.config.slurry_core_bridge_config import SLURRY_CORE_BRIDGE_CONFIG
from system.model.map_control.condition_model import condition_config
from system.model.map_control.slurry_policy_model import p4pc_slurry_policy_config
from system.model.map_control.slurry_policy_model import slurry_policy_config
from system.model.map_control.slurry_policy_model._engine.schema import OUTLET_SO2_COLUMN


def main() -> None:
    enabled_ph = [
        str(tower["ph_column"])
        for tower in SITE["towers"]
        if tower.get("enabled", True)
    ]

    assert condition_config.CONDITION_AXES == SITE["condition_axes"]
    assert (
        condition_config.DEFAULT_DATA_COLUMNS["outlet_so2"]
        == SITE["process_columns"]["outlet_so2"]
    )
    assert (
        condition_config.DEFAULT_DATA_COLUMNS["liquid_gas"]
        == SITE["process_columns"]["liquid_gas"]
    )
    assert condition_config.DEFAULT_EMISSION_LIMIT == float(
        SITE["outlet_so2_safe_range"][1]
    )
    if enabled_ph:
        assert condition_config.DEFAULT_DATA_COLUMNS["xst_ph"] == enabled_ph[0]
    if len(enabled_ph) > 1:
        assert condition_config.DEFAULT_DATA_COLUMNS["apt_ph"] == enabled_ph[1]

    policy_plant = slurry_policy_config.PLANT_CONFIG
    for key in (
        "time_column",
        "process_columns",
        "outlet_so2_safe_range",
        "condition_axes",
        "towers",
    ):
        assert policy_plant[key] == SITE[key]

    p4pc_plant = p4pc_slurry_policy_config.PLANT_CONFIG
    assert p4pc_plant["towers"] == SITE["towers"]
    assert p4pc_plant["condition_axes"] == SITE["condition_axes"]
    assert p4pc_plant["process_columns"] == SITE["process_columns"]

    assert OUTLET_SO2_COLUMN == SITE["process_columns"]["outlet_so2"]
    assert (
        SLURRY_CORE_BRIDGE_CONFIG["target_column"]
        == SITE["process_columns"]["target_so2"]
    )

    edges = slurry_policy_config.TRAINING_CONFIG["state"]["outlet_so2_edges"]
    assert float(edges[0]) == float(SITE["outlet_so2_safe_range"][0])
    assert float(edges[-1]) == float(SITE["outlet_so2_safe_range"][1])

    path_values = [
        *slurry_policy_config.PLANT_CONFIG["paths"].values(),
        condition_config.INITIAL_CONDITION_TRAIN_CONFIG["input_csv_path"],
        condition_config.INITIAL_CONDITION_TRAIN_CONFIG["output_csv_path"],
        condition_config.INCREMENTAL_CONDITION_TRAIN_CONFIG["input_csv_path"],
        condition_config.INCREMENTAL_CONDITION_TRAIN_CONFIG["output_csv_path"],
    ]
    for value in path_values:
        assert "F:\\tlgj" not in str(value)
        assert "F:\\tlgj_new" not in str(value)

    print("unified plant config regression tests passed")


if __name__ == "__main__":
    main()
