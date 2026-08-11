# 配置参数总览

> 本文用于快速定位“应该改哪个配置文件”。Python 配置文件中的每个参数已补充行内中文注释。
> `cost_calculator_config.json` 必须保持标准 JSON，不能直接写 `#` 或 `//` 注释，因此其参数说明集中在本文末尾。

## 1. 换厂、改单塔/双塔、修改泵数量与功率

优先修改：`system/model/map_control/project_config.py`

- `xst_pump_count`：一级塔泵数量。
- `apt_pump_count`：二级塔泵数量；单塔为 `0`。
- `min_running_pumps`：全系统最少运行泵数。
- `pump_current_mapping`：泵位索引到电流测点字段。
- `pump_status_columns`：各泵状态字段，顺序就是组合字符串位序。
- `pump_power_config` / `pump_powers`：泵功率，顺序必须与状态位一致。
- `valid_pump_patterns`：合法泵数模式 `(一级塔台数, 二级塔台数)`。
- `forbidden_pump_combinations`：明确禁止的具体组合；当前为空。

修改这里后，工况、Q-learning 和 fast_change 会自动读取同一套泵配置。

## 2. 工况网格、合并、标签稳定与 fast_change

修改：`system/model/map_control/cluster/cluster_config.py`

常改项：

- `DATA_FILE_PATH`：初次工况划分输入数据。
- `CLUSTER_FEATURES`：决定基础工况的字段；当前只使用 `yyq_SO2`。
- `GRID_DEFINITION`：每个工况字段的 `min/max/step`。
- `LG_*`：液气比合并统计和阈值。
- `PUMP_CONSISTENCY_THRESHOLD`：工况内泵组一致性要求。
- `FAST_TREND_*`：原烟气 SO₂ 快变趋势参数。
- `FAST_EFFECT_JYQ_SO2_THRESHOLD`：净烟气 SO₂ 效果风险阈值。
- `FAST_ADD_XST_PH_MIN`：单塔快变增泵最低 pH。
- `FAST_ADD_APT_PH_MIN`：双塔二级塔最低 pH。
- `NON_FAST_SO2_*`：稳态降泵 SO₂ 门控。

## 3. Q-learning 初次训练、增量训练和在线推荐

修改：`system/model/map_control/q_learning/common/config.py`

常改项：

- 路径：`DATA_PATH`、`RESULTS_DIR`、`UPDATE_RESULTS_DIR`。
- 学习：`ALPHA`、`GAMMA`、`BATCH_SIZE`、`EPISODES`。
- 初次奖励：`INITIAL_*_WEIGHT`。
- 增量奖励：`INCREMENTAL_*_WEIGHT`。
- SO₂ 奖励：`SO2_REWARD_RANGE_*`、`SO2_TARGET_CENTER`、高斯宽度参数。
- 启停硬约束：`MAX_DAILY_SWITCHES`、`MAX_SWITCHES_PER_ACTION`、`PREDICT_MIN_COOLING_PERIOD`。
- 分层推荐：`ONLINE_Q_TOP_RATIO`、`ONLINE_Q_TOP_MIN_CANDIDATES`。
- 同功率防跳：`ONLINE_BLOCK_EQUAL_POWER_SWAP`、`ONLINE_EQUAL_POWER_TOLERANCE`。
- Top-K 在线反馈：所有 `TOPK_*` 参数。

注意：修改泵数量、功率和字段时不要在这里重复改，应修改 `project_config.py`。

## 4. pH 预测模型

修改：`system/model/map_control/PH_predict/ph_model_config.py`

- `FEATURE_CONFIG`：必需、可选、字符串和泵状态特征。
- `FEATURE_PREPROCESSING`：泵状态编码和字符串字段处理。
- `FEATURE_GENERATION`：pH 合并与差分窗口。
- `MODEL_PARAMS`：XGBoost 参数。
- `DATA_PROCESSING`：训练/测试划分。

单塔需保证 `xst` 字段完整；二级塔不存在时不要要求 `apt` 字段参与实际训练。

## 5. 原始数据限幅、滤波和泵状态识别

修改：`system/base/config/DataPreprocessorConfig.py`

