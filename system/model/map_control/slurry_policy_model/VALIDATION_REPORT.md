# V1.8B 验证报告

验证日期：2026-07-31

## 1. 验证目标

V1.8B 只优化临近工况策略的计算方式，验证时重点确认：

```text
空间邻域映射结果不变
每个 target/state/action 使用的 episode 集合不变
证据权重不变
P25/P50/P75 定义不变
SO₂方向、pH、安全、稳定性和可靠性不变
初次、增量、工况合并后的输出链路不变
```

## 2. 已执行正式测试

```bash
python -m compileall -q .
python tests/self_check_v1_8b.py
python tests/test_performance_equivalence.py
python tests/test_neighbor_optimization_equivalence.py
python tests/test_version_alignment_pipeline.py
python tests/test_pickle_only_pipeline.py
```

全部通过。

## 3. 临近空间映射等价验证

新旧映射分别采用：

```text
V1.8A：source grid × 全部 target condition
V1.8B：坐标反向索引 + 有限方形搜索
```

验证内容：

```text
80 组随机网格与随机合并区域
每组 20 个源基础格
每组 25 个目标工况
每个目标工况 1～6 个成员格
包含负荷轴与 SO₂轴半径不对称的边界案例
```

逐行比较：

```text
anchor_grid_id
neighbor_target_condition_label
neighbor_load_grid_offset
neighbor_inlet_so2_grid_offset
neighbor_mapping_weight
```

结果完全一致。

特别验证了以下容易产生误差的场景：

```text
一个目标工况同时存在：
- 轴向超限但 Chebyshev 距离更近的成员格
- 轴向未超限但排序次序更后的成员格
```

V1.8B 与原 `minimum_offsets_to_grids()` 的选择和拒绝结果一致。

## 4. 数值统计等价验证

验证了：

```text
三次独立排序计算 P25/P50/P75
        对比
单次排序后同时定位三个分位点
```

以及：

```text
pandas 分类别 .loc 加权求和
        对比
NumPy 编码与加权累计
```

包含缺失值、零权重、字符串类别和 UNKNOWN。所有值在绝对误差 `1e-12` 内一致。

## 5. 临近策略完整结构对照

使用随机合成 episode，分别运行 V1.8A 参考流程和 V1.8B 优化流程，覆盖：

```text
include_same_condition = True / False
include_global_only = True / False
多 condition、多 state、多 action
ACTION / HOLD
SO₂三方向与 UNKNOWN
两塔 pH、安全和三只阀门增量
```

逐层比较：

```text
neighbor target condition
state key
action_id
action_profile
so2_effect
ph_effect
stability
safety
support
spatial_support
reliability
profile_status
```

结果一致。

另外分别对同一 action group：

```text
现场字段直接进入 aggregate_action_profile
        对比
预计算安全、pH方向、标准化标签辅助列后进入 aggregate_action_profile
```

结果一致。

## 6. 全层级聚合对照

同一份包含 1200 条 episode、20 个工况、4 个状态和4个动作的合成数据，分别运行完整 V1.8A 和 V1.8B `aggregate_all_levels()`。

逐字段比较：

```text
conditions
condition_grids
neighbor_state
plant_action_prior
transients
```

字典键、事件数、权重、分布、可靠性和代表性阀门增量全部一致；浮点比较容差为 `1e-12`。

## 7. 初次与增量端到端测试

通过现有端到端测试验证：

```text
第一模块 v001
→ 第二模块初次 v001
→ 第一模块 v002 合并工况
→ 第二模块历史 episode 按 anchor_grid_id 重映射
→ 生成第二模块 v002
```

检查通过：

```text
旧工况目录不残留
合并后新工况继承旧 episode
condition_policy.pkl 正常生成
neighbor_state_policy.pkl 正常生成
plant_action_prior.pkl 正常生成
condition_remap_report.json 正常生成
manifest 与 Pickle/CSV 继承正常
```

## 8. 合成性能对照

以下结果只表示本次容器中的受控合成数据，不代表现场固定倍数。

### 8.1 空间映射表

测试规模：

```text
1848 个源基础格
480 个目标工况
每个目标工况 2 个成员格
输出映射 16252 行
```

结果：

```text
V1.8A：1.720 秒
V1.8B：0.043 秒
```

空间映射阶段约为原耗时的 2.5%。

### 8.2 临近策略阶段

测试规模：

```text
1200 条 episode
20 个目标工况
320 个 target/state/action profile
```

结果：

```text
V1.8A：13.709 秒
V1.8B：6.912 秒
```

本次临近策略阶段耗时下降约 49.6%。

### 8.3 全层级聚合

同一份 1200 条 episode 数据：

```text
V1.8A：15.334 秒
V1.8B：约 10.0～10.5 秒
```

本次完整聚合阶段耗时下降约 31%～35%。

## 9. 未改变的逻辑

本次未修改：

```text
ACTION / HOLD 识别
动作阈值和强度分级
基线、迟滞、响应窗口
FAST 判定与训练路由
工况版本对齐和历史重映射
邻域 ±2 / ±3
距离权重函数
minimum_mapping_weight
include_same_condition
include_global_only
SO₂响应方向
pH约束
稳定性和安全性
可靠性公式
输出 PKL / JSON 的字段含义
```

## 10. 仍然存在的性能边界

V1.8B 仍然为每个最终 `target condition × state × action` 构建完整档案。因此当状态键和动作种类非常多时，档案数量本身仍会带来计算成本。

本版本没有采用以下会改变产物完整度的办法：

```text
缩小邻域
抽样 episode
减少 HOLD
只为低支持状态生成临近策略
近似分位数
跳过部分 profile
只重建脏工况
```

现场运行后应查看 `performance_report.json` 中：

```text
neighbor_build_mapping_table
neighbor_batch_merge
neighbor_aggregate_profiles
neighbor_action_profile_count
neighbor_total_expanded_row_count
```

若主要耗时仍集中在 `neighbor_aggregate_profiles`，说明瓶颈已不是空间匹配，而是实际需要生成的状态—动作档案数量。
