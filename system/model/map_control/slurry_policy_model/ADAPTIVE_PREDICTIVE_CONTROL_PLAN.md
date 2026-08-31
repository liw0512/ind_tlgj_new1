# 第二模块 V2：自适应预测供浆控制改造方案

> 分支：`codex/supply-flow-adaptive-dmc-v1`
>
> 基线：`codex/supply-flow-policy-v2` @ `ed2a161974938d22de927361816402617d53b227`
>
> 状态：方案冻结后分阶段实现；任何阶段均不得直接开启 DCS 写入。

## 1. 改造目标

现有第二模块以历史 `STEP / PULSE / BOOST_STEP` 供浆动作作为主要学习对象，形成 `supply_flow_prototypes.pkl` 并在线选择目标供浆流量。V2 不再把“历史人工动作”默认视为最佳策略，而把历史供浆动作视为自然发生的系统辨识激励，用于学习装置动态响应。

V2 目标：

1. 学习原烟气 SO2 扰动对净烟气 SO2 与 pH 的动态传播；
2. 学习实际供浆变化对净烟气 SO2 与 pH 的动态响应；
3. 显式保存响应延迟、响应核、置信度和可辨识性；
4. 在线每 10 秒滚动预测未来 5~10 分钟 SO2 / pH 轨迹；
5. 根据用户设定的净烟气 SO2 目标、pH 储备区间、供浆成本和动作平滑约束，生成平缓的 `TARGET_SUPPLY_FLOW`；
6. 利用实时预测误差做快速 bias 修正；
7. 利用高可信可辨识片段做受限在线参数自适应；
8. 周期性使用新片段做离线增量重辨识并发布新版本；
9. FAST_CHANGE 继续负责外扰识别，Safety Envelope 继续作为最终 veto；
10. 保持现有执行接口兼容，V2 初期只运行 Shadow，不改变现有 DCS 写入状态。

## 2. 明确不做的事情

第一版 V2 不做：

- 不把 `yyq_LL` 作为核心快速扰动轴；该测点相对变化小而短时波动大，第一版仅预留可选慢变量接口；
- 不训练“状态 -> 人工供浆量”的黑盒模仿模型作为主控制器；
- 不直接上 Offline RL；
- 不使用未经可辨识性过滤的动作片段估计供浆因果效果；
- 不要求 pH 精确跟踪一个死目标；pH 采用储备区间/软约束；
- 不允许在线自适应无边界修改模型；
- 不在 V2 Shadow 验证完成前写 DCS。

## 3. 控制问题定义

### 3.1 操纵量

唯一主操纵量：

`u = Q_actual / TARGET_SUPPLY_FLOW`

模型学习与反馈评价使用真实 `Q_actual`，而不是命令值。

### 3.2 主要输出

- 一级控制目标：`jyq_SO2` 相对用户目标值的风险；
- 二级状态目标：`pH` 位于合理储备区间；
- 三级经济目标：减少不必要供浆；
- 四级动作目标：限制 `ΔQ`，抑制脉冲式供浆。

由于只有一个主操纵量而有两个输出，不能要求 `jyq_SO2` 和 pH 同时精确跟踪两个独立设定值。净烟气 SO2 为主要目标，pH 为储备状态与软约束。

### 3.3 主要测量扰动

第一版核心外扰：

- `yyq_SO2` 当前值；
- `yyq_SO2` 因果变化率；
- `yyq_SO2` 因果加速度/FAST severity（若可靠）；
- FAST_CHANGE 的 direction / severity / state。

`yyq_LL` 第一版不进入核心快速动态模型；后续只有在离线消融验证能稳定提升跨时间预测时才加入，并且必须使用强平滑后的慢变量语义。

## 4. V2 动态模型

第一版采用可解释、低自由度的 FIR/ARX/LPV-FIR 思路，不直接使用高容量神经网络。

需要辨识四条核心动态路径：

1. `G_yyq_jyq(τ)`：原烟气 SO2 扰动 -> 净烟气 SO2；
2. `G_yyq_ph(τ)`：原烟气 SO2 扰动 -> pH 消耗；
3. `G_q_jyq(τ)`：实际供浆变化 -> 净烟气 SO2；
4. `G_q_ph(τ)`：实际供浆变化 -> pH。

每条响应模型至少保存：

- sample interval；
- horizon；
- pure delay；
- response kernel / step response；
- gain；
- sample count；
- identifiability confidence；
- validation metrics；
- support domain；
- model semantic version。

