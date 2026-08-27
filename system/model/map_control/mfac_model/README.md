# Scheme 2 MFAC Model

本目录是项目的**正式第二模块**。第一模块 `condition_model` 保留；原第二模块 `slurry_policy_model` 的在线策略、动作推荐、训练器、激活器和运行时状态机已经从当前 Scheme2 分支物理删除。

## 1. 正式生产运行链

当前应用启动链：

```text
Application.py
-> system.gui.live_dashboard
-> DataClientMain
-> Process4MapControlMFAC.ProcessForMapConsole
-> OnlineConditionPolicyPipeline
-> condition_model                         # 第一模块
-> SlurryPolicyOnlineBridge               # 仅迁移兼容类名
-> MFACUnifiedRuntimePolicy               # 正式第二模块唯一 runtime
   -> DynamicQbaseCalculator              # 每10秒只计算一次
   -> SAFE_PRIMARY_FALLBACK
      OR
      Scheme2RuntimeCoordinator           # 已标定后唯一 target owner
-> algorithm_target_supply_flow
-> formal DCS adapter                     # 尚未接入
```

`system/model/Process4MapControl.py` 只继续承担历史数据/线程/训练/数据库公共外壳。正式应用不直接实例化它；`Process4MapControlMFAC.ProcessForMapConsole` 覆盖旧 Scheme2 sidecar 接点，因此生产链不会再执行第二套 Qbase/target 计算。

## 2. 单一 MFAC runtime

每个10秒决策周期只允许：

```text
MFACUnifiedRuntimePolicy
-> calculate Dynamic Qbase exactly once
-> choose exactly one target path
```

### 未标定：SAFE_PRIMARY_FALLBACK

```text
Q_target_algorithm = clip(Qbase + 0, 0, 70)
```

不声明 tracking、响应学习、non-zero residual 或 DCS application。

### 已显式标定：COORDINATOR_SHADOW

```text
same precomputed Qbase
-> Scheme2RuntimeCoordinator
-> continuous target
-> tracking
-> SO2 response
-> pH response
-> runtime state / audit
```

Coordinator 成为该周期唯一 target publisher。fallback 与 Coordinator 不会在同一周期同时执行。

正式 P4PC 中旧 `_run_scheme2_shadow()` 只剩字段映射：

```text
mfac_* -> scheme2_shadow_* compatibility alias
```

它不读取过程输入、不重算 Qbase、不重新发布 target。

固定审计字段：

```text
scheme2_runtime_source = PRIMARY_MFAC_RUNTIME
scheme2_duplicate_runtime_path = false
```

## 3. 正式双响应门槛

“正式 MFAC runtime”必须同时具备：

```text
SO2 response
pH response
```

底层 `Scheme2RuntimeCoordinator` 为组件测试仍可单独实例化 SO2-only 版本，但：

```text
MFACUnifiedRuntimePolicy.configure_runtime_coordinator(...)
Process4MapControlMFAC.configure_mfac_runtime(...)
```

都会拒绝缺失任意以下内容的 Coordinator：

```text
ph_response
ph_online_adaptation
ph_arbitration
```

因此单响应 Coordinator 不能成为正式第二模块 runtime。

## 4. 双响应定义与控制职责

两个响应都由同一次真实供浆变化产生：

```text
phi_so2 = ΔSO2 / ΔQ_actual    # 正常 < 0
phi_ph  = ΔpH  / ΔQ_actual    # 正常 > 0
```

两条响应都以真实：

```text
actual_flow_reached_time
```

作为因果起点，但拥有独立 delay / observation / measurement window、置信度和在线递推。

SO2 是唯一控制产生通道；pH 只仲裁 SO2 residual：

```text
SO2 residual candidate
-> PHResidualArbiter
-> PASS / SCALE / BLOCK
-> residual_mfac_final
-> MFACResidualHoldManager
```

禁止：

```text
residual_final = residual_so2 + residual_ph
```

等待实际流量到位及 SO2/pH 响应结束期间，held residual 不允许每10秒重复累加；Qbase 可以继续每周期重算。

