# 第一模块：condition_model（方案1 Canonical）

方案2的第一模块不再维护独立实现。第一模块唯一设计基准固定为：

```text
repository : liw0512/ind_tlgj_new
branch     : codex/adaptive-feedback-slurry-v1
commit     : 0d99e18262dc2b1bf9fb03464de5eb4eb4166d44
```

方案2仓库只允许在“第一模块 -> 第二模块”的集成边界上适配 MFAC；第一模块自身的网格、统计、自动合并、ConditionSnapshot、增量更新和在线稳定判定不得单独演化。

## 1. 第一模块边界

第一模块负责：

```text
历史/实时过程数据
    ↓
按 plant_config 的 1/2 个任意工况轴建立固定网格
    ↓
基础格统计与成熟度判断
    ↓
相邻基础格证据式自动合并
    ↓
ConditionSnapshot 版本化
    ↓
在线瞬时 Condition 分类
    ↓
MAJORITY 窗口稳定
    ↓
condition_label / base_condition_id / grid_id / policy_region_id / state_key ...
```

到这里第一模块结束。

方案2从第一模块输出之后才开始分叉：

```text
Condition enriched row
    ↓
MFACUnifiedRuntimePolicy
    ↓
Dynamic Qbase
    +
Dual-response MFAC
```

因此 `integrated_version_manager.py`、`online_condition_policy_bridge.py` 虽然仍放在本目录中，但它们属于**集成边界**，不能从方案1机械覆盖回旧 `slurry_policy_model` 后端。

## 2. 已按方案1完整锁定的纯第一模块文件

下面文件必须与方案1基准 commit 的 Git blob 完全一致：

```text
__init__.py
auto_merge_manager.py
condition_merger.py
condition_schema.py
grid_definition.py
grid_range_analyzer.py
incremental_condition_updater.py
initial_condition_builder.py
online_condition_classifier.py
snapshot_io.py
```

`tests/test_scheme2_first_module_scheme1_parity.py` 会直接计算这些文件的 Git blob SHA。任何方案2单方面修改都会使回归测试失败。

以下文件是 Scheme2 适配边界，不要求字节一致：

```text
condition_config.py
integrated_online_example.py
integrated_version_manager.py
online_condition_policy_bridge.py
README.md
```

其中 `condition_config.py` 的**第一模块算法参数**必须与方案1一致；仅路径和第二模块 active pointer 允许适配 MFAC。

`condition_merge_statistics.json` 是训练产生的累计统计数据，不属于可迁移源码，不能从另一个工程直接复制覆盖。

## 3. 厂级配置仍只改 plant_config.py

方案1第一模块的一个关键设计是：现场事实不散落在 `condition_model` 中，而是统一从：

```text
system/model/config/plant_config.py
```

派生。

第一模块读取：

- 1 个或 2 个任意工况轴；
- 工况轴字段、范围、步长；
- 当前启用塔的 pH 字段；
- 净烟气 SO2 安全上限；
- 其他用于统计/风险判断的标准字段。

方案2继续使用自己的现场 `PLANT_CONFIG`，因为这里还承载 Dynamic Qbase 与 MFAC 的物理合同。迁移第一模块不等于复制方案1现场参数。

例如单轴：

```python
"condition_axes": [
    {
        "column": "yyq_SO2",
        "min": 500.0,
        "max": 5000.0,
        "step": 100.0,
    },
]
```

内部仍使用 `P*-S*` 网格 ID；在单轴模式中第二槽只是兼容结构，不代表必须存在第二个现场变量。

## 4. 第一模块算法参数完全采用方案1

当前 canonical 值：

```text
min_observed_samples                   = 10
min_mature_samples                     = 30
min_auto_merge_samples                 = 100
min_auto_confirm_samples               = 300
min_common_state_samples               = 10
min_risk_samples                       = 30
min_metric_coverage_ratio              = 0.80
min_consecutive_pass_snapshots         = 3
min_new_samples_per_member_for_confirmation = 10
max_auto_region_cells                  = 8
max_liquid_gas_relative_difference     = 0.15
max_pump_distribution_distance         = 0.25
max_risk_rate_difference               = 0.10
```

在线稳定：

