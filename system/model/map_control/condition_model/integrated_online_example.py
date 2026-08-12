# -*- coding: utf-8 -*-
"""第一模块调用第二模块，并支持同版本对原子热更新的最小示例。"""

from system.model.map_control.condition_model.online_condition_classifier import (
    build_online_condition_policy_pipeline,
)


# 程序启动时创建一次。默认从统一 active_version.json 读取 condition + policy。
# 后续 activate_policy_version.py 发布新版本后，该对象会按配置间隔自动预加载并
# 原子切换，不需要重新创建 pipeline，也不能每个控制周期重新创建。
pipeline = build_online_condition_policy_pipeline()


realtime_row = {
    "date": "2026-08-04 10:30:00",
    "jzfh": 350.0,
    "yyq_SO2": 3200.0,
    "jyq_SO2": 24.3,
    "xstjy_PH": 5.1,
    "aptjy_PH": 6.0,
    "liquid_gas_ratio": 10.5,
    "xst_FMKD1": 30.0,
    "xst_FMKD2": 31.0,
    "apt_FMKD": 25.0,
    "outlet_so2_target": 20.0,
}


final_output = pipeline.process(
    realtime_row,
    execution_context={
        "automatic_control_allowed": False,
        "manual_valves": [],
        "faulted_valves": [],
        "supply_pump_state_changing": False,
    },
)

print("统一版本:", final_output["integrated_active_version"])
print("第一模块版本:", final_output["condition_loaded_version"])
print("第二模块版本:", final_output["slurry_policy_loaded_version"])
print("版本一致:", final_output["version_consistent"])
print("版本切换状态:", final_output["version_switch_state"])
print("在线决策:", final_output)


# MainControl 真正执行动作后再回传；没有执行时 actual_action_executed=False。
# feedback = pipeline.record_execution({
#     "decision_id": final_output["slurry_policy_decision_id"],
#     "recommendation_accepted": True,
#     "actual_action_executed": True,
#     "actual_execution_time": realtime_row["date"],
#     "actual_action_id": final_output["slurry_policy_action_id"],
#     "actual_action_family": final_output["slurry_policy_action_family"],
#     "actual_action_direction": final_output["slurry_policy_action_direction"],
#     "actual_action_magnitude": final_output["slurry_policy_action_magnitude"],
#     "actual_valve_before": {
#         "xst_v1": 30.0,
#         "xst_v2": 31.0,
#         "apt_v1": 25.0,
#     },
#     "actual_valve_after": {
#         "xst_v1": 31.0,
#         "xst_v2": 32.0,
#         "apt_v1": 25.0,
#     },
# })