第一版优先使用“全局响应核 + 少量上下文缩放”，避免把历史数据切成大量稀疏工况格子。可选上下文包括 pH、yyq_SO2 level、当前 Q、循环泵组合。

## 5. 历史数据如何使用

### 5.1 保留现有动作事件提取

现有 `STEP / PULSE / BOOST_STEP` 检测继续保留，但角色改变：

- 旧语义：历史动作原型，在线复用；
- V2 语义：系统辨识激励，提取输入变化和未来响应。

### 5.2 可辨识性优先于“动作好坏”

新增 `IdentifiabilityScorer`，每个 episode 至少输出：

- `IDENTIFIABLE`；
- `WEAKLY_IDENTIFIABLE`；
- `UNIDENTIFIABLE`。

主要判断：

- 时间是否连续；
- Q_actual 是否有足够激励；
- 流量计是否有效；
- pH / yyq_SO2 / jyq_SO2 是否有效；
- 循环泵状态是否发生强切换；
- FAST / 扰动是否可观测；
- 响应窗口是否完整；
- 同窗口是否存在无法解释的强结构变化。

坏动作不自动丢弃。例如“供浆不足”“供浆过量”仍能给出响应与安全边界信息；真正不进入因果响应辨识的是无法归因的片段。

### 5.3 闭环混杂处理

历史人工供浆由 yyq_SO2、jyq_SO2、pH 等变量共同触发，因此不能直接用 `future_y ~ Q` 学因果效果。

第一版采用两步策略：

1. 先建立自由响应/扰动模型，预测在当前状态和既有供浆历史下未来输出；
2. 对高可辨识动作片段学习实际输出相对自由响应预测的残差，从而估计新增供浆动作的额外效果。

必要时增加行为残差化：先估计历史状态下人工通常采取的供浆水平，再用 `Q_actual - Q_behavior_hat` 作为额外激励强度，降低 treatment-selection bias。

## 6. 在线未来预测

在线预测不是一次性预测后盲信 10 分钟，而是 Receding Horizon：

- 每 10 秒重新读取实时状态；
- 每 10 秒重新估计当前 bias / hidden disturbance；
- 每 10 秒重新预测未来 5~10 分钟；
- 每轮只执行第一个 10 秒动作；
- 下一轮使用新实测数据重新计算。

未来输出拆为：

`future_output = free_response + past_action_remaining_response + candidate_future_action_response + bias_correction`

其中：

- `free_response`：若不新增供浆动作，当前 yyq / jyq / pH 状态自然如何发展；
- `past_action_remaining_response`：过去几分钟已经发生但尚未兑现完的供浆作用；
- `candidate_future_action_response`：候选未来供浆轨迹产生的预测作用；
- `bias_correction`：未测扰动、慢漂移和当前模型偏差的快速校正。

## 7. DMC/MPC 控制目标

第一版控制器底层使用 DMC 风格动态矩阵/预测矩阵，允许后续替换为状态空间 MPC，但不改变上层接口。

建议目标函数：

`J = J_SO2 + J_pH_zone + J_Q + J_delta_Q + J_terminal_risk`

含义：

- `J_SO2`：净烟气 SO2 超过用户目标的预测风险；
- `J_pH_zone`：pH 离开储备区间的软约束；
- `J_Q`：额外供浆成本；
- `J_delta_Q`：供浆变化率/脉冲惩罚；
- `J_terminal_risk`：预测窗末端仍恶化的风险。

硬约束至少包括：

- `Q_min <= Q_target <= Q_max`；
- `|ΔQ_10s| <= max_delta_q_per_step`；
- 传感器有效；
- 流量反馈有效；
- 循环泵/设备可用；
- Safety Envelope 最终限幅；
- OOD / model confidence 不足时禁止激进动作。

## 8. pH 动态储备

第一版不使用固定 pH 死目标，而使用：

- `pH_reserve_low`；
- `pH_reserve_high`；
- 可选动态 `pH_reserve_low(t)`。

动态储备下限只允许基于可靠预测逐步引入。基本原则：

- yyq_SO2 稳定且 jyq_SO2 余量大时，不为维持高 pH 无谓供浆；
- yyq_SO2 快速上涨、模型预测未来 pH 储备被耗尽并造成 jyq_SO2 越目标时，提前平缓补浆；
- pH 高于储备上限时抑制继续加浆，避免过冲。