```text
stability_mode                  = MAJORITY
stability_window_size           = 6
majority_tie_policy             = KEEP_LAST_STABLE
allow_provisional_region_fallback = True
```

以后若第一模块参数需要优化，应先在统一第一模块设计中确认，再同步两套方案；不能只在方案2把样本阈值按 10s 周期自行乘倍数。

## 5. 自动合并语义

基础格满足证据条件后可形成策略区域：

```text
独立基础格
    ↓
AUTO_PROVISIONAL_MERGE
    ↓  后续增量持续获得证据
AUTO_CONFIRMED_MERGE
    ↓  后续结构证据不再成立
自动收缩 / 拆分 / 移除
```

核心判断包括：

- 基础格相邻；
- 样本数量达到阶段门槛；
- 液气比统计覆盖和相对差满足要求；
- 循环泵状态分布距离满足要求；
- SO2 风险统计有足够证据且差异可接受；
- 合并区域保持矩形；
- 区域大小不超过配置上限。

pH 在第一模块中用于解释性统计，不作为固定网格坐标。单塔/双塔均由中央拓扑配置决定；缺少未启用塔 pH 不应导致第一模块失败。

## 6. Initial 训练

方案2仍按统一训练生命周期调用第一模块：

```text
raw training data
→ model_csv/Initial_train.csv
→ initial_condition_builder.py
→ Initial_train_after_condition.csv
→ snapshots/v001/condition_snapshot.json
→ MFAC Initial
→ condition + MFAC 同版本验证/激活
```

第一模块独立运行：

```powershell
D:\anaconda\envs\py3921\python.exe `
  system\model\map_control\condition_model\initial_condition_builder.py `
  --input <input.csv> `
  --output <output.csv> `
  --snapshot-output <condition_snapshot.json> `
  --snapshot-version v001
```

## 7. Incremental 训练

```text
new data
→ Update_train.csv
→ active condition snapshot vN
→ incremental_condition_updater.py
→ Incremental_train_after_condition.csv
→ condition snapshot vN+1
→ MFAC incremental vN+1
→ 原子验证后统一激活
```

第一模块增量逻辑与方案1一致；方案2的 7 天 Initial / 3 天 Incremental 生命周期属于整个 Scheme2 离线调度，不改写第一模块内部算法。

## 8. 在线 Condition 判定

正式入口仍是：

```text
system/model/map_control/condition_model/online_condition_classifier.py
```

在线顺序：

```text
raw realtime row
→ instant grid / region classification
→ MAJORITY(6) stability
→ append all condition fields
→ Scheme2 integrated-version consistency check
→ MFAC backend
```

这里的“MFAC backend”是方案2适配层，不属于第一模块算法。兼容类名 `SlurryPolicyOnlineBridge` 和部分 `slurry_policy_*` alias 暂时保留，仅用于避免旧 P4PC/数据库接口一次性破坏；正式控制所有权属于 MFAC。

## 9. 方案2必须保留的集成差异

以下内容不能因为第一模块迁移而退回方案1第二模块：

```text
MFAC_ACTIVE_VERSION_FILE
MFAC_CORE_BRIDGE_CONFIG
IntegratedVersionManager 的 MFAC artifact 校验
MFACUnifiedRuntimePolicy
DynamicQbase
Scheme2RuntimeCoordinator
SO2 / pH 双响应
shadow safety
```

安全状态继续保持：

```text
LEARN = 0
Residual = 0
DCS write = off
```

## 10. 回归验证

第一模块来源一致性：

```powershell
D:\anaconda\envs\py3921\python.exe -m unittest discover `
  -s tests `
  -p "test_scheme2_first_module_scheme1_parity.py" `
  -v
```

完整 Scheme2：

```powershell
D:\anaconda\envs\py3921\python.exe -m unittest discover `
  -s tests `
  -p "test_scheme2_*.py" `
  -v
```

迁移完成的判定标准不是“目录看起来相似”，而是：

1. 纯第一模块源码 blob 与方案1基准完全一致；
2. 第一模块算法参数与方案1完全一致；
3. ConditionSnapshot / Initial / Incremental / Online 接口合同保持一致；
4. Scheme2 只在第二模块边界使用 MFAC 适配；
5. 完整 Scheme2 回归仍通过。
