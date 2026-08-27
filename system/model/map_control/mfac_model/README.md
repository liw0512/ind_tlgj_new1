# Scheme 2 MFAC Model

本目录现在是项目的**正式第二模块**。第一模块 `condition_model` 保留；原第二模块 `slurry_policy_model` 的在线策略、动作推荐、训练器、激活器和运行时状态机已经从当前分支删除。

## 正式生产运行链

当前应用启动链已经收敛为：

```text
Application.py
-> system.gui.live_dashboard
-> DataClientMain
-> Process4MapControlMFAC.ProcessForMapConsole
-> OnlineConditionPolicyPipeline
-> condition_model                         # 第一模块
-> SlurryPolicyOnlineBridge               # 仅兼容壳
-> MFACUnifiedRuntimePolicy               # 正式第二模块唯一入口
   -> DynamicQbaseCalculator              # 每10秒只计算一次
   -> SAFE_PRIMARY_FALLBACK
      OR
      Scheme2RuntimeCoordinator           # 配置后成为唯一 target owner
-> algorithm_target_supply_flow
-> DCS adapter (future, currently off)
```

`system/model/Process4MapControl.py` 仍保留为数据、训练、数据库、线程等公共运行外壳，其中历史 Scheme2 helper 方法尚未物理删除；但正式应用不再直接实例化该类。`Process4MapControlMFAC.ProcessForMapConsole` 继承公共外壳，并覆盖旧 Shadow 接点，因此生产链不会再执行第二套 Qbase/target 计算。

## 单一路径语义

以前存在的重复路径是：

```text
primary bridge -> Qbase -> target
P4PC shadow    -> Qbase -> coordinator -> target
```

现在改为互斥的单一路径：

```text
MFACUnifiedRuntimePolicy
-> calculate Qbase exactly once
-> if Coordinator is absent:
      SAFE_PRIMARY_FALLBACK
      target = clip(Qbase + 0, 0, 70)
   else:
      COORDINATOR_SHADOW
      pass the same precomputed Qbase to Scheme2RuntimeCoordinator
      Coordinator publishes the only target for this cycle
```

两条分支不会在同一个周期同时执行。

原 `Process4MapControl._run_scheme2_shadow()` 在正式 `Process4MapControlMFAC` 子类中已经被覆盖为**字段兼容映射**：只把已经算好的 `mfac_*` 结果映射成临时 `scheme2_shadow_*` 字段，不读取过程输入、不重新计算 Qbase、不重新发布 target。

因此正式运行时：

```text
scheme2_duplicate_runtime_path = false
scheme2_runtime_source = PRIMARY_MFAC_RUNTIME
```

## 输出字段合同

正式第二模块主命名空间固定为：

```text
mfac_*
```

例如：

```text
mfac_runtime_mode
mfac_qbase_raw
mfac_qbase_effective
mfac_residual_mfac_hold
mfac_algorithm_target_supply_flow
mfac_runtime_cycle
```

为避免前端和数据库一次迁移全部断裂，以下仍是临时兼容字段：

```text
slurry_policy_*
scheme2_shadow_*
```

它们都来自同一个 MFAC decision，不代表旧算法或第二套 runtime 仍存在。

旧 `condition_config.py` 仍可能显式传入：

```text
output_prefix = slurry_policy_
```

Bridge 会将其解释为 legacy alias 请求，并强制主合同保持 `mfac_*`；因此旧配置不能再覆盖 canonical MFAC namespace。

## 当前安全阶段

MFAC 已经替代原第二模块，但生产控制权限仍未打开：

```text
LEARN = 0
Residual = 0
DCS write = off
```

当前没有 Coordinator 时正式输出为：

```text
Q_target_algorithm = clip(Qbase + 0, 0, 70)
```

安装已标定 Coordinator 后，当前生产接入仍只接受：

```text
learning_enabled = false
residual_control_enabled = false
dcs_write_enabled = false
```

并且主链固定：

```text
target_was_applied = false
dcs_applied_target_supply_flow = None
```

即使输入数据中出现同名字段，也不会被当前生产接入解释成“算法命令已经被 DCS 执行”。正式 target-applied/readback 必须通过后续单独评审的 DCS adapter 接入。

## Dynamic Qbase

Qbase 当前使用：

- `yyq_SO2`：入口 SO2；
- runtime outlet SO2 target；
- `yyq_LL`：烟气流量；
- `xstshsjy_MD`：浆液密度。

当前钢厂固定：

```text
omega = 0.0013 * rho - 1.3
reference pH = 6.0
Ca/S = 1.7
```

实时 pH 不回灌 Qbase，只用于 pH response learning 和 residual safety arbitration。

历史/实时实际供浆流量也不得成为 Qbase 或 algorithm target fallback。

## 双响应 MFAC

双响应固定指：

```text
SO2 response
pH response
```

