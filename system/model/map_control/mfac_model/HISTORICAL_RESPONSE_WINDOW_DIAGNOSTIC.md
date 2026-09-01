# Scheme2 MFAC 历史双响应窗口诊断

## 目的

本轮只验证历史供浆动作之后，pH 与净烟气 SO2 应分别使用什么响应窗口，以及动作前趋势修正是否能提高物理方向与跨日期稳定性。

本轮不修改正式控制权限：

```text
LEARN = 0
Residual = 0
DCS write = off
historical prior activation = off
```

同时不降低现有离线训练与 blocked validation 门槛。

## 当前 review candidate

不是 CALIBRATED 参数，仅作为本轮实机 A/B 测试候选：

```text
pH:
  baseline = 动作开始前 5 min
  measurement = 历史 flow-reached proxy 后 5~8 min
  aggregation = MEDIAN

SO2:
  baseline = 动作开始前 5 min
  measurement = 历史 flow-reached proxy 后 10~12 min
  aggregation = MEDIAN
```

旧历史共享窗口 `3~13 min` 同时保留在扫描中，仅用于比较。

当前历史事件没有在线运行时那种真实 `actual_flow_reached_time`，因此诊断模块使用现有供浆事件检测器的 `event.end_time` 作为“稳定新供浆平台已达到”的历史 proxy。

## 动作前趋势修正

历史人工操作具有明显内生性：经常是 pH / SO2 已经恶化后，操作员才改变供浆，因此简单：

```text
after - before
```

可能把原有过程趋势错误归因给供浆动作。

诊断同时输出：

```text
baseline_median
response_median
pretrend_per_min
counterfactual_response_median
raw_delta
corrected_delta
raw_phi
corrected_phi
```

其中 corrected response 是将动作前 baseline 趋势外推到响应窗口，形成透明的“无动作继续原趋势”参考，再计算实际响应偏差。

这仍然只是历史诊断 counterfactual，不等于最终因果模型。

## yyq_LL

本轮明确：

```text
yyq_LL hard condition axis = no
yyq_LL nuisance feature = no
yyq_LL eligibility gate = no
yyq_LL diagnostic-only = yes
```

是否未来加入 MFAC，必须后续通过实机数据质量 QA 和 blocked-date A/B validation 证明。

## 本地执行

假设训练数据位于：

```text
F:\tlgj\files\new_data_train_10s.csv
```

### 1. 拉取最新分支

```powershell
cd F:\tlgj
git checkout codex/scheme2-mfac-v1
git pull origin codex/scheme2-mfac-v1
```

### 2. 先运行新增语义测试

```powershell
python -m pytest -q tests\test_scheme2_historical_response_window_diagnostic.py
```

测试覆盖：

- pH / SO2 窗口独立；
- 旧 3~13 min 仅保留为 comparison；
- pH 物理方向 `phi_ph > 0`；
- SO2 物理方向 `phi_so2 < 0`；
- 窗口使用 median，可抵抗单点尖峰；
- pretrend correction 能识别历史操作内生性；
- `yyq_LL` 不属于正式 MFAC 诊断输入要求。

### 3. 运行实机历史扫描

从仓库根目录执行：

```powershell
python -m system.model.map_control.mfac_model.historical_response_window_diagnostic `
  --csv files\new_data_train_10s.csv `
  --out files\scheme2_response_window_scan
```

默认参数：

```text
baseline = 5 min
minimum |delta Q| for diagnostic cohort = 2 m3/h
minimum baseline/final operating flow = 5 m3/h
maximum response horizon = 13 min
```

这些只属于诊断 cohort，不会修改正式 MFAC gate。

## 输出

生成：

```text
files\scheme2_response_window_scan\event_audit.csv
files\scheme2_response_window_scan\window_event_details.csv
files\scheme2_response_window_scan\window_scan_summary.csv
files\scheme2_response_window_scan\response_window_scan_report.md
```

优先回传：

1. `response_window_scan_report.md`
2. `window_scan_summary.csv`
3. `event_audit.csv`

若需要逐事件排查，再补：

4. `window_event_details.csv`

## 结果判读顺序

优先：

1. `date_median_direction_rate`
2. `corrected_direction_rate`
3. `corrected_phi_relative_mad`
4. `independent_days`
5. `event_count`
6. raw 与 corrected 的差异

首先比较：

```text
PH candidate 5~8 min  vs PH legacy 3~13 min
SO2 candidate 10~12 min vs SO2 legacy 3~13 min
```

## 下一步正式代码修改条件

只有实机扫描确认双通道窗口与 corrected response 有稳定改善，才进入：

```text
offline_training_config.py
supply_flow_effect_profiler.py
historical_model_based_gain_adapter.py
```

正式迁移时按相同 trainer 依次做：

```text
V0 = legacy shared window + raw delta
V1 = dual independent windows + raw delta
V2 = dual independent windows + corrected delta
```

用相同 blocked-date validation 比较，避免把“窗口改善”和“模型复杂度变化”混在一起。
