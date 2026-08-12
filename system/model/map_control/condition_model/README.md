# 第一模块：condition_model 工况划分

`condition_model` 是供浆控制系统的第一模块，负责把连续过程数据映射到稳定、可版本化的工况标签，并把 `condition_label` 等字段追加到原始数据后交给第二模块。

## 1. 模块职责

第一模块只负责“当前属于哪个工况”，不直接给供浆阀动作。

完整链路：

```text
历史/实时过程数据
    ↓
按配置工况轴划分基础网格
    ↓
统计每个基础格历史状态
    ↓
相邻基础格自动合并
    ↓
生成 condition_snapshot.json
    ↓
在线瞬时工况识别
    ↓
滑动窗口众数稳定
    ↓
condition_label / grid_id / state_key ...
    ↓
第二模块 slurry_policy_model
```

## 2. 厂级配置只改一处

换厂时不要在本目录重复修改现场字段。统一厂级配置位于：

```text
system/model/config/plant_config.py
```

第一模块从这里读取：

- 1 个或 2 个任意工况轴；
- 工况轴字段、范围和步长；
- 净烟气 SO2 字段及安全上限；
- 液气比字段；
- 当前启用塔的 pH 字段。

例如只使用原烟气 SO2：

```python
"condition_axes": [
    {
        "column": "yyq_SO2",
        "min": 500.0,
        "max": 7000.0,
        "step": 200.0,
    }
]
```

例如钢厂使用两个其他变量：

```python
"condition_axes": [
    {"column": "gas_flow", "min": 1000.0, "max": 5000.0, "step": 200.0},
    {"column": "inlet_sulfur", "min": 100.0, "max": 3000.0, "step": 100.0},
]
```

当前固定支持 1 或 2 个工况轴。三维以上会显著增加历史经验稀疏，不建议直接扩展笛卡尔网格。

## 3. condition_config.py 负责什么

`condition_config.py` 不再维护厂级现场事实，只保留第一模块算法参数，例如：

- `DEFAULT_MERGE_CONFIG`：工况成熟度和自动合并阈值；
- `DEFAULT_ONLINE_CONFIG`：在线稳定窗口；
- `out_of_range_policy`：越界处理；
- standalone 运行时的项目内默认路径。

默认在线稳定方式：

```text
最近 6 次瞬时 condition_label
        ↓
取众数
        ↓
并列时保持上一稳定标签
```

## 4. 基础网格

工况轴按 `plant_config.py` 中的 `min / max / step` 建立固定网格。

内部网格 ID 仍使用：

```text
P1-S1
P1-S2
P2-S1
...
```

其中 `P/S` 只是第一轴/第二轴的内部编码，不再代表固定的 Power / SO2。

单轴模式内部仍保留一个单格第二槽，因此网格类似：

```text
P1-S1
P2-S1
P3-S1
```

这个 `S1` 只是兼容内部结构，不要求第二个现场测点。

## 5. 自动合并

基础格满足证据条件后可自动合并成策略区域，不需要人工确认。

生命周期：

```text
独立基础格
    ↓
AUTO_PROVISIONAL_MERGE
    ↓  后续增量持续有新增证据
AUTO_CONFIRMED_MERGE
    ↓  后续结构证据不再成立
自动收缩 / 拆分 / 移除
```

主要判断包括：

- 基础格必须相邻；
- 样本数达到门槛；
- 液气比有效覆盖率和均值差满足阈值；
- 循环泵状态分布相近；
- 净烟气 SO2 风险率证据充分且差异可接受；
- 合并区域保持矩形；
- 区域大小不超过配置上限。

默认关键参数仍在 `condition_config.py -> DEFAULT_MERGE_CONFIG` 中维护。

`condition_merge_statistics.json` 是累计统计辅助文件；正式在线标签以 `condition_snapshot.json` 为准。

## 6. pH 在第一模块中的作用

pH 只用于工况解释统计，不决定基础工况坐标。

单塔厂没有二级塔 pH 时不会报错；第一模块会：

```text
有该塔 pH → 统计均值
没有该塔 pH → 对应统计保持空值
```

第二模块的 pH 安全控制仍按中央 `plant_config.py` 中每个 enabled 塔的配置执行。

## 7. 初次训练

P4PC 正式流程：

```text
原始训练数据
→ system/model/map_control/model_csv/Initial_train.csv
→ initial_condition_builder.py
→ Initial_train_after_condition.csv
→ snapshots/v001/condition_snapshot.json
→ 第二模块初次训练
```

独立运行可使用：

```bash
python system/model/map_control/condition_model/initial_condition_builder.py \
  --input <input.csv> \
  --output <output.csv> \
  --snapshot-output <condition_snapshot.json> \
  --snapshot-version v001
```

## 8. 增量训练

P4PC 正式流程：

```text
新增历史数据
→ Update_train.csv
→ 读取当前 active condition vN
→ incremental_condition_updater.py
→ Incremental_train_after_condition.csv
→ condition snapshot vN+1
→ 第二模块增量训练 vN+1
→ 两模块同版本验证后统一激活
```

增量训练失败不会覆盖当前激活版本。

## 9. 在线输出

第一模块保留原始输入字段，并追加稳定接口字段，主要包括：

```text
condition_snapshot_version
grid_id
base_condition_id
condition_label
policy_region_id
region_status
region_member_count
coverage_status
state_key
condition_experience_source
condition_valid
out_of_range_clipped
clip_axis
condition_reason
```

在线还会产生 `stable_condition_label / condition_stable / condition_switch_state` 等稳定状态信息供第二模块使用。

## 10. 版本管理

第一模块不能单独把新快照抢先上线。

正式运行采用：

```text
condition vN + policy vN
        ↓
训练 condition vN+1
        ↓
训练 policy vN+1
        ↓
验证版本与哈希一致
        ↓
原子更新 active_version.json
```

线上始终使用同版本的第一、第二模块组合。

## 11. 核心文件

```text
condition_config.py                 第一模块算法配置
initial_condition_builder.py        初次训练
incremental_condition_updater.py    增量训练
online_condition_classifier.py      在线工况识别
condition_merger.py                 两工况合并证据判断
auto_merge_manager.py               自动区域生命周期
condition_schema.py                 快照/统计结构
grid_definition.py                  网格映射
snapshot_io.py                      快照读写
integrated_version_manager.py       两模块统一版本切换
online_condition_policy_bridge.py   第一模块→第二模块在线桥接
```

## 12. 测试

开发过程中的大量一次性测试已合并成一个核心回归文件：

```text
system/model/map_control/condition_model/tests/test_core_regression.py
```

运行：

```bash
python system/model/map_control/condition_model/tests/test_core_regression.py
```

它只保护最容易在后续改动中被破坏的结构性能力：

- 任意单轴/双轴工况；
- 初次快照、增量快照和在线识别一致性；
- 单塔/缺失第二塔 pH；
- 自动临时合并、确认与拆分生命周期。

这些测试使用小规模合成数据是为了快速验证程序不变量，不代表生产训练只使用少量数据。真实模型训练仍使用完整历史 CSV/数据库数据。