不是“执行延迟 + 过程延迟”。执行延迟单独定义为：

```text
target_change_time -> actual_flow_reached_time
```

两个过程响应都从真实 `actual_flow_reached_time` 开始归因，并允许独立 delay / observation / measurement window。

学习方向：

```text
phi_so2 = delta_SO2 / delta_Q_actual    # 正常 < 0
phi_ph  = delta_pH  / delta_Q_actual    # 正常 > 0
```

SO2 是唯一控制产生通道；pH 只仲裁 SO2 residual：

```text
SO2 residual candidate
-> PHResidualArbiter
-> PASS / SCALE / BLOCK
-> residual_mfac_final
```

禁止：

```text
residual_final = residual_so2 + residual_ph
```

## Coordinator 完整链

```text
precomputed Dynamic Qbase
-> ContinuousTargetPublisher
-> algorithm target
-> DCS applied target/readback
-> actual slurry-flow feedback
-> SupplyFlowTrackingMonitor
-> actual_flow_reached_time

actual_flow_reached_time
├─> ProcessResponseMonitor (SO2)
│   -> canonical ActionResponseEvent
│   -> MFACOnlineAdapter
│   -> phi_so2_live
│
└─> PHResponseMonitor
    -> PHOnlineAdapter
    -> phi_ph_live

SO2 residual candidate
-> pH arbitration
-> MFACResidualHoldManager
-> residual_mfac_hold
```

等待真实供浆执行和 SO2/pH 响应期间，held residual 不允许每10秒重复累加；Qbase 可以继续每周期重算。

## 历史证据

原第二模块 `_engine` 中仍有价值的历史真实供浆事件工具已经迁入：

```text
mfac_model/historical_episode_engine/
```

用途仅限：

```text
10s production-equivalent history
-> real supply-flow episode extraction
-> real delta_Q_actual
-> SO2 / pH response evidence
-> MFAC bootstrap / calibration
```

历史回放必须保持：

```text
replay_semantics = COUNTERFACTUAL_SHADOW
```

历史真实流量不能伪装成算法 target，也不能把历史后续 SO2 响应归因给未实际执行的 counterfactual target。

## Runtime restore

状态恢复必须同时满足：

```text
MFAC semantics version match
condition_snapshot_version match
mfac_context_id match
```

新 runtime 持久化 SO2/pH 独立状态、held residual 和 last valid algorithm target，不允许跨工况静默复用。

## Version lifecycle

第二模块版本产物位于 MFAC 路径：

```text
mfac_model/mfac_model_output/
  active_version.json
  snapshots/v###/
    manifest.json
    training_summary.json
```

生命周期：

```text
condition training/update
-> condition snapshot v###
-> MFAC version artifact v###
-> activate_mfac_version.py
-> active_version.json backend=MFAC
```

P4PC 中部分 `slurry_policy_*` 配置键暂时仍作为兼容键存在，但其实际路径已经指向 MFAC builder / activation / artifact root。

## 当前完成状态

已经完成：

- 原 `slurry_policy_model` 源码目录物理删除；
- 历史 Episode 引擎迁入 MFAC；
- Dynamic Qbase；
- SO2/pH 双响应组件；
- online phi updater；
- SO2 residual + pH arbitration；
- residual HOLD；
- runtime persistence；
- `Scheme2RuntimeCoordinator`；
- `MFACUnifiedRuntimePolicy` 单一正式第二模块入口；
- Bridge 降级为兼容壳；
- Coordinator 跨 integrated hot reload 重新绑定；
- 正式 `DataClientMain` 启动链切到 `Process4MapControlMFAC`；
- 旧 Shadow hook 在正式运行类中变成 mapping-only；
- canonical `mfac_*` 前缀在旧配置下仍被强制保留；
- 相关替换、路由、单路径回归测试已经写入仓库。

## 尚未完成 / 不能宣称完成

- 当前 GitHub 环境仍没有可用 CI status，因此不能宣称新增测试已经实际执行通过；
- `condition_config` / `IntegratedVersionManager` 仍保留部分 `slurry_policy_*` 兼容命名，后续可逐步重命名；
- 正式 DCS target-applied/readback adapter 尚未接入；
- SO2/pH response calibration profile 尚未完成；
- online LEARN 尚未启用；
- 非零 residual 尚未启用；
- DCS write 尚未启用。

## 下一阶段

1. 在可执行环境运行全部 Scheme2 syntax/unit/integration tests；
2. 清理剩余 legacy `slurry_policy_*` 配置/状态命名，但保持数据库和前端兼容迁移；
3. 用历史与 mock-DCS 数据标定 tracking、SO2 response、pH response 参数；
4. 接入并单独评审 formal DCS target-applied/readback adapter；
5. Bootstrap/Profile 证据合格后，按阶段评审 online LEARN；
6. 再评审非零 residual；
7. 最后才评审 DCS write。