## 9. 三层自适应

### 9.1 快速 bias / disturbance correction

时间尺度：10 秒到数分钟。

预测值与实测值出现偏差时，不立即重训模型，而更新输出 bias / hidden disturbance estimate。该修正进入下一轮未来预测。

### 9.2 在线受限参数自适应

时间尺度：数分钟到数小时/天。

仅在高可辨识 episode 中，使用 RLS / Kalman parameter estimator 更新少数慢参数：

- `K_q_jyq`；
- `K_q_ph`；
- `K_yyq_jyq`；
- `K_yyq_ph`；
- 可选 delay / time-constant scale。

在线参数必须：

- 有最小激励门槛；
- 有 confidence gate；
- 有更新速率限制；
- 有相对离线模型上下界；
- 预测误差异常或传感器异常时冻结。

### 9.3 离线增量重辨识

新高质量 episode 持续积累，周期性与旧有效数据合并，重新辨识完整响应核，历史 replay + Shadow 验证后发布 `v002 / v003 ...`。

在线自适应是 runtime delta；离线增量训练生成正式模型版本，两者必须分离。

## 10. 模型健康与退化策略

新增 `ModelHealthMonitor`，至少监测：

- one-step / horizon prediction error；
- bias magnitude；
- parameter drift；
- confidence；
- OOD distance；
- sensor health；
- identifiability rate。

状态建议：

- `HEALTHY`；
- `DEGRADED`；
- `DRIFT`；
- `INVALID`。

退化策略：

- `HEALTHY`：允许正常 Shadow/控制；
- `DEGRADED`：提高平滑惩罚、缩小动作范围；
- `DRIFT`：冻结在线参数学习，使用保守动作并触发重辨识；
- `INVALID`：HOLD 或回退到已验证旧策略，绝不让坏模型继续自适应。

## 11. 与现有模块边界

### 第一模块 condition model

职责不变。只确保第二模块可取得必要状态和有效性标记。

### FAST_CHANGE

职责不变：识别 yyq_SO2 的方向、速度、severity、FAST/RECOVERY/REGULAR 状态。V2 将其作为 measured disturbance context，而不是 `FAST_Lx -> 固定动作` 映射。

### 第二模块 slurry_policy_model

核心重构区域。新增：

```text
slurry_policy_model/
  adaptive_predictive/
    contracts.py
    config.py
    response_model.py
    predictor.py
    state_estimator.py
    dmc_controller.py
    online_adaptation.py
    model_health.py
    shadow_advisor.py

  _engine/
    response_identification.py
    identifiability.py
```

旧 prototype pipeline 暂时保留，作为安全先验、历史支持域和对比基线。

### Runtime / Coordinator

只新增保存：

- 最近 Q / yyq / jyq / pH 历史；
- prediction bias；
- adaptive parameter delta；
- prediction trajectory；
- model confidence / health。

### Safety Envelope

继续作为最终 veto。新增对 predictive target 的 rate limit、absolute limit、confidence/OOD gate。

### DCS 执行

不改变 `TARGET_SUPPLY_FLOW` 主接口；V2 第一阶段 `SHADOW_ONLY = True`，不改变当前 DRY_RUN / DCS write-off 安全状态。

## 12. 版本产物

V2 snapshot 建议：

```text
policy snapshot v###/
  global/supply_flow_prototypes.pkl        # legacy prior / support guard
  predictive/
    response_model.json
    response_kernels.npz
    identification_summary.json
    validation_metrics.json
    support_domain.json
  adaptive/
    online_bounds.json
    initial_covariance.json
  valid_identification_episodes.csv
  weak_identification_episodes.csv
  invalid_episodes.csv
  manifest.json
```

`manifest.json` 必须记录：

- `policy_semantics_version = ADAPTIVE_PREDICTIVE_SUPPLY_FLOW_V1`；
- base dataset fingerprint；
- signal mapping；
- sample interval；
- horizon；
- response model schema；
- FAST semantics version；
- safety config version。

当响应语义、FAST 主扰动语义、流量拓扑或关键传感器映射变化时，禁止直接继承旧在线自适应参数，必须重新离线辨识。

## 13. 实施阶段

### Phase 0：分支与文档

- 建分支；
- 冻结边界；
- 保持旧链路行为不变。

验收：现有测试理论上不受影响。

### Phase 1：预测控制公共契约与 Shadow 骨架

新增：

