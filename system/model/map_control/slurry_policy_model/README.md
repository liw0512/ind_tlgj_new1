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
第一模块统一在线入口传入 condition_label + 当前过程状态 + 动态目标
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

## 3. 离线输入

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

第二模块同时学习：

```text
ACTION：阀门发生有效变化
HOLD：阀门持续保持
```

历史响应采用：

```text
动作前 baseline
→ 动作开始/结束
→ response_delay
→ response_window
```

如果响应窗口中又发生新动作、供浆泵启停等无法归因的扰动，对应 episode 可以判为 INVALID。

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

得到塔级等效增加 3%，不是简单相加成 6%。

## 6. 单塔、双塔和任意阀门数量

结构全部跟随中央 `plant_config.py`。

单塔厂只启用一级塔时，只读取一级塔 pH、阀门和供浆泵；双塔时才同时存在两个塔级控制对象。一座塔有 1/2/3 个供浆阀时，只需要在 `valves` 中增删配置，算法本身不需要改。

普通在线决策原则上一次只选择一座塔执行非 HOLD 动作；多塔同时动作历史保留作审计和 FAST 场景扩展。

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

描述，因此支持一泵一阀、一泵多阀、多泵共阀和共母管。

一个阀只要至少有一台服务泵状态为 1，就认为供浆路径可用。如果模型原计划两阀各 +3%，但其中一条独立泵支路停止，则只保留可用阀的 +3%，不会补偿成 +6%。所有服务路径均停止时，该塔动作直接不可执行。

## 8. 动态 SO2 目标与 pH 安全

离线模型不绑定固定目标，只学习动作历史响应。在线使用当前 `effective_target` 重新评价动作是否合适。

pH 不作为大量离散状态，而是在候选执行前按当前 pH、历史 ΔpH 和该塔 `ph_safe_range` 做连续安全判断；预测越界时直接过滤动作。

## 9. 在线候选和动作解析

普通模式候选顺序：

```text
LOCAL_CONDITION
→ NEIGHBOR_STATE
→ PLANT_ACTION_PRIOR
→ RULE_BASELINE
```

FAST 模式：

```text
TRANSIENT
→ RULE_BASELINE
```

候选随后经过目标方向、pH、安全历史、手动/故障阀、供浆泵可用性、动作强度和在线状态机约束。

选出塔级动作后，`ValveActionResolver` 将塔级等效动作映射到当前厂实际存在且可用的阀门，并执行阀位上下限、单次最大动作和最小有效动作等限制。

主要输出：

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

## 10. 执行反馈

第二模块只给推荐，不直接写 DCS。实际执行后由上层调用：

```python
pipeline.record_execution({...})
```

只有真实执行反馈才会推动 `WAITING_EFFECT`、动作间隔、反向锁等在线状态机。

## 11. 初次与增量训练

初次：

```text
Initial_train_after_condition.csv
+ condition snapshot v001
→ initial_slurry_policy_trainer.py
→ policy snapshot v001
→ activate_policy_version.py
→ active_version.json
```

增量：

```text
当前 active vN
+ Incremental_train_after_condition.csv
+ condition snapshot vN+1
→ 继承并重映射历史 episode
→ 增加新 episode
→ policy vN+1
→ 两模块同版本验证
→ 原子激活 vN+1
```

增量失败时线上继续使用旧版本。

## 12. 统一在线入口

第二模块在线**不再作为独立主入口运行**。正式在线和历史 CSV 快速回放统一从第一模块进入：

```text
system/model/map_control/condition_model/online_condition_classifier.py
```

该文件内部长期持有：

```text
OnlineConditionClassifier
        ↓
OnlineConditionPolicyPipeline
        ↓
SlurryPolicyOnlineBridge
        ↓
OnlineSlurryPolicy
```

因此一条实时数据的完整路径是：

```text
原始实时 row
→ 第一模块工况判定
→ condition_label / grid_id / state_key
→ 第二模块在线判定
→ 动作候选、安全过滤、状态机、阀门解析
→ 第一模块字段 + slurry_policy_* 字段联合输出
```

P4PC 也复用同一个 `build_online_condition_policy_pipeline()` 和长期 `pipeline.process()`，没有第二套在线实现。

## 13. 历史 CSV 快速回放完整在线链

训练并激活一组同版本模型后，可以直接运行第一模块在线文件，把历史 CSV 一行一行当成实时数据送入完整在线 Pipeline：

```bash
python system/model/map_control/condition_model/online_condition_classifier.py \
  --snapshot active \
  --input <历史测试数据.csv> \
  --output <online_replay_result.csv> \
  --target 20
```

或者由 CSV 每行提供动态目标：

```bash
python system/model/map_control/condition_model/online_condition_classifier.py \
  --snapshot active \
  --input <历史测试数据.csv> \
  --output <online_replay_result.csv> \
  --target-column outlet_so2_target
```

输出 CSV 同时包含：

```text
原始历史字段
+ 第一模块 condition 字段
+ 第二模块 slurry_policy_* 字段
```

回放过程中同一个 Pipeline 对象持续存在，所以第一模块多数窗口、第二模块 FAST/目标/状态机等内存状态都会跨行保留。它用于检查算法在历史现场状态下会如何判工况、选经验、过滤动作和给出阀门推荐，不会直接写 DCS。

## 14. 版本和快照

线上唯一正式版本入口是：

```text
active_version.json
```

激活时校验 condition/policy 版本、condition snapshot 哈希、grid-condition 映射及厂级塔/pH/阀门/供浆泵拓扑一致性。工况轴或设备拓扑发生结构变化时，应重新初次训练，不能让旧 snapshot 静默继续工作。

## 15. 核心文件

```text
slurry_policy_config.py              第二模块算法配置
initial_slurry_policy_trainer.py     初次训练入口
incremental_slurry_policy_trainer.py 增量训练入口
slurry_policy_core.py                离线核心流程
_engine/                              episode、聚合、快照等公共引擎
slurry_policy_online/                 在线候选、目标、安全、状态机和动作解析
activate_policy_version.py           同版本校验和激活
p4pc_slurry_policy_config.py         P4PC兼容配置入口
```

模块内不再保留 `test_core_regression.py` 一类合成测试。需要验证完整在线行为时，使用上述历史 CSV 回放入口。