## 5. Runtime 配置：默认明确未标定

正式配置入口：

```text
mfac_model/runtime_config.py
mfac_model/mfac_primary_config.py
```

仓库默认：

```text
enabled = false
status = DISABLED_UNCALIBRATED
learning_enabled = false
residual_control_enabled = false
dcs_write_enabled = false
```

所有现场相关 section 默认均为空：

```text
tracking = {}
so2_response = {}
so2_adaptation = {}
residual = {}
ph_response = {}
ph_adaptation = {}
ph_arbitration = {}
```

这是故意设计的 fail-closed 状态。代码不会把单元测试数值或“经验值”伪装成生产标定值。

当 `enabled=true` 时，必须显式提供完整双响应参数；缺失任意必填项：

```text
INVALID_INCOMPLETE_CALIBRATION
```

参数存在但 dataclass 校验失败：

```text
INVALID_CALIBRATION_CONFIG
```

完整且安全：

```text
CONFIGURED_SHADOW
```

当前阶段即使配置完整，builder 仍强制：

```text
LEARN = 0
Residual = 0
DCS write = off
```

## 6. Dynamic Qbase

Qbase 使用：

- `yyq_SO2`：入口 SO2；
- runtime outlet SO2 target；
- `yyq_LL`：烟气流量；
- `xstshsjy_MD`：浆液密度。

当前钢厂公式参数：

```text
omega = 0.0013 * rho - 1.3
reference pH = 6.0
Ca/S = 1.7
```

实时 pH 不回灌 Qbase，只用于 pH response learning 和 residual safety arbitration。

历史/实时实际供浆流量：

```text
只能用于 tracking / ΔQ_actual / response evidence
不得成为 Qbase fallback
不得成为 algorithm target fallback
```

## 7. Target 连续性

fallback 与 Coordinator 模式切换时会迁移：

```text
last_valid_algorithm_target
```

因此若切换后的第一个周期恰好 Qbase 输入无效，仍按：

```text
HOLD_LAST_INVALID_INPUT
```

保持上一有效 algorithm target，不会因为 runtime mode 切换丢失连续性。

## 8. 当前 DCS 边界

当前正式 runtime 始终：

```text
target_was_applied = false
dcs_applied_target_supply_flow = None
```

即使原始数据帧出现同名字段，也不会被解释为“本次算法命令已经被 DCS 执行”。

正式 DCS target-applied/readback 必须通过未来单独评审的 adapter 接入。

## 9. Canonical 命名

### 生命周期配置

正式配置文件：

```text
system/model/config/mfac_core_bridge_config.py
MFAC_CORE_BRIDGE_CONFIG
```

正式键：

```text
mfac_initial_script
mfac_incremental_script
mfac_activate_script
mfac_config
mfac_output_root
active_version_file
```

旧：

```text
system/model/config/slurry_core_bridge_config.py
SLURRY_CORE_BRIDGE_CONFIG
```

只剩兼容 wrapper，把历史 key 映射到同一 MFAC 路径；不再定义真实第二模块配置。

### active_version.json

新激活版本只发布：

```text
integrated_version
backend = MFAC
condition {...}
mfac {...}
```

不再生成新的 `slurry_policy` block。`IntegratedVersionManager` 仍能读取迁移期旧指针，但 normalize 后 canonical 字段固定为：

```text
mfac_version
mfac_snapshot_path
mfac_manifest_sha256
mfac_source_condition_version
```

历史 `policy_*` 只保留只读兼容 property。

### 在线输出

主命名空间：

```text
mfac_*
```

例如：

```text
mfac_runtime_mode
mfac_qbase_effective
mfac_residual_mfac_hold
mfac_algorithm_target_supply_flow
mfac_runtime_cycle
mfac_runtime_config_status
mfac_runtime_configured
```

`slurry_policy_*` 与 `scheme2_shadow_*` 仅是迁移 alias，来源仍是同一个 MFAC decision。

