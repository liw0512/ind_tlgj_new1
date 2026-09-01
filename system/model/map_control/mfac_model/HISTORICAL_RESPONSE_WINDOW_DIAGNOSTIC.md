# Scheme2 MFAC 历史双响应窗口诊断 V2

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

## V2 关键修正：时延证据与局部增益证据分开

首轮诊断错误地要求 timing cohort 的 baseline flow 和 final flow 都大于 5 m3/h。
实机 `event_audit.csv` 表明这批历史强供浆动作大量属于：

```text
STARTUP_STEP : 近 0 -> 正常供浆流量
SHUTDOWN_STEP: 正常供浆流量 -> 近 0
```

这些动作不适合直接成为 MFAC 局部增益，但仍然包含很有价值的物理响应时延信息。

因此 V2 拆成两套资格：

```text
TIMING_ONLY
  可使用清晰 STARTUP / SHUTDOWN / LOCAL_STEP
  用于：pH / SO2 时延和 measurement window review
  禁止：将启停动作 apparent phi 发布成 runtime prior

LOCAL_GAIN
  仅 operating-flow -> operating-flow 的局部阶跃
  用于：未来 phi_so2 / phi_ph marginal-gain training
  仍需经过正式 causal/context/blocked-validation gates
```

默认 timing cohort 还要求：

```text
complete event = true
|delta Q| >= 2 m3/h
无后续动作覆盖最大 response horizon
active duration <= 20 min
transition_count <= 3
至少动作前或动作后有一侧处于 operating flow >= 5 m3/h
```

这组限制只属于时延探索，不改变正式 MFAC 训练门槛。

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

历史人工操作具有明显内生性：经常是 pH / SO2 已经恶化后，操作员才改变供浆，因此简单 `after - before` 可能把原有过程趋势错误归因给供浆动作。

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

注意：STARTUP / SHUTDOWN 的 `raw_phi` / `corrected_phi` 只是 descriptive apparent ratio，用于方向和时延比较，不能当作局部 MFAC 增益。

## yyq_LL

本轮明确：

```text
yyq_LL hard condition axis = no
yyq_LL nuisance feature = no
yyq_LL eligibility gate = no
yyq_LL diagnostic-only = yes
```

是否未来加入 MFAC，必须后续通过实机数据质量 QA 和 blocked-date A/B validation 证明。

## 本地执行环境

统一使用：

```text
D:\anaconda\envs\py3921\python.exe
```

训练数据：

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
D:\anaconda\envs\py3921\python.exe -m unittest discover `
  -s tests `
  -p "test_scheme2_historical_response_window_diagnostic.py" `
  -v
```

测试覆盖：

- pH / SO2 窗口独立；
- 旧 3~13 min 仅保留为 comparison；
- median 响应统计；
- pretrend correction；
- STARTUP/SHUTDOWN 可进入 timing，但不能进入 local gain；
- operating -> operating 才能成为 local-gain diagnostic candidate；
- 低流量噪声、长时间多阶段动作不进入 timing；
- `yyq_LL` 不属于正式 MFAC 输入要求。

### 3. 运行实机历史扫描

```powershell
D:\anaconda\envs\py3921\python.exe `
  -m system.model.map_control.mfac_model.historical_response_window_diagnostic `
  --csv files\new_data_train_10s.csv `
  --out files\scheme2_response_window_scan
```

## 输出

生成：

```text
files\scheme2_response_window_scan\event_audit.csv
files\scheme2_response_window_scan\window_event_details.csv
files\scheme2_response_window_scan\window_scan_summary.csv
files\scheme2_response_window_scan\window_scan_by_evidence_class.csv
files\scheme2_response_window_scan\response_window_scan_report.md
```

优先回传：

1. `response_window_scan_report.md`
2. `window_scan_summary.csv`
3. `window_scan_by_evidence_class.csv`
4. `event_audit.csv`

若需要逐事件排查，再补 `window_event_details.csv`。

## 结果判读顺序

优先：

1. `date_median_direction_rate`
2. `corrected_direction_rate`
3. `apparent_corrected_phi_relative_mad`
4. `independent_days`
5. `event_count`
6. raw 与 corrected 的差异
7. STARTUP_STEP 与 SHUTDOWN_STEP 是否给出一致的窗口趋势

首先比较：

```text
PH candidate 5~8 min       vs PH legacy 3~13 min
SO2 candidate 10~12 min   vs SO2 legacy 3~13 min
```

如果 STARTUP 与 SHUTDOWN 对同一窗口给出明显相反结论，不能直接固定时延，需要继续查 action anchor / confounder / pretrend 语义。

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
