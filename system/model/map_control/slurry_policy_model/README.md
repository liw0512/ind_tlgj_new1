# 第二模块：slurry_policy_model 供浆动作学习与在线策略

`slurry_policy_model` 是供浆控制系统的第二模块。它读取第一模块已经标注 `condition_label` 的历史数据，从历史 ACTION/HOLD 片段中学习塔级供浆动作对净烟气 SO2 和塔 pH 的响应，并在在线阶段结合动态目标、安全约束和现场设备状态给出阀门推荐动作。

## 1. 模块边界

第二模块不负责工况划分，也不直接写 DCS。

完整链路：

```text
第一模块标注 CSV
    ↓
历史 ACTION / HOLD 提取
    ↓
塔级动作归一化
    ↓
SO2 / pH 响应统计
    ↓
LOCAL / NEIGHBOR / PLANT PRIOR / TRANSIENT 经验
    ↓
policy snapshot v###
    ↓
在线读取 condition_label + 当前过程状态 + 动态目标
    ↓
候选检索、过滤、排序
    ↓
塔级动作 → 实际阀门增量
    ↓
recommended_valve_deltas / projected_valve_openings
    ↓
P4PC / MainControl 最终联锁和执行
```

## 2. 厂级配置只改一处

换厂时所有现场物理结构统一配置在：

```text
system/model/config/plant_config.py
```

第二模块不再重复维护：

- 时间字段；
- 净烟气 SO2 字段和安全范围；
- 工况轴；
- 单塔/双塔；
- 各塔 pH 字段和安全范围；
- 阀门数量、字段和量程；
- 定频供浆泵电流字段、阈值和 pump→valve 拓扑；
- 在线 SO2 目标字段。

`slurry_policy_config.py` 只保留算法参数，例如响应窗口、动作强度、可靠性、在线控制和执行限幅。

## 3. 输入

第二模块离线训练输入必须是第一模块刚生成的带工况标签 CSV：

```text
初次：Initial_train_after_condition.csv
增量：Incremental_train_after_condition.csv
```

原始过程字段会被第一模块完整保留，因此第二模块能够继续读取：

- 当前工况轴原始值；
- 净烟气 SO2；
- 各 enabled 塔 pH；
- 各供浆阀开度；
- 配置的定频供浆泵电流；
- 第一模块 `condition_label / grid_id / state_key / version` 等字段。

## 4. ACTION 与 HOLD

第二模块同时学习两类历史：

```text
ACTION：阀门发生有效变化
HOLD：阀门持续保持，但过程结果稳定/变化
```

保留 HOLD 的目的，是让系统知道“什么情况下不动作也是合理策略”，避免历史库只剩操作事件。

历史动作按每个阀的 `action_threshold` 判定真实变化，响应评价采用：

```text
动作前 baseline
→ 动作开始/结束
→ response_delay
→ response_window
```

如果响应窗口中又发生新动作、供浆泵启停等无法归因的扰动，对应 episode 可被判 INVALID，但仍可留作审计。

## 5. 塔级动作语义

策略学习的是“哪座塔增加/减少供浆”，而不是照搬操作员当时如何分配每一只阀。

动作族主要为：

```text
TOWER:xst|SUPPLY
TOWER:apt|SUPPLY
```

方向：

```text
INCREASE / DECREASE / HOLD
```

强度：

```text
MICRO / SMALL / MEDIUM / STRONG
```

同塔多阀的塔级动作幅度使用各阀归一化变化的平均值。例如两个 0~100 阀历史分别 +2、+4：

```text
(2/100 + 4/100) / 2 = 0.03
```

得到塔级等效增加 3%，不是把两个阀简单相加成 6%。

## 6. 单塔、双塔和任意阀门数量

结构全部跟随中央 `plant_config.py`。

单塔厂只启用一级塔时：

```text
只读取一级塔 pH
只识别一级塔阀门动作
只生成一级塔候选
```

一座塔有 1/2/3 个供浆阀时，只需要在 `valves` 中增删配置，算法本身不需要改。

普通在线决策原则上一次只选择一座塔执行非 HOLD 动作；多塔同时动作的历史仍可保留作审计，但不作为普通模式直接执行的首选经验。

## 7. 定频供浆泵可用性

供浆泵当前按定频泵处理：

```text
current > run_current_threshold → 1 运行
current <= threshold            → 0 停止
缺失 / NaN                       → 0 fail-safe
```

泵与阀的拓扑由：

```python
"served_valve_ids": ["xst_v1", "xst_v2"]
```

描述。

因此自然支持：

- 一泵一阀；
- 一泵多阀；
- 多泵共用一个阀；
- 多泵共母管服务多个阀。

一个阀只要至少有一台服务泵状态为 1，就认为供浆路径可用。

如果模型原计划两阀各 +3%，但其中一条独立泵支路停止，则只保留可用阀的 +3%，不会把另一条支路损失的动作自动补偿成 +6%。所有服务路径均停止时，该塔候选被直接过滤。

离线 episode 也使用同一套 0/1 判定：正常运行电流小幅波动不会误判为泵切换，只有跨过阈值导致 0↔1 变化才记为供浆泵状态变化。

