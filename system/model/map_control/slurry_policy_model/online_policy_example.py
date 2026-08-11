"""在线调用示例；运行前先用 activate_policy_version.py 激活正式版本。"""
from slurry_policy_online import OnlineSlurryPolicy


policy = OnlineSlurryPolicy()

first_module_row = {
    "date": "2026-08-03 15:30:00",
    "jzfh": 350.0,
    "yyq_SO2": 3200.0,
    "jyq_SO2": 24.3,
    "xstjy_PH": 5.10,
    "aptjy_PH": 6.00,
    "xst_FMKD1": 30.0,
    "xst_FMKD2": 31.0,
    "apt_FMKD": 25.0,
    "condition_snapshot_version": "v006",
    "raw_grid_id": "P12-S13",
    "raw_condition_label": "366",
    "stable_condition_label": "365",
    "condition_label": "365",
    "condition_stable": True,
    "condition_switch_state": "STABLE",
    "condition_valid": True,
    "state_key": "",
}

decision = policy.evaluate(
    first_module_row,
    target=20.0,
    execution_context={
        "automatic_control_allowed": True,
        "manual_valves": [],
        "faulted_valves": [],
        "supply_pump_state_changing": False,
    },
)
print(decision)
