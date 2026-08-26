# Scheme 2 MFAC Model

本目录是方案2独立 MFAC sidecar，不替代 `condition_model`，也不直接修改方案1现有 `slurry_policy_model` 在线行为。

## 当前实现边界

当前分支已经具备以下独立组件：

```text
condition_model output
-> MFACContextResolver
-> mfac_context_id

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
- non-accumulating residual HOLD semantics。

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

## 当前仍未接入生产主链

这些组件目前仍保持 sidecar/shadow 边界：

- 尚未接入 `Process4MapControl.py` 主循环；
- 尚未建立正式 DCS write adapter；
- 尚未启用生产在线 `LEARN`；
- 尚未启用非零生产 residual；
- runtime state 持久化/恢复尚未完成；
- tracking deadband、reach tolerance、sustain、timeout 和 response-window 参数仍需用正式 DCS/历史证据标定。

因此当前生产安全语义仍应保持：

```text
LEARN = 0
Residual = 0
DCS write = off
```

## 下一阶段

1. 对新增组件执行完整 syntax/unit test；
2. 增加 runtime state 持久化与恢复；
3. 建立 Scheme2 runtime coordinator，以 Shadow 方式串起 target -> tracking -> response -> event -> phi；
4. 接入 `Process4MapControl.py`，但保持 DCS no-write / LEARN off；
5. 用历史回放验证 continuous target 与 `COUNTERFACTUAL_SHADOW`；
6. 用正式 DCS target-applied readback + actual flow feedback 标定 tracking 参数；
7. 有合格 Bootstrap/Profile 后再逐步打开 online learn 与 residual。
