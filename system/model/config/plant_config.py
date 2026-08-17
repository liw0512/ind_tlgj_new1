"""厂级物理/信号配置唯一事实源。

换厂时只修改本文件中的 ``PLANT_CONFIG``。第一模块 condition_model、第二模块
slurry_policy_model 以及 P4PC 集成层都从这里读取真正随厂变化的物理事实。
标准过程字段名已经固定在 ``standard_fields.py``，不再在这里做二次字段映射。

本文件只放“随厂变化”的内容：
- 工况轴选择、范围与步长；
- 净烟气 SO2 安全范围；
- 单塔/双塔结构、各塔 pH 字段和安全范围；
- 每塔供浆阀数量/字段/量程；
- 定频供浆泵电流阈值以及 pump -> valve 拓扑。

训练周期、动作评分、响应窗口、线程/队列、数据库等非厂级算法/运行参数仍由
各自模块配置管理，不应复制到这里。
"""
from __future__ import annotations


PLANT_CONFIG = {
    # 净烟气 SO2 硬安全范围。上限同时作为第一模块 risk_rate 的排放限值。
    "outlet_so2_safe_range": [0.0, 35.0],

    # ------------------------------------------------------------------
    # 工况轴唯一配置。支持 1 个或 2 个任意数值字段。
    # 第 1 个轴内部编码为 P#，第 2 个轴内部编码为 S#；P/S 不再代表固定物理量。
    #
    # 单轴示例：
    # "condition_axes": [
    #     {"column": "yyq_SO2", "min": 500.0, "max": 7000.0, "step": 200.0},
    # ],
    #
    # 钢厂双轴示例：
    # "condition_axes": [
    #     {"column": "gas_flow", "min": 1000.0, "max": 5000.0, "step": 200.0},
    #     {"column": "inlet_sulfur", "min": 100.0, "max": 3000.0, "step": 100.0},
    # ],
    "condition_axes": [
        # {
        #     "column": "jzfh",
        #     "min": 100.0,
        #     "max": 660.0,
        #     "step": 10.0,
        # },
        {
            "column": "yyq_SO2",
            "min": 500.0,
            "max": 5000.0,
            "step": 100.0,
        },
    ],

    # ------------------------------------------------------------------
    # 塔/阀门/供浆泵拓扑唯一配置。
    # - 单塔：将不用的塔 enabled=False，或删除该塔项；
    # - 每塔 1/2/3 个阀：直接增删 valves；
    # - supply_pumps 为空：不启用泵电流阀门可用性约束；
    # - supply_pumps 非空：current > run_current_threshold 判泵状态=1，否则=0；
    # - 一个泵可以服务多个阀，一个阀也可以由多台泵服务；任一服务泵运行，
    #   该阀即具备供浆路径。
    "towers": [
        {
            "tower_id": "xst",
            "display_name": "一级塔",
            "enabled": True,
            "ph_column": "xstjy_PH",
            "ph_safe_range": [5.6, 6.8],
            "ph_guard_band": 0.15,
            "valves": [
                {
                    "valve_id": "xst_v1",
                    "display_name": "一级塔供浆阀1",
                    "column": "xst_FMKD",
                    "min_opening": 0.0,
                    "max_opening": 100.0,
                    "action_threshold": 0.50,
                },
                # {
                #     "valve_id": "xst_v2",
                #     "display_name": "一级塔供浆阀2",
                #     "column": "xst_FMKD2",
                #     "min_opening": 0.0,
                #     "max_opening": 100.0,
                #     "action_threshold": 0.50,
                # },
            ],
            # 示例：一台 A 泵同时服务两个阀：
            # "supply_pumps": [
            #     {
            #         "pump_id": "xst_pump_A",
            #         "current_column": "xstgjb_ADL",
            #         "run_current_threshold": 10.0,
            #         "served_valve_ids": ["xst_v1", "xst_v2"],
            #     },
            # ],
            "supply_pumps": [],
        },
        # {
        #     "tower_id": "apt",
        #     "display_name": "二级塔",
        #     "enabled": True,
        #     "ph_column": "aptjy_PH",
        #     "ph_safe_range": [5.6, 6.5],
        #     "ph_guard_band": 0.15,
        #     "valves": [
        #         {
        #             "valve_id": "apt_v1",
        #             "display_name": "二级塔供浆阀",
        #             "column": "apt_FMKD",
        #             "min_opening": 0.0,
        #             "max_opening": 100.0,
        #             "action_threshold": 0.50,
        #         }
        #     ],
        #     # 示例：
        #     # "supply_pumps": [
        #     #     {
        #     #         "pump_id": "apt_pump_A",
        #     #         "current_column": "aptgjb_ADL",
        #     #         "run_current_threshold": 10.0,
        #     #         "served_valve_ids": ["apt_v1"],
        #     #     },
        #     # ],
        #     "supply_pumps": [],
        # },
    ],
}