- `limit_config`：每个测点的物理上下限。
- `filter_config`：滤波算法和窗口。
- `current_threshold`：泵运行电流阈值。
- `pump_base_values`：每台泵基准值。
- `min_data_required`：滤波启动最少样本数。
- `feature_generation`：派生特征开关。

## 6. 部署环境、数据库和界面

入口：`system/base/config/SysConfig.py`

具体环境：

- `SysConfigDev4Linux.py`
- `SysConfigTest.py`
- `SysConfigProd.py`

只在 `SysConfig.py` 中启用一套环境。不同机器需修改部署路径、数据库连接、Python 解释器和界面塔/泵列表。

## 7. 成本计算 JSON 参数

文件：`cost_calculator_config.json`

JSON 不支持注释，参数含义如下：

### electricity

- `U`：电压系数或电压归一化因子，单位由成本公式决定。
- `cos_phi`：功率因数，通常为 `0~1`。
- `mu`：电机或传动综合效率系数，通常为 `0~1`。
- `P_elec`：电价，单位应与最终成本时间尺度一致。

### limestone

- `K`：石灰石消耗换算系数。
- `M`：石灰石摩尔质量或公式使用的质量换算量。
- `P_stone`：石灰石单价。

### pollution

- `F`：污染物或副产物折算系数。
- `P_pollute`：污染物排放成本单价。
- `eta_gypsum`：石膏产出/回收效率，通常为 `0~1`。
- `P_gypsum`：石膏收益或价格。
- `C_clean_dust`：净烟气粉尘相关成本系数。

### water

- `S_ye`：液体或废水折算量。
- `P_shui`：水价或废水处理单价。

### equipment_config

每类设备包含：

- `prefix`：实时测点字段前缀。
- `devices`：该类设备列表。
- `suffix`：设备字段后缀，例如 `ADL`。
- `rated_power`：额定功率，通常单位为 kW。
- `efficiency`：设备效率，范围通常为 `0~1`。

### 其他

- `current_threshold`：设备运行判定电流阈值。
- `output_mapping`：设备前缀到成本计算输出字段的映射。

## 修改后的生效方式

- 仅路径、界面、在线阈值变化：重启在线进程。
- 工况网格或合并参数变化：重新运行工况初次/增量流程并发布新快照。
- Q-learning 奖励、状态离散或训练参数变化：重新训练对应 Q 表。
- pH 特征或模型参数变化：重新训练 pH 模型。
- 泵数量、字段顺序或功率变化：至少重新加载配置；涉及 Q 表状态位变化时需要重新训练 Q 表。

## Process4MapControl 专用配置

文件：`system/model/config/process4map_config.py`

- `UnitStopConfig`：停机字段、比较方式、阈值和持续时间；字段可选 `yyq_SO2`、`jzfh`、`glfl` 等。
- `DataValidationConfig`：jym 校验码、净烟气 SO2 有效性阈值和校验窗口。
- `RuntimeConfig`：队列容量、线程数、快照周期、断线宽限和维护周期。
- `TrainingConfig`：初次/增量训练数据天数、样本量、模型备份保留时间。
- `PersistenceConfig`：过滤表和模型结果表前缀。
- `input_fields` / `limits`：Process4MapControl 输入字段和限幅范围。

## 单塔 pH 预测

`system/model/map_control/PH_predict/ph_model_config.py` 当前只配置一级塔 `xstjy_PH`。
二级塔 APT 特征、目标、模型文件和在线输出已经取消。旧双塔模型不兼容，需重新执行 `ph_main.py` 初次训练。

### Process4MapControl 自动训练数据源与路径

`system/model/config/process4map_config.py -> TrainingConfig` 统一配置初次/增量训练的数据来源、周期、数据库取数条数、最少记录数、工作 CSV、训练脚本和模型临时目录。

- `initial_data_source` / `incremental_data_source`：`database` 或 `csv`。
- `initial_source_csv` / `incremental_source_csv`：测试 CSV 路径。
- `incremental_trigger_interval_days`：增量周期；切换 CSV 不会绕过周期。
- `*_database_record_limit`：实际数据库读取上限；0 表示天数乘每天记录数。
- `*_minimum_records`：对应阶段允许训练的最少数据量。
