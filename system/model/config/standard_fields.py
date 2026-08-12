"""供浆算法标准数据接口字段。

这些字段不是“厂级可配置映射”，而是 data_preprocessor1 之后的固定接口契约。
不同现场原始 DCS 点名如果不同，应在数据接入/预处理层映射成这些标准字段；
condition_model、slurry_policy_model、P4PC 和数据库之后都直接使用同名字段。
"""
from __future__ import annotations

TIME_COLUMN = "date"
OUTLET_SO2_COLUMN = "jyq_SO2"
LIQUID_GAS_RATIO_COLUMN = "liquid_gas_ratio"
TARGET_SO2_COLUMN = "outlet_so2_target"

STANDARD_PROCESS_FIELDS = (
    TIME_COLUMN,
    OUTLET_SO2_COLUMN,
    LIQUID_GAS_RATIO_COLUMN,
    TARGET_SO2_COLUMN,
)
