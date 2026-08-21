# 第二模块：供浆流量动作学习与在线策略

`slurry_policy_model` 读取第一模块已经标注工况的历史数据，学习实际供浆流量变化与净烟气 SO2、吸收塔 pH 响应之间的关系。第二模块的规范动作是塔级目标供浆流量，不是阀门开度。

## 模块边界

```text
第一模块标注后的 10 秒数据
    ↓
识别实际供浆流量 STEP / PULSE / BOOST_STEP 事件
    ↓
过滤泵切换、循环泵变化、工况切换等不可归因片段
    ↓
统计峰值、最终流量、持续时间及 SO2 / pH 效果
    ↓
生成 supply_flow_prototypes.pkl
    ↓
按当前工况、控制需求、安全状态选择流量原型
    ↓
输出 TARGET_SUPPLY_FLOW
```

本模块不划分工况、不直接写 DCS，也不输出阀门增量。阀位和泵状态可以作为现场状态或监测量保留，但不会成为推荐动作或目标流量不可用时的回退控制量。

## 离线训练

输入为第一模块输出的带工况标签 CSV。历史数据中的实际供浆流量用于识别三类动作：

- `STEP`：流量变化后保持在新平台；
- `PULSE`：流量达到峰值后回到原平台附近；
- `BOOST_STEP`：先达到强化峰值，再稳定在新的最终平台。

只有动作形态可学习、上下文干净、效果窗口完整、工况有效且满足越界规则的片段，才以 `FLOW_ACTION / FLOW_POLICY` 进入原型统计。离线训练不再提取阀门 ACTION/HOLD，也不再生成局部阀门策略、邻近阀门策略或全厂阀门先验。

主要产物：

```text
policy snapshot v###/
  global/supply_flow_prototypes.pkl
  valid_episodes.csv
  invalid_episodes.csv
  training_summary.json
  manifest.json
```

初次训练与增量训练均使用同一套 `ACTUAL_SUPPLY_FLOW_V1` 语义。增量版本继承并去重历史流量事件，再与新数据一起重建供浆流量原型。

## 在线推理

在线链路按当前稳定工况、SO2 目标偏差、塔 pH 安全状态和供浆流量原型可靠性选择动作。唯一规范输出为：

```text
recommendation_type = TARGET_SUPPLY_FLOW
tower_id
action_direction
flow_shape
current_flow
target_peak_flow
target_final_flow
target_peak_flow_range
target_final_flow_range
```

没有合格原型、流量计不完整、pH 不安全或状态机要求等待时，输出 `HOLD`，不会退回旧阀门建议。

## 执行与反馈

当前 `TargetFlowExecutionAdapter` 为 `DRY_RUN`：它只生成目标流量执行预览并校验方向、目标区间和反馈契约，不执行任何 DCS 写操作。现场执行层完成工程限幅和联锁后，应通过 `record_execution()` 回传真实执行结果。状态机会继续跟踪流量是否到达峰值/最终值，并在响应窗口内评估 SO2 和 pH 效果。

## 配置

- 厂级测点与塔结构：`system/model/config/plant_config.py`
- 第二模块离线/在线参数：`slurry_policy_config.py`
- 当前规范输出：`ONLINE_POLICY_CONFIG["control_output"]["type"] = "TARGET_SUPPLY_FLOW"`
- 当前执行边界：`ONLINE_POLICY_CONFIG["target_flow_execution"]["adapter_mode"] = "DRY_RUN"`

## 10 秒数据语义

在线每 10 秒决策一次。第 10 秒输入使用第 8、9、10 秒连续量的均值；0/1 状态量取第 10 秒当前值。离线训练使用基于原始 1 秒 CSV 按同一规则得到的 10 秒语义数据，保证训练与在线一致。