- response model contract；
- predictor contract；
- DMC config；
- state/bias estimator；
- model health；
- shadow advisor。

这一阶段允许使用手工/fixture response kernel，只验证数据流和数学方向，不替换旧 Advisor。

验收：

- 对正向 Q 增量，pH 预测方向符合配置 kernel；
- 对 yyq_SO2 正向扰动，jyq_SO2 风险方向符合 kernel；
- 同样预测结果下，更高 `delta_q_penalty` 输出更平滑；
- Shadow 不改变现有推荐。

### Phase 2：离线可辨识性与 FIR/ARX 辨识

新增：

- episode identifiability scorer；
- response kernel estimator；
- delay estimator；
- rolling/time-block validation；
- support-domain estimator。

验收：必须输出训练/验证时间段分离的指标，不允许随机切分近邻时间点冒充泛化性能。

### Phase 3：历史 replay

使用历史数据逐点模拟在线：

- 只使用时刻 t 及之前数据；
- 预测未来 5~10min；
- 记录预测误差、目标越线预警提前量；
- 与历史 pulse/step 行为比较 Q 变化率、pH 波动和 jyq_SO2 风险。

验收门槛不只看 RMSE，还看：

- SO2 越目标召回率；
- false alarm rate；
- lead time；
- pH zone violation；
- simulated ΔQ smoothness；
- OOD coverage。

### Phase 4：Shadow DMC

实时每 10 秒：

- 接收 FAST + realtime state；
- 预测未来轨迹；
- 计算 shadow target；
- 不执行；
- 保存实际未来响应做评价。

验收：连续运行、无异常内存增长、无阻塞主链路、prediction/model health 可追溯。

### Phase 5：在线 bias 修正

只启用输出 bias / disturbance correction，不更新动态 gain。

验收：短期预测误差稳定改善，异常测点不会造成 bias 无限累积。

### Phase 6：在线受限 RLS/Kalman 自适应

只对高置信动作 episode 更新少数 gain scale，默认关闭，先 Shadow 记录“若更新会怎样”。

验收：参数受 bounds 约束；无激励不更新；异常/DRIFT 时冻结；长期 replay 不发散。

### Phase 7：受控切换

在 Safety Envelope 与 DCS 写许可之外新增 feature flag：

- `predictive_mode = OFF | SHADOW | ADVISORY | ACTIVE`。

初始必须为 `SHADOW`。

切换 ACTIVE 前必须完成现场独立评审。

## 14. 测试计划

新增单元测试：

- convolution / response kernel；
- pure delay；
- past-action remaining response；
- bias correction；
- DMC smoothness penalty；
- Q/rate limit；
- pH zone penalty；
- SO2 target penalty；
- RLS bounds / no-excitation freeze；
- model health transitions。

新增集成测试：

- synthetic plant response；
- FAST_RISE + adequate pH reserve；
- FAST_RISE + low pH reserve；
- delayed Q response；
- sensor invalid；
- model drift；
- legacy advisor + shadow advisor coexistence。

## 15. 第一版配置建议

第一版配置必须保守：

- sample interval = 10s；
- prediction horizon 先支持 5~10min 可配置；
- control horizon 显著短于 prediction horizon；
- `yyq_LL` disabled；
- adaptive gain update disabled；
- bias correction enabled in Shadow；
- target output = `TARGET_SUPPLY_FLOW`；
- DCS write = off；
- predictive mode = `SHADOW`。

所有具体 delay、gain、pH zone、Q rate limit 都不得直接从探索分析数字硬编码为现场最终值，必须由离线辨识/厂级约束生成并在 snapshot 中版本化。

## 16. 关键设计原则

1. 历史人工动作是实验，不是真理；
2. 学动作之后的装置响应，而不是复刻动作；
3. 真实 `Q_actual` 才是辨识输入；
4. pH 是吸收能力储备状态，不是唯一最终目标；
5. 净烟气 SO2 用户目标是控制优化的主目标；
6. FAST_CHANGE 是外扰前馈，不直接决定固定供浆量；
7. 预测不要求绝对完美，但动态方向、延迟和增益必须可验证；
8. 每 10 秒滚动重预测，只执行第一步；
9. 在线 bias 修正、在线参数自适应、离线增量重训是三个不同层次；
10. 自适应必须可冻结、可限幅、可审计、可回滚；
11. 不可靠输入宁可不用；
12. Safety Envelope 永远拥有最终否决权。
