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
-> ProcessResponseMonitor
-> ProcessResponseEvent
-> OnlineResponseToMFACAdapter
-> ActionResponseEvent
-> MFACOnlineAdapter
-> updated phi_live

SO2 target / outlet SO2 + phi_live
-> MFACResidualController
-> MFACResidualHoldManager
-> residual_mfac_hold

MFACRuntimeState + residual_mfac_hold + last valid algorithm target
-> Scheme2RuntimeStore
-> atomic JSON persistence / guarded restore

all stages above
-> Scheme2RuntimeCoordinator
-> one auditable Shadow cycle result
```

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
- online response -> canonical `ActionResponseEvent`；
- event-driven online `phi` update component；
- MFAC residual candidate calculation；
- non-accumulating residual HOLD semantics；
- runtime state / residual / last-valid-target 持久化与版本保护恢复。
- `Scheme2RuntimeCoordinator` 全链 Shadow 编排；
- `Process4MapControl.py` fail-closed Shadow 接点；
- Python 3.9 运行环境兼容性修复。
- 按工匠公式在线动态计算 Qbase，并记录公式输入、版本和失效原因；
- 当前钢厂 Ca/S 使用参考 pH 6.0 对应的 1.7，实时 pH 只进入安全监督。

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

MFAC 学习始终使用：

```text
delta_q_actual = actual_flow_after - actual_flow_before
phi_event = delta_so2 / delta_q_actual
```

不使用算法目标差代替真实 `delta_q_actual`。

等待供浆执行/等待 SO2 响应期间，`residual_mfac_hold` 保持不变，不允许每 10 秒重复累加；`Qbase` 可继续按每个控制周期重算。

Runtime restore 必须同时满足：

```text
MFAC semantics version match
condition_snapshot_version match
mfac_context_id match
```

否则拒绝恢复旧 `phi`/residual，不跨工况静默复用。

## 当前生产接入边界

`Process4MapControl.py` 已具备显式 `configure_scheme2_shadow(...)` 接点，
但默认不构造未标定 Coordinator。该接点只接受以下安全配置：

- `LEARN = 0`；
- `Residual = 0`；
- `DCS write = off`。

主循环每周期使用当前 `yyq_SO2`、在线 SO2 target、`yyq_LL` 和
`xstshsjy_MD` 动态计算 Qbase；不会用 `xstshsjy_LL`、Scheme1
`current_flow`、`xst_base_flow` 或 `target_final_flow` 作为算法目标 fallback。
密度—含固量关系的截距符号和数值尺度仍待现场标定表/人工算例最终确认，
因此当前计算结果只允许 Shadow 审计，不得作为开启闭环的依据。
即使输入数据声称 target 已应用，当前主循环接点也固定传入
`target_was_applied=false`，直到正式 DCS application/readback adapter 单独评审接入。

以下生产能力仍未启用：

- 尚未建立正式 DCS write adapter；
- 尚未启用生产在线 `LEARN`；
- 尚未启用非零生产 residual；
- tracking deadband、reach tolerance、sustain、timeout 和 response-window 参数仍需用正式 DCS/历史证据标定。

因此当前生产安全语义仍应保持：

```text
LEARN = 0
Residual = 0
DCS write = off
```

## 下一阶段

1. 用正式 DCS target-applied readback + actual flow feedback 标定 tracking 参数；
2. 配置并注入 Shadow Coordinator，持续观察目标、tracking 和 response 事件；
3. 独立评审 DCS application/readback adapter；
4. 有合格 Bootstrap/Profile 和回放证据后，再分阶段评审 online learn 与 residual。
