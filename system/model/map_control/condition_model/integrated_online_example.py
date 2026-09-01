# -*- coding: utf-8 -*-
"""方案1第一模块 + 方案2 MFAC 同版本在线入口最小示例。

第一模块的调用方式保持方案1 canonical 设计；仅第二模块 backend 由
Scheme1 slurry_policy_model 适配为 Scheme2 MFACUnifiedRuntimePolicy。
"""

from system.model.map_control.condition_model.online_condition_classifier import (
    build_online_condition_policy_pipeline,
)


# 程序启动时创建一次。默认从 Scheme2 canonical active_version.json 读取
# condition + MFAC 同版本对。不要在每个控制周期重新创建 pipeline。
pipeline = build_online_condition_policy_pipeline()


realtime_row = {
    "date": "2026-08-04 10:30:00",
    "yyq_SO2": 1800.0,
    "jyq_SO2": 24.3,
    "yyq_LL": 820000.0,
    "xstjy_PH": 6.10,
    "xstshsjy_MD": 1150.0,
    "xstshsjy_LL": 50.0,
    "liquid_gas_ratio": 10.5,
    "outlet_so2_target": 20.0,
}


final_output = pipeline.process(
    realtime_row,
    execution_context={
        # 当前仓库仍是 shadow；这里不授予自动控制权限。
        "automatic_control_allowed": False,
        "manual_valves": [],
        "faulted_valves": [],
        "supply_pump_state_changing": False,
    },
)

print("统一版本:", final_output["integrated_active_version"])
print("第一模块版本:", final_output["condition_loaded_version"])
# slurry_policy_loaded_version 是第一模块 canonical 接口遗留字段名；
# 在 Scheme2 中其值实际对应 MFAC model version。
print("MFAC版本:", final_output["slurry_policy_loaded_version"])
print("第二模块类型:", final_output["second_module_type"])
print("版本一致:", final_output["version_consistent"])
print("版本切换状态:", final_output["version_switch_state"])
print("算法供浆目标:", final_output.get("second_module_algorithm_target_supply_flow"))
print("MFAC运行模式:", final_output.get("second_module_runtime_mode"))
print("DCS写入允许:", final_output.get("second_module_dcs_write_enabled"))
print("在线决策:", final_output)


# 当前 shadow 阶段不应伪造真实执行反馈。
# 未来仅当 MainControl/DCS 确实执行了目标、并有真实执行事实时，才调用：
#
# feedback = pipeline.record_execution({...})
#
# 真实执行反馈随后由 Scheme2 的 supply-flow tracking / response monitor
# 形成 actual-flow-reached 因果锚点；算法建议本身不能成为 MFAC 学习事件。
