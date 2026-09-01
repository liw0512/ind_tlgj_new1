# 第一模块 V2：100 基础格 + 数据驱动 Seed Region + 稳健漂移管理

## 目标

本阶段只改第一模块的离线工况结构，不切换第二模块控制主链。

钢厂继续以 `yyq_SO2` 为唯一工况轴，`500~5000 mg/Nm3`、`100 mg/Nm3` 为固定基础格。
基础格是长期稳定的历史坐标；最终 `condition_label` 是版本化 Operating Region。

第一版 Seed Region：

| Region | yyq_SO2 | 类型 | 第二模块 local correction |
|---|---:|---|---|
| EDGE_LOW | 500~900 | EDGE / LOW SUPPORT | 否 |
| C1 | 900~1600 | CORE / HIGH | 是 |
| C2 | 1600~1900 | CORE / HIGH | 是 |
| C3 | 1900~2200 | CORE / HIGH | 是 |
| C4 | 2200~3000 | CORE / MEDIUM | 是，后续强 shrinkage |
| EDGE_HIGH | 3000~5000 | EDGE / OOD | 否 |

边界来自当前钢厂历史数据的覆盖、近似自由 pH/出口响应和数据充足度分析。
它们是 `v001` 的数据驱动 seed，不写死为在线 if/else 逻辑。

## Robust liquid/gas

旧基础统计的 `sum/count` 普通均值继续保留，避免破坏兼容接口。

新增独立 robust 分布层，按：

`base_grid + circulation-pump state`

维护固定 histogram。Histogram 用于累计历史分布，因此增量批次划分方式不会改变
P05/P50/P95 的统计语义。

默认：

- histogram: 0~100, bin width 0.25；
- P05/P50/P95；
- P05~P95 trimmed mean；
- batch >= 300 且至少覆盖 2 天后才做漂移判定；
- median shift <=4%: STABLE；
- 4%~6%: WATCH；
- 6%~10%: SUSPECTED_DRIFT；
- >10%: STRONG_SHIFT。

`WATCH/SUSPECTED_DRIFT/STRONG_SHIFT` 不立即吸收到长期 baseline，避免测点故障
被系统逐步“学成正常”。连续 3 个版本同方向偏移只升级为需要物理一致性复核，
当前阶段仍不自动修改 region 边界。

## Region 生命周期

初次训练：

`100 base grid -> seed publisher -> EDGE/C1/C2/C3/C4 -> snapshot`

增量训练：

`更新 base-grid 累计统计 -> batch robust stats -> drift report -> KEEP previous regions`

当前阶段：

- 自动 boundary change = OFF；
- merge/split 只保留为未来版本级结构评估能力；
- robust L/G 漂移只能作为证据，不能单独触发 merge/split；
- 后续加入 quasi-free process evidence；
- 再后续读取上一版第二模块 dynamic_structure_report；
- 只有跨版本证据充分后才允许 KEEP/MERGE/SPLIT 改边界。

## 分阶段切换

1. 新增 robust statistics、seed publisher 和报告，不影响旧在线链。
2. 用钢厂完整 CSV 做新 V2 initial replay。
3. 用时间切分模拟多次增量，验证 region 始终 KEEP、drift 状态符合预期。
4. 通过后替换旧 Initial/Incremental 入口中的自由 AutoMergeManager。
5. 再继续第二模块 Global Model + Condition Correction。

这样旧生产 snapshot/在线分类器在验证前保持不变。