## 10. Canonical 数据库持久化

正式扩展 schema：

```text
system/model/config/mfac_database_schema.py
```

`t_model_result_*` 现在正式持久化 `mfac_*`：

```text
mfac_loaded_version
mfac_runtime_mode
mfac_qbase_*
mfac_residual_mfac_hold
mfac_algorithm_target_*
mfac_runtime_cycle
mfac_learn_enabled
mfac_residual_enabled
mfac_dcs_write_enabled
mfac_runtime_config_status
mfac_runtime_config_version
mfac_runtime_config_missing_fields
...
```

旧 `slurry_policy_*` 列暂不物理删除，以保护历史表和旧页面迁移。

`DataHandler` 查询新数据优先读取 canonical MFAC 字段；对于历史行使用：

```sql
COALESCE(mfac_field, slurry_policy_field)
```

因此添加新列后，旧行的 NULL 不会导致历史曲线消失。

## 11. 历史证据

原第二模块 `_engine` 中仍有价值的真实供浆事件工具已迁到：

```text
mfac_model/historical_episode_engine/
```

只允许：

```text
10s production-equivalent history
-> real supply-flow episode extraction
-> real ΔQ_actual
-> SO2 / pH response evidence
-> MFAC bootstrap / calibration
```

历史回放必须：

```text
replay_semantics = COUNTERFACTUAL_SHADOW
```

历史 actual flow 不能伪装成 algorithm target；未实际执行的 counterfactual target 也不能被宣称对后续 SO2/pH 产生了真实响应。

## 12. Runtime restore

恢复必须同时满足：

```text
MFAC semantics version match
condition_snapshot_version match
mfac_context_id match
```

新 runtime 持久化 SO2/pH 独立状态、held residual 和 last-valid target，不允许跨工况静默复用。

## 13. Version lifecycle

```text
condition training/update
-> condition snapshot v###
-> MFAC version artifact v###
-> activate_mfac_version.py
-> canonical active_version.json
```

产物位置：

```text
mfac_model/mfac_model_output/
  active_version.json
  snapshots/v###/
    manifest.json
    training_summary.json
  runtime/
```

## 14. 当前安全状态

无论 fallback 还是完整已标定 Shadow Coordinator，目前都固定：

```text
LEARN = 0
Residual = 0
DCS write = off
```

因此未标定的正式运行输出等价于：

```text
Q_target_algorithm = clip(Qbase + 0, 0, 70)
```

## 15. 当前完成状态

已经完成：

- 原 `slurry_policy_model` 物理删除；
- 历史 Episode 引擎迁入 MFAC；
- Dynamic Qbase；
- SO2/pH 双响应；
- SO2/pH 独立在线 phi；
- SO2 residual + pH PASS/SCALE/BLOCK；
- non-accumulating residual HOLD；
- runtime persistence；
- dual-response Coordinator；
- 单一 `MFACUnifiedRuntimePolicy`；
- 正式 P4PC 路由；
- canonical `mfac_*` 输出；
- canonical MFAC lifecycle config；
- canonical MFAC active pointer；
- canonical MFAC DB persistence；
- historical canonical-first/legacy-coalesce 查询；
- fail-closed runtime config builder；
- 正式 runtime 双响应门槛；
- fallback/Coordinator target continuity；
- 对应单元/集成回归测试已经写入仓库。

## 16. 仍未打开的生产权限/后续事项

以下不是当前代码缺口，而是需要真实现场证据后才能进入的后续阶段：

1. 用历史和 mock-DCS/现场数据完成 tracking、SO2 response、pH response 标定；
2. 将经过评审的显式参数填入 runtime config；
3. 接入 formal DCS target-applied/readback adapter；
4. Bootstrap/Profile 证据合格后单独评审 online LEARN；
5. 再评审 non-zero residual；
6. 最后评审 DCS write。

代码修改完成后统一执行 syntax / Scheme2 unit / integration tests；测试结果必须以真实执行证据为准，不能仅凭测试文件存在宣称通过。
