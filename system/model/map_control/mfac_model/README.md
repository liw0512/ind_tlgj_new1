# Scheme 2 MFAC Model

本目录现在是项目的**正式第二模块**。第一模块 `condition_model` 保留，原第二模块 `slurry_policy_model` 的在线策略、Q-learning/动作推荐、训练器、激活器和运行时状态机已经退出当前分支并删除源码目录。

当前主链定义为：

```text
10s production frame
-> condition_model                      # 第一模块
-> MFAC                                 # 正式第二模块
   -> Dynamic Qbase
   -> residual_mfac_hold
   -> continuous target
-> DCS adapter (future, currently off)
```

为避免前端/数据库在一次迁移中同时断裂，`SlurryPolicyOnlineBridge` 这个**旧类名**和部分 `slurry_policy_*` 输出字段暂时保留为兼容壳；它们的实际 backend 已经是 `MFAC`，不会再 import 或执行旧 `slurry_policy_model`。

## 第二模块替换状态

已完成：

```text
old slurry_policy_model online controller    -> removed
old slurry_policy_model trainer              -> removed
old slurry_policy_model activation scripts   -> removed
old source directory                         -> removed

condition_model output
-> MFACPrimaryPolicy
-> DynamicQbaseCalculator
-> ContinuousTargetPublisher
-> algorithm_target_supply_flow
```

P4PC 原有的若干 legacy 配置键（例如 `slurry_policy_initial_script`、`slurry_policy_output_root`）仍暂时存在，但已经全部重定向到 `mfac_model` 的 version builder / activation / artifact root；这些键只是迁移兼容名称，不代表旧算法仍存在。

旧第二模块中唯一继续保留的能力是**历史真实供浆动作的事件检测/响应画像工具**。该部分已经迁移到：

```text
mfac_model/historical_episode_engine/
```

只允许用于 MFAC offline bootstrap/calibration evidence，不具备在线控制权限。

## 当前正式第二模块的安全阶段

当前正式第二模块已替换成 MFAC，但控制权限还没有打开：

```text
LEARN = 0
Residual = 0
DCS write = off
```

因此当前主输出等价于：

```text
Q_target_algorithm = clip(Qbase + 0, 0, 70)
```

这不是永久控制律，而是替换旧第二模块后的安全 Shadow 起点。后续只有在双响应 Bootstrap/Profile 和 DCS readback 证据合格后，才允许逐步启用 `residual_mfac_hold` 和在线学习。

## 完整 MFAC 组件链

```text
condition_model output
-> MFACContextResolver
-> mfac_context_id

current inlet SO2 + runtime SO2 target + gas flow + slurry density
-> DynamicQbaseCalculator
-> Qbase_raw / Qbase_effective

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
```

其中完整双响应 Coordinator 当前仍以 Shadow/验证能力存在；正式主链先使用 `MFACPrimaryPolicy` 的安全零 residual 输出。后续激活时应把两者收敛为一个 MFAC runtime path，不能重新引入旧第二模块。

## 双响应定义

“**双响应 MFAC**”固定指：

```text
SO2 response
pH response
```

不是“执行延迟 + 过程延迟”。执行延迟仍单独定义：

```text
target_change_time -> actual_flow_reached_time
```

两个响应通道都以真实 `actual_flow_reached_time` 为因果起点，并允许不同的 delay / observation / measurement window。

学习方向：

```text
phi_so2 = delta_SO2 / delta_Q_actual    # 正常 < 0
phi_ph  = delta_pH  / delta_Q_actual    # 正常 > 0
```

两条通道可以不同步结束：

```text
pH COMPLETED != whole action completed
SO2 COMPLETED != force pH completed
```

## SO2 主控、pH 仲裁

pH 不是第二个并联供浆控制器。禁止：

```text
residual_final = residual_so2 + residual_ph
```

正确语义：

```text
SO2 residual candidate
-> pH arbitration
-> PASS / SCALE / BLOCK
-> residual_final
```

即：

```text
residual_final = pH_arbitration(residual_so2)
```

`PHResidualArbiter` 不生成 additive `delta_Q_ph`。

## Qbase 与实时 pH

Dynamic Qbase 使用：

- `yyq_SO2`；
- runtime SO2 target；
- `yyq_LL`；
- `xstshsjy_MD`。

当前钢厂：

```text
omega = 0.0013 * rho - 1.3
reference pH = 6.0
Ca/S = 1.7
```

实时 pH：

```text
不得回灌 Qbase
只进入 pH response learning + safety / residual arbitration
```

历史实际供浆量也不得成为 Qbase 或 algorithm target fallback。

## Historical evidence

历史回放必须保持：

```text
replay_semantics = COUNTERFACTUAL_SHADOW
```

历史真实流量只用于真实物理证据：

```text
delta_q_actual = actual_flow_after - actual_flow_before
```

历史事件入口现在归 MFAC 所有：

```text
10s production-equivalent history
-> mfac_model.historical_episode_engine
-> Scheme1EpisodeToMFACAdapter / canonical ActionResponseEvent
-> bootstrap / calibration
```

不得再依赖已删除的 `slurry_policy_model` 包。

## Runtime restore

恢复状态必须同时满足：

```text
MFAC semantics version match
condition_snapshot_version match
mfac_context_id match
```

旧 V1 runtime 只有 `phi_live/confidence_live` 时可按 SO2 通道兼容恢复；新 runtime 同时持久化 SO2/pH 状态。

## Version lifecycle

第二模块版本产物已经迁到：

```text
mfac_model/mfac_model_output/
  active_version.json
  snapshots/v###/
    manifest.json
    training_summary.json
```

初次/增量生命周期：

```text
condition training/update
-> condition snapshot v###
-> MFAC version artifact v###
-> activate_mfac_version.py
-> active_version.json backend=MFAC
```

当前 `active_version.json` 仍可临时包含一个 `slurry_policy` compatibility block，原因只是旧 `IntegratedVersionManager` 的字段兼容；该 block 指向 MFAC manifest，不指向旧算法产物。

## 当前仍未完成

- GitHub 环境没有 CI status，新增/迁移代码尚不能宣称已经完整执行通过；
- standalone `condition_config` / `IntegratedVersionManager` 中还有若干 `slurry_policy_*` 兼容命名待逐步改成 canonical `mfac_*`；
- `Process4MapControl.py` 目前同时保留 MFAC primary bridge 与独立 Scheme2 Shadow coordinator 接点，后续应收敛成单一路径；
- 正式 DCS target-applied/readback adapter 尚未接入；
- SO2/pH response calibration profile 尚未完成；
- online LEARN、非零 residual、DCS write 均未启用。

## 下一阶段

1. 增加“旧第二模块已删除且 MFAC backend 生效”的替换回归测试；
2. 清理 standalone condition 配置和版本管理中的 legacy 命名；
3. 在可执行环境运行全部 Scheme2 syntax/unit/integration tests；
4. 收敛 P4PC 的 MFAC primary 与 Shadow coordinator 为单一正式 MFAC runtime path；
5. 完成 SO2/pH calibration profile 和 formal DCS readback；
6. 证据合格后再逐阶段评审 LEARN、Residual、DCS write。
