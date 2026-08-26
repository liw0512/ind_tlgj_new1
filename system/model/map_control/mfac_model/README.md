# Scheme 2 MFAC Model

本目录是方案2独立 MFAC sidecar，不替代 `condition_model`，也不直接修改方案1现有 `slurry_policy_model` 在线行为。

## 当前实现边界

当前分支已经具备以下独立组件：

```text
condition_model output
-> MFACContextResolver
-> mfac_context_id

current inlet SO2 + runtime SO2 target + gas flow + slurry density
-> DynamicQbaseCalculator
-> Qbase_raw / Qbase_effective audit result

Scheme1 historical episode
-> Scheme1EpisodeToMFACAdapter
-> StrictMFACEligibilityGate
-> ActionResponseEvent
-> bootstrap_trainer
-> phi_prior / phi_live0 / delay evidence

Qbase + residual_mfac_hold
-> ContinuousTargetPublisher
-> algorithm_target_supply_flow

algorithm target
-> DCS applied target/readback
-> actual slurry-flow feedback
-> SupplyFlowTrackingMonitor
-> actual_flow_reached_time

actual_flow_reached_time
├─> ProcessResponseMonitor (SO2 channel)
│   -> ProcessResponseEvent
│   -> OnlineResponseToMFACAdapter
│   -> ActionResponseEvent
│   -> MFACOnlineAdapter
│   -> phi_so2_live (< 0)
│
└─> PHResponseMonitor (pH channel)
    -> PHResponseEvent
    -> PHOnlineAdapter
    -> phi_ph_live (> 0)

SO2 target / outlet SO2 + phi_so2_live
-> MFACResidualController
-> SO2 residual candidate
-> PHResidualArbiter
-> PASS / SCALE / BLOCK
-> residual_mfac_final
-> MFACResidualHoldManager
-> residual_mfac_hold

MFACRuntimeState
  - phi_so2_live / confidence_so2_live
  - phi_ph_live / confidence_ph_live
  - independent SO2/pH event counters and timestamps
+ residual_mfac_hold
+ last valid algorithm target
-> Scheme2RuntimeStore
-> atomic JSON persistence / guarded restore

all stages above
-> Scheme2RuntimeCoordinator
-> one auditable Shadow cycle result
```

## 双响应定义

方案2当前“**双响应 MFAC**”固定指两个被控响应通道：

```text
SO2 response
pH response
```

不是把“执行延迟 + 过程延迟”称为双响应。执行延迟仍单独定义为：

```text
target_change_time -> actual_flow_reached_time
```

两条过程响应都从真实 `actual_flow_reached_time` 开始归因，并允许各自使用不同的 delay / observation / measurement window。

学习方向固定为：

```text
phi_so2 = delta_SO2 / delta_Q_actual    # 正常物理方向 < 0
phi_ph  = delta_pH  / delta_Q_actual    # 正常物理方向 > 0
```

两条通道可以不同步完成：

```text
pH COMPLETED != whole action completed
SO2 COMPLETED != force pH completed
```

Coordinator 对同一个 tracking event 分别记录 SO2/pH 终态；启用双响应时，SO2 必须完成且 pH 也必须进入终态后，动作才可进入下一次 residual 更新资格。每个 tracking event 只消费一次，避免每 10 秒重复动作。

## SO2 主控、pH 仲裁

pH **不是第二个并联供浆控制器**。最终控制量不允许采用：

```text
residual_final = residual_so2 + residual_ph    # 禁止
```

正确语义为：

```text
SO2 residual candidate
-> pH arbitration
-> PASS / SCALE / BLOCK
-> residual_final
```

因此：

```text
residual_final = pH_arbitration(residual_so2)
```

`PHResidualArbiter` 使用当前 pH、运行/安全区间以及（有证据时）`phi_ph_live` 预测 SO2 residual 对 pH 的影响，只能：

- `PASS`：保持 SO2 residual；
- `SCALE`：缩小 SO2 residual；
- `BLOCK`：把本次 residual 限制为 0。

它不会生成 additive `delta_Q_ph`。

## Qbase 与实时 pH 的职责边界

Dynamic Qbase 每周期使用：

