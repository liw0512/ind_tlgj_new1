"""厂级物理/信号配置唯一事实源。

换厂时只修改本文件中的 ``PLANT_CONFIG``。第一模块 condition_model、第二模块
slurry_policy_model、P4PC 集成层、数据库基础历史字段以及 GUI V2 实时监控页都从
这里读取真正随厂变化的物理事实。

字段命名职责边界：
- 通讯层负责把 DCS/PLC 原始点位映射成系统使用的字段名；
- P4PC 接收通讯层传入的完整数据帧，不再维护现场字段白名单；
- 本文件只描述这些字段在当前厂里的物理角色和设备拓扑。

本文件只放“随厂变化”的内容：
- 工况轴选择、范围与步长；
- 净烟气 SO2 安全范围；
- 单塔/双塔结构、各塔 pH 字段和安全范围；
- 每塔供浆阀数量/字段/量程；
- 定频供浆泵电流阈值以及 pump -> valve 拓扑；
- GUI 实时监控所需的烟气/塔体/流量/泵等可变测点列表；
- 少量既不属于上述拓扑、但仍要求长期写数据库的额外现场字段。

注意：不额外维护 tower_count / valve_count / pump_count。数量直接由 enabled=True 的
塔和对应列表长度得到，避免“数量配置”和“实际列表”不一致。

训练周期、动作评分、响应窗口、线程/队列等非厂级算法/运行参数仍由各自模块配置
管理，不应复制到这里。
"""
from __future__ import annotations

from typing import Any, Mapping


PLANT_CONFIG = {
    # 净烟气 SO2 硬安全范围。上限同时作为第一模块 risk_rate 的排放限值。
    "outlet_so2_safe_range": [0.0, 35.0],

    # ------------------------------------------------------------------
    # GUI V2 实时监控页的烟气侧/公共辅助测点。
    # 这里使用“列表”而不是写死字段，因此换厂时可直接增删：
    # - inlet_signals：原烟气/入口侧；
    # - outlet_signals：净烟气/出口侧；
    # - auxiliary_signals：氧化风等公共辅助系统。
    # digits 控制显示小数位；unit 仅用于显示，不参与算法计算。
    # 这些 column 同时会自动进入基础历史数据库字段集合。
    "realtime_monitor": {
        "inlet_signals": [
            {
                "column": "yyq_SO2",
                "display_name": "原烟气 SO₂",
                "unit": "mg/Nm³",
                "digits": 0,
            },
            {
                "column": "yyq_LL",
                "display_name": "原烟气流量",
                "unit": "Nm³/h",
                "digits": 0,
            },
            {
                "column": "yyq_O2",
                "display_name": "入口 O₂",
                "unit": "%",
                "digits": 1,
            },
            {
                "column": "tlrkyq_YL",
                "display_name": "入口烟气压力",
                "unit": "kPa",
                "digits": 2,
            },
        ],
        "outlet_signals": [
            {
                "column": "jyq_SO2",
                "display_name": "净烟气 SO₂",
                "unit": "mg/Nm³",
                "digits": 1,
            },
            {
                "column": "jyq_LL",
                "display_name": "净烟气流量",
                "unit": "Nm³/h",
                "digits": 0,
            },
            {
                "column": "tlckyq_YL",
                "display_name": "出口烟气压力",
                "unit": "kPa",
                "digits": 2,
            },
        ],
        "auxiliary_signals": [
            {
                "column": "yhfjmg_YL",
                "display_name": "氧化风机出口母管压力",
                "unit": "kPa",
                "digits": 1,
            },
        ],
    },

    # ------------------------------------------------------------------
    # 若通讯层还传入其他字段，并希望这些字段长期进入 t_data1_filter_rt / 
    # t_model_result，但它们既不属于工况轴、塔/阀/泵拓扑，也不需要在实时监控页
    # 展示，可把标准字段名写在这里。这里只需要字段名，默认按数值 float8 保存。
    # 示例："persistence_extra_fields": ["zml", "coal_quality_xxx"]
    "persistence_extra_fields": [],

    # ------------------------------------------------------------------
    # 工况轴唯一配置。支持 1 个或 2 个任意数值字段。
    # 第 1 个轴内部编码为 P#，第 2 个轴内部编码为 S#；P/S 不再代表固定物理量。
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
    # - 单塔：仅保留一座 enabled=True 的塔；不用的塔 enabled=False 或删除；
    # - 双塔：保留两座 enabled=True 的塔；不需要额外再写 tower_count=2；
    # - 每塔 1/2/3/... 个阀：直接增删 valves，阀数量 = len(valves)；
    # - supply_flows：供浆流量测点，可配置 0/1/N 个；
    # - monitor_supply_pumps：GUI 监控供浆泵，可配置 0/1/N 台，支持频率/电流等；
    # - circulation_pumps：浆液循环泵，可配置 0/1/N 台；
    # - supply_pumps：第二模块控制约束使用的“定频泵电流拓扑”，不要把仅用于
    #   显示的变频频率测点直接塞进 supply_pumps。
    # 上述配置中的测点字段会自动进入数据库基础历史字段集合。
    "towers": [
        {
            "tower_id": "xst",
            "display_name": "一级塔",
            "enabled": True,
            "ph_column": "xstjy_PH",
            "ph_safe_range": [5.6, 6.8],
            "ph_guard_band": 0.15,

            # GUI 塔体实时测点。可按厂增删，pH 也可在此重复展示。
            "monitor_fields": [
                {
                    "column": "xstjy_PH",
                    "display_name": "浆液 pH",
                    "unit": "",
                    "digits": 2,
                },
                {
                    "column": "xstshsjy_MD",
                    "display_name": "浆液密度",
                    "unit": "kg/m³",
                    "digits": 1,
                },
                {
                    "column": "xst_YW",
                    "display_name": "吸收塔液位",
                    "unit": "m",
                    "digits": 2,
                },
            ],

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

            "supply_flows": [
                {
                    "flow_id": "xst_supply_flow_1",
                    "display_name": "供浆流量",
                    "column": "xstshsjy_LL",
                    "unit": "m³/h",
                    "digits": 1,
                },
            ],

            # GUI 供浆泵监控。这里只决定“显示多少台、显示哪个反馈值”。
            # run_threshold 只用于页面上的 RUN/STOP 提示，不参与第二模块控制约束。
            "monitor_supply_pumps": [
                {
                    "pump_id": "xst_supply_A",
                    "display_name": "供浆泵2A",
                    "value_column": "xstgjb_APL",
                    "unit": "Hz",
                    "digits": 1,
                    "run_threshold": 1.0,
                },
                {
                    "pump_id": "xst_supply_B",
                    "display_name": "供浆泵2B",
                    "value_column": "xstgjb_BPL",
                    "unit": "Hz",
                    "digits": 1,
                    "run_threshold": 1.0,
                },
            ],

            # GUI 浆液循环泵监控。数量同样完全由列表决定。
            "circulation_pumps": [
                {
                    "pump_id": "xst_circ_A",
                    "display_name": "循环泵A",
                    "value_column": "xstjyxhb_ADL",
                    "unit": "A",
                    "digits": 1,
                    "run_threshold": 5.0,
                },
                {
                    "pump_id": "xst_circ_B",
                    "display_name": "循环泵B",
                    "value_column": "xstjyxhb_BDL",
                    "unit": "A",
                    "digits": 1,
                    "run_threshold": 5.0,
                },
                {
                    "pump_id": "xst_circ_C",
                    "display_name": "循环泵C",
                    "value_column": "xstjyxhb_CDL",
                    "unit": "A",
                    "digits": 1,
                    "run_threshold": 5.0,
                },
                {
                    "pump_id": "xst_circ_D",
                    "display_name": "循环泵D",
                    "value_column": "xstjyxhb_DDL",
                    "unit": "A",
                    "digits": 1,
                    "run_threshold": 5.0,
                },
                {
                    "pump_id": "xst_circ_E",
                    "display_name": "循环泵E",
                    "value_column": "xstjyxhb_EDL",
                    "unit": "A",
                    "digits": 1,
                    "run_threshold": 5.0,
                },
            ],

            # 第二模块控制约束：定频供浆泵电流拓扑。
            # 示例：
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
        #     "monitor_fields": [
        #         {
        #             "column": "aptjy_PH",
        #             "display_name": "浆液 pH",
        #             "unit": "",
        #             "digits": 2,
        #         },
        #     ],
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
        #     "supply_flows": [],
        #     "monitor_supply_pumps": [],
        #     "circulation_pumps": [],
        #     "supply_pumps": [],
        # },
    ],
}


def enabled_towers(config: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], ...]:
    """返回当前厂所有启用塔；单塔/双塔由返回数量自然决定。"""
    plant = PLANT_CONFIG if config is None else config
    return tuple(
        tower
        for tower in (plant.get("towers", []) or [])
        if tower.get("enabled", True)
    )


