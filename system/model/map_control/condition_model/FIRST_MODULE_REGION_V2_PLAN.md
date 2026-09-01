# 第一模块 V2：100 基础格 + 数据驱动 Seed Region + 稳健上下文变化管理

## 目标

本阶段只改第一模块的离线工况结构，不切换第二模块控制主链。

钢厂继续以 `yyq_SO2` 为唯一工况轴，`500~5000 mg/Nm3`、`100 mg/Nm3` 为固定基础格。
基础格是长期稳定的历史坐标；最终 `condition_label` 是版本化 Operating Region。

第一版 Seed Region：

| Region | condition_label | yyq_SO2 | 类型 | 第二模块 local correction |
|---|---:|---:|---|---|
| EDGE_LOW | 10001 | 500~900 | EDGE / LOW SUPPORT | 否 |
| C1 | 10002 | 900~1600 | CORE / HIGH | 是 |
| C2 | 10003 | 1600~1900 | CORE / HIGH | 是 |
| C3 | 10004 | 1900~2200 | CORE / HIGH | 是 |
| C4 | 10005 | 2200~3000 | CORE / MEDIUM | 是，后续强 shrinkage |
| EDGE_HIGH | 10006 | 3000~5000 | EDGE / OOD | 否 |

边界来自当前钢厂历史数据的覆盖、近似自由 pH/出口响应和数据充足度分析。
它们是 `v001` 的数据驱动 seed，不写死为在线 if/else 逻辑。

## Robust liquid/gas：只解释 Operating Context

旧基础统计的 `sum/count` 普通均值继续保留，避免破坏兼容接口，但不再用于 V2 结构判断。

新增独立 robust 分布层，按：

`base_grid + circulation-pump state`

维护固定 histogram。Histogram 用于长期参考分布，因此增量批次划分方式不会改变
P05/P50/P95 的统计语义。

钢厂数据回放进一步证明：在固定循环泵拓扑下，`liquid_gas_ratio` 主要由 `yyq_LL`
反向决定。因此 L/G 分布变化必须解释为 **Operating Context Distribution Shift**，
不能单独解释为脱硫过程动力学漂移，更不能单独触发 Region merge/split。

默认：

- histogram: 0~100, bin width 0.25；
- P05/P50/P95 与 P05~P95 trimmed mean 均为 `IN_RANGE_ONLY`；
- underflow/overflow 单独作为数据质量和尾部分布证据；
- batch >= 300 且至少覆盖 2 天后才做上下文变化判定；
- median shift <=4%: `STABLE`；
- 4%~6%: `WATCH`；
- 6%~10%: `SUSPECTED_CONTEXT_SHIFT`；
- >10%: `STRONG_CONTEXT_SHIFT`。

`WATCH/SUSPECTED_CONTEXT_SHIFT/STRONG_CONTEXT_SHIFT` 不立即吸收到长期 reference，
避免运行上下文变化或测点问题被系统逐步“学成正常”。连续 3 个**有充分证据且同方向**
的版本只升级为 `confirmed_context_shift / requires_context_review`，当前阶段仍不自动修改
Region 边界，也不能等同于 `CONFIRMED_PROCESS_DRIFT`。

## Reference baseline 与 warmup

新的 `grid+pump` 组合不能因为首次出现几十个样本就建立正式 baseline。

生命周期：

`BASELINE_WARMUP -> 满足 min_baseline_samples + min_independent_days -> BASELINE_INITIALIZED`

初训时样本/独立日期不足的组合也放入 warmup；旧 V2 snapshot 在第一次增量时会自动把
不成熟 baseline 迁回 warmup，从而避免永久卡在 `INSUFFICIENT_EVIDENCE`。

## Pending context-shift 生命周期

已有 pending 上下文变化时：

- 新批次 `STABLE`：清除 pending，并允许该批吸收到 reference baseline；
- 新批次 `INSUFFICIENT_EVIDENCE`：保留 pending，不增加 supported-version count；
- 新批次完全没有该 `grid+pump`：同样保留 pending，状态记为 `PAUSED_NO_OBSERVATION`；
- 同方向且有充分证据的 shift：`consecutive_supported_versions += 1`；
- 反方向且有充分证据的 shift：重新从 1 开始。

因此“连续 3 次”现在指连续的**有效支持证据**，不是要求三个 wall-clock snapshot
每一批都必须有足够样本。

## Region 生命周期

初次训练：

`100 base grid -> seed publisher -> EDGE/C1/C2/C3/C4 -> snapshot`

增量训练：

`更新 base-grid 累计统计 -> batch robust context stats -> context-shift report -> KEEP previous regions`

当前阶段：

- 自动 boundary change = OFF；
- merge/split 只保留为未来版本级结构评估能力；
- robust L/G 只属于 operating-context evidence，`structural_decision_authority=false`；
- 后续加入 quasi-free pH / outlet SO2 process evidence；
- 再后续读取上一版第二模块 dynamic_structure_report；
- 只有 process behavior、dynamic response、独立时间支持和 shadow/time-block 验证均充分后，
  才允许提出 KEEP/MERGE_CANDIDATE/SPLIT_CANDIDATE；
- 最终结构变更仍走离线版本化发布，不由单个增量批次自动修改。

## 分阶段切换

1. 新增 robust statistics、seed publisher 和 context-shift 报告，不影响旧在线链。
2. 用钢厂完整 CSV 做新 V2 initial replay。
3. 用独立未来验证集模拟多次增量，验证 Region 始终 KEEP、context-shift 生命周期符合预期。
4. 通过后替换旧 Initial/Incremental 入口中的自由 AutoMergeManager。
5. 再继续第二模块 Global Model + Condition Correction。

这样旧生产 snapshot/在线分类器在验证前保持不变。
