# 湿法脱硫供浆 V3 工况模块——无人值守自动合并版 V2

## 1. 实现流程

```text
初次/增量数据累计
    → 生成全部上下左右相邻基础格候选
    → ConditionMerger 自动评价
    → 满足自动发布门槛
    → AUTO_PROVISIONAL_MERGE
    → 后续增量快照持续复核
    → 每个成员格均新增足够的有效证据样本后，记一次验证通过
    → 达到验证次数和确认样本门槛
    → AUTO_CONFIRMED_MERGE
    → 后续结构证据不再成立
    → 自动拆分或收缩
```

不再需要人工确认，也不允许通过 `merge_condition_label_pairs` 直接修改正式标签。

## 2. 默认门槛

```python
min_observed_samples = 10
min_mature_samples = 30

min_auto_merge_samples = 100
min_auto_confirm_samples = 300
min_common_state_samples = 10
min_risk_samples = 30
min_metric_coverage_ratio = 0.80
min_consecutive_pass_snapshots = 3
min_new_samples_per_member_for_confirmation = 10
max_auto_region_cells = 8

max_liquid_gas_relative_difference = 0.15
max_pump_distribution_distance = 0.25
max_risk_rate_difference = 0.10
```

`min_auto_confirm_samples` 按有效合并证据计数执行。每个基础格的有效证据计数取以下三者的最小值：

```text
基础格 sample_count
液气比有效计数
净烟气 SO2 / 风险有效计数
```

因此，只有样本行数增长、但液气比或净烟气 SO2 无效，不会推动自动确认。

## 3. 防止“空增量快照”错误确认

相同区域在新快照继续通过结构判断，并不一定自动增加验证次数。

只有区域内每个成员基础格，相比上一次已经计入验证的快照，均新增至少：

```text
min_new_samples_per_member_for_confirmation
```

个有效证据样本时，验证次数才加 1。

小批次可以累积；与该区域无关的增量数据不会推动其从临时合并升级为确认合并。

快照中记录：

```text
verification_passes
verification_progress
member_sample_counts
member_evidence_counts
counted_member_evidence_counts
last_counted_snapshot_version
```

`verification_progress` 可能为：

```text
INITIAL_PASS
COUNTED_NEW_EVIDENCE
HELD_INSUFFICIENT_NEW_SAMPLES
MIGRATED_WITHOUT_COUNTED_SAMPLE_BASELINE
```

## 4. 自动评价项目

相邻基础格必须同时满足：

1. 合并功能已启用；
2. 上下左右直接相邻；
3. 都不是 `EMPTY`；
4. 两格样本均达到自动发布门槛；
5. 至少存在一个双方均达到最少样本数的共同内部状态；
6. 液气比有效覆盖率达到门槛；
7. 平均液气比相对差不超过阈值；
8. 循环泵组合分布距离不超过阈值；
9. 双方风险有效样本达到门槛；
10. 风险率差不超过阈值；
11. 区域扩张后仍是完整矩形；
12. 区域内所有相邻边均通过评价；
13. 区域最大、最小液气比相对差不超过阈值；
14. 区域成员数不超过 `max_auto_region_cells`。

风险数据缺失返回：

```text
INSUFFICIENT_RISK_EVIDENCE
```

不会被当作兼容。

## 5. mode 的实际行为

```text
disabled
    不生成或发布合并，所有基础格保持独立。

evidence_only
    每格达到 min_auto_merge_samples 后，可以发布自动临时合并。

conservative
    每格达到 min_auto_confirm_samples 后，才允许首次发布临时合并；
    后续仍需完成多次新增证据验证才能升级为确认合并。
```

`enabled=False` 与 `mode=disabled` 均会关闭自动合并。

## 6. 在线使用规则

```text
LOCAL_GRID
    当前基础格的相同 state_key 样本达到 min_observed_samples。

MERGED_REGION
    当前格属于自动合并区域。

PLANT_GLOBAL
    当前格有样本，但本地状态经验不足且没有可用合并区域。

BASELINE_ONLY
    当前格没有历史样本。
```

默认允许 `AUTO_PROVISIONAL_MERGE` 作为 `MERGED_REGION` 回退来源，但：

```text
AUTO_PROVISIONAL_MERGE
    不允许经济性探索。

AUTO_CONFIRMED_MERGE
    满足其他在线条件时，才允许经济性探索。
```

通过以下配置可关闭临时区域在线回退：

```python
"allow_provisional_region_fallback": False
```

正式标签只从 `condition_snapshot.json` 读取。`condition_merge_statistics.json` 仅是兼容统计文件。

## 7. 自动拆分和生命周期事件

每次增量训练都从当前累计基础格证据重新构造区域。

旧区域不再通过时，不会继续保留历史合并，而是自动拆分、收缩或移除。生命周期事件分为：

```text
REGION_EXPANDED
REGION_CONTRACTED
REGION_SPLIT
REGION_REMOVED
```

扩张不会误记为拆分。真正的拆分、收缩和移除同时写入：

```text
metadata.auto_merge_state.split_events
```

完整生命周期写入：

```text
metadata.auto_merge_state.region_lifecycle_events
auto_merge_report.json
```

## 8. 合并报告

`auto_merge_report.json` 包含：

- 所有相邻候选及候选 ID；
- 每项证据、实际差值及阈值；
- 允许或拒绝原因；
- 区域扩张尝试；
- 最终发布区域；
- 验证次数和新增证据计数；
- 自动确认结果；
- 拆分、收缩、扩张和移除事件；
- 汇总统计。

## 9. 输入字段诊断

初次和增量训练先标准化 CSV 表头，并验证：

```text
jzfh
yyq_SO2
liquid_gas_ratio
jyq_SO2
xstjy_PH
aptjy_PH
```

字段缺失、标准化后重名，或者整列没有任何有限数值时直接报错，不再静默生成全部为 0 的 pH 计数。

空白理论网格中的 `null` 仍然正常，表示历史数据没有覆盖该格。

## 10. 液气比规则

当前采用连续相对差：

```text
|mean_liquid_gas_A - mean_liquid_gas_B|
--------------------------------------- ≤ 15%
max(|A|, |B|)
```

区域扩张时还检查区域最大、最小液气比的相对差。

本版本没有加入硬性 `LG_BAND_STEP` 固定分带，避免 `17.9` 与 `18.1` 这类边界附近的相近值被强行拆开。

## 11. 文件替换

新增：

```text
auto_merge_manager.py
```

替换：

```text
condition_config.py
condition_merger.py
condition_schema.py
grid_definition.py
grid_range_analyzer.py
initial_condition_builder.py
incremental_condition_updater.py
online_condition_classifier.py
snapshot_io.py
```

建议先备份原目录，并从原始初次训练数据重新生成 `v001`。旧快照可兼容读取，但旧版没有完整的有效证据验证基线，升级后会安全地从当前证据重新建立确认进度。

## 12. 测试

包内提供：

```text
test_auto_merge_pipeline.py
```

从项目根目录运行：

```bash
python -m system.model.map_control.condition_model.test_auto_merge_pipeline
```

覆盖：

- 初次自动临时合并；
- 无关增量不推动确认；
- 有效证据小批次累计；
- 达到多次验证后自动确认；
- 结构差异后的自动拆分；
- 严格 JSON；
- 人工标签对直通入口保护。