- `yyq_SO2`；
- runtime SO2 target；
- `yyq_LL`；
- `xstshsjy_MD`。

当前钢厂 Ca/S 固定使用参考 pH 6.0 对应的 `1.7`。实时 pH：

```text
不得回灌 Qbase
只进入 pH response learning + safety / residual arbitration
```

密度—含固量关系已于 2026-08-26 确认为：

```text
omega = 0.0013 * rho - 1.3
```

且 `omega` 以质量分数小数形式代入。

## 已完成

- MFAC schema / runtime-state contract；
- condition -> MFAC context resolver；
- MFAC compatibility-gate contract；
- 复用方案1供浆 Episode 的历史事件适配；
- strict learning eligibility；
- offline bootstrap evidence / recursive replay；
- continuous algorithm-target publication；
- historical `COUNTERFACTUAL_SHADOW` 语义；
- DCS applied target -> actual flow tracking；
- `actual_flow_reached_time` 生成；
- SO2 process-response monitoring；
- pH independent response monitoring；
- online SO2 response -> canonical `ActionResponseEvent`；
- SO2 negative-direction event-driven online phi update；
- pH positive-direction event-driven online phi update；
- SO2 residual candidate calculation；
- pH PASS/SCALE/BLOCK residual arbitration；
- non-accumulating residual HOLD semantics；
- SO2/pH 双状态 runtime persistence / restore；
- `Scheme2RuntimeCoordinator` 双响应 Shadow 编排；
- `Process4MapControl.py` fail-closed Shadow 接点；
- Dynamic Qbase 在线审计计算。

## 关键控制语义

算法目标：

```text
Q_target_algorithm = clip(Qbase + residual_mfac_hold, 0, 70)
```

历史实际供浆量不允许作为算法目标 fallback。历史回放必须保持：

```text
replay_semantics = COUNTERFACTUAL_SHADOW
```

在线因果事件只有在以下条件满足后才允许建立：

```text
target_was_applied = true
AND
dcs_applied_target_supply_flow valid
AND
actual flow reached
```

两条响应学习都必须使用真实：

```text
delta_q_actual = actual_flow_after - actual_flow_before
```

不得使用算法目标差代替真实 `delta_q_actual`。

等待供浆执行/等待 SO2 或 pH 响应期间，`residual_mfac_hold` 保持不变，不允许每 10 秒重复累加；`Qbase` 可继续按每个控制周期重算。

Runtime restore 必须同时满足：

```text
MFAC semantics version match
condition_snapshot_version match
mfac_context_id match
```

旧 V1 runtime 只有 `phi_live/confidence_live` 时仍可恢复，等价于 SO2 通道；pH 状态为空并保持未激活。新 runtime 同时持久化 SO2 和 pH 状态，不跨工况静默复用。

## 当前生产接入边界

`Process4MapControl.py` 已具备显式 `configure_scheme2_shadow(...)` 接点，
但默认不构造未标定 Coordinator。当前接点仍只接受以下安全配置：

- `LEARN = 0`；
- `Residual = 0`；
- `DCS write = off`。

即使输入数据声称 target 已应用，当前主循环接点也固定传入：

```text
target_was_applied = false
```

直到正式 DCS application/readback adapter 单独评审接入。

以下生产能力仍未启用：

- 尚未建立正式 DCS write adapter；
- 尚未启用生产在线 `LEARN`；
- 尚未启用非零生产 residual；
- SO2/pH 双响应窗口和 tracking 参数仍需用正式 DCS/历史证据继续标定；
- pH `phi` bootstrap/profile 仍需合格事件证据。

因此当前生产安全语义仍保持：

```text
LEARN = 0
Residual = 0
DCS write = off
```

## 下一阶段

1. 在可执行环境运行全部 Scheme2 syntax/unit/integration tests；
2. 用 mock-DCS 回放继续验证 SO2/pH 先后完成、单通道 censor、superseded、timeout、sample-gap；
3. 用正式 DCS target-applied readback + actual flow feedback 标定 tracking 参数；
4. 为 SO2/pH 分别形成可审计 response calibration profile；
5. 独立评审 DCS application/readback adapter；
6. 只有 Bootstrap/Profile 证据合格后，再分阶段评审 online learn 与非零 residual。