def configured_persistence_columns(
    config: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """收集当前厂需要长期写入基础历史表的数值现场字段。

    通讯层可以传入更多字段，P4PC 仍会透传；数据库只需要为算法/监控/显式要求
    长期留档的字段建稳定列，避免每收到一个临时字段就动态改表结构。
    """
    plant = PLANT_CONFIG if config is None else config
    columns: list[str] = []

    def add(value: Any) -> None:
        column = str(value or "").strip()
        if column:
            columns.append(column)

    for axis in plant.get("condition_axes", []) or []:
        add(axis.get("column"))

    monitor = plant.get("realtime_monitor", {}) or {}
    for group_name in ("inlet_signals", "outlet_signals", "auxiliary_signals"):
        for item in monitor.get(group_name, []) or []:
            add(item.get("column"))

    for tower in enabled_towers(plant):
        add(tower.get("ph_column"))
        for item in tower.get("monitor_fields", []) or []:
            add(item.get("column"))
        for valve in tower.get("valves", []) or []:
            add(valve.get("column"))
        for flow in tower.get("supply_flows", []) or []:
            add(flow.get("column"))
        for pump in tower.get("monitor_supply_pumps", []) or []:
            add(pump.get("value_column"))
        for pump in tower.get("circulation_pumps", []) or []:
            add(pump.get("value_column"))
        for pump in tower.get("supply_pumps", []) or []:
            add(pump.get("current_column"))

    for item in plant.get("persistence_extra_fields", []) or []:
        if isinstance(item, Mapping):
            add(item.get("column"))
        else:
            add(item)

    return tuple(dict.fromkeys(columns))