## 8. 动态 SO2 目标

离线模型不绑定 15、20 等固定目标，只学习动作产生的历史 SO2 响应。

在线使用：

```text
error = current_so2 - effective_target
predicted_so2_after = current_so2 + historical_delta_so2
```

同一经验层级优先选择预测后更接近当前动态目标、同时满足安全约束的动作。

`outlet_so2_safe_range` 是硬安全范围，不等于控制目标。

在线同时维护：

```text
commanded_target：操作员/DCS目标
effective_target：经过目标缓变后真正参与控制的目标
```

## 9. pH 安全

pH 不再作为大量离散经验桶维度，而是在候选执行前做在线安全判断。

对某座塔的增加/减少动作，使用该动作历史 pH 响应的保守统计估计：

```text
predicted_ph_after = current_ph + historical_delta_ph_guard
```

预测超出该塔 `ph_safe_range` 时直接过滤该候选。

## 10. 在线候选层级

普通模式：

```text
LOCAL_CONDITION
→ NEIGHBOR_STATE
→ PLANT_ACTION_PRIOR
→ RULE_BASELINE
```

快变模式：

```text
TRANSIENT
→ RULE_BASELINE
```

默认 FAST 缺少匹配历史时不回退到普通本地经济策略。

候选随后经过：

- 当前目标方向；
- pH 安全；
- 手动/故障阀；
- 供浆泵可用性；
- 历史可靠性和安全性；
- 动作强度限制；
- WAITING_EFFECT、反向锁、动作间隔等状态机限制。

## 11. 阀门动作解析

选出塔级动作后，`ValveActionResolver` 将历史塔级等效动作重新映射到当前厂实际存在且可用的阀门。

最终继续执行：

```text
阀门 min/max
valve_limit_margin
单阀最大动作限制
minimum_command_delta
每个阀 action_threshold
供浆泵可用性
```

输出主要包括：

```text
action_family
action_direction
action_magnitude
recommended_valve_deltas
projected_valve_openings
active_valve_ids
active_tower_ids
reason_codes
```

第二模块只生成推荐，不直接写 DCS。

## 12. 执行反馈

实际联锁和下发由上层完成。真正执行后应调用：

```python
policy.record_execution({...})
```

只有收到实际执行反馈，在线状态机才进入响应等待；未执行的推荐不会被错误当成已执行动作。

## 13. 初次训练

P4PC 正式链路：

```text
Initial_train_after_condition.csv
+ condition snapshot v001
→ initial_slurry_policy_trainer.py
→ policy snapshot v001
→ activate_policy_version.py
→ active_version.json
```

独立运行示例：

```bash
python system/model/map_control/slurry_policy_model/initial_slurry_policy_trainer.py \
  --input <Initial_train_after_condition.csv> \
  --condition-snapshot <condition_snapshot.json> \
  --output <output_root>
```

## 14. 增量训练

```text
当前 active vN
+ Incremental_train_after_condition.csv
+ condition snapshot vN+1
→ 读取历史 episode
→ 按新 grid→condition 映射重排
→ 增加新 episode
→ 生成 policy vN+1
→ 两模块同版本验证
→ 原子激活 vN+1
```

增量失败时线上继续使用旧 vN。

## 15. 版本和快照

线上唯一正式入口是：

```text
active_version.json
```

激活时校验：

- condition/policy 版本一致；
- condition snapshot 哈希；
- grid-condition 映射一致；
- 厂级塔/pH/阀门/供浆泵拓扑一致；
- snapshot manifest 必要文件有效。

改动厂级工况轴、塔、阀门、pH、供浆泵拓扑等结构后，不允许旧 snapshot 静默继续作为新结构使用。

## 16. 核心文件

```text
slurry_policy_config.py             第二模块算法配置
initial_slurry_policy_trainer.py    初次训练入口
incremental_slurry_policy_trainer.py 增量训练入口
slurry_policy_core.py               离线核心流程
_engine/                             episode、聚合、校准、快照等公共引擎
slurry_policy_online/                在线候选、目标、安全、状态机和动作解析
activate_policy_version.py          同版本校验和激活
p4pc_slurry_policy_config.py        P4PC兼容配置入口
```

## 17. 测试

开发阶段产生的多份 `test_*.py`、性能等价脚本和版本自检已收敛为：

```text
system/model/map_control/slurry_policy_model/tests/test_core_regression.py
```

运行：

```bash
python system/model/map_control/slurry_policy_model/tests/test_core_regression.py
```

保留的测试只覆盖容易在以后修改时破坏的核心结构：

- 任意工况轴不重新写死 `jzfh / yyq_SO2`；
- 单塔/双塔和任意阀数量；
- 塔级等效动作计算；
- 定频泵 0/1 判定；
- 一泵多阀、多泵共阀、独立支路；
- 停泵支路不动作、不做动作补偿；
- 全部供浆路径停止时过滤塔动作。

测试使用小规模合成场景只是为了快速验证结构不变量，不代表实际训练使用单条数据。真实离线训练仍使用完整历史数据，并通过 baseline/action/response 时间窗口抽取大量 episode。
