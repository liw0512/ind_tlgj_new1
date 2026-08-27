# Scheme 2 MFAC Model

本目录是项目的**正式第二模块**。第一模块 `condition_model` 保留；原第二模块 `slurry_policy_model` 的在线策略、动作推荐、训练器、激活器和运行时状态机已经从当前 Scheme2 分支物理删除。

## 1. 正式生产运行链

```text
Application.py
-> DataClientMain
-> Process4MapControlMFAC.ProcessForMapConsole
-> condition_model
-> SlurryPolicyOnlineBridge              # 仅迁移兼容类名
-> MFACUnifiedRuntimePolicy              # 正式第二模块唯一 runtime
   -> DynamicQbaseCalculator             # 每10秒只算一次
   -> SAFE_PRIMARY_FALLBACK
      OR
      Scheme2RuntimeCoordinator          # 已标定后唯一 target owner
-> algorithm_target_supply_flow
-> formal DCS adapter                    # 尚未接入
```

旧 `Process4MapControl.py` 继续作为数据、线程、训练和数据库公共外壳；正式应用由 `Process4MapControlMFAC` 覆盖旧 Scheme2 sidecar 接点，因此不会再执行第二套 Qbase/target 计算。

## 2. 单一路径与安全状态

每个10秒决策周期只允许一次 Dynamic Qbase 计算以及一条 target 发布路径：

```text
Q_target_algorithm = clip(
    Qbase + residual_mfac_hold,
    plant_min_supply_flow,
    plant_max_supply_flow
)
```

未标定时：

```text
SAFE_PRIMARY_FALLBACK
residual_mfac_hold = 0
```

已显式标定时：

```text
same precomputed Qbase
-> Scheme2RuntimeCoordinator
-> one target publisher
```

当前生产权限始终固定：

```text
LEARN = 0
Residual = 0
DCS write = off
```

并且当前正式接入始终：

```text
target_was_applied = false
dcs_applied_target_supply_flow = None
```

实际供浆流量只能用于 tracking / `delta_Q_actual` / response evidence，不能成为 Qbase 或 algorithm-target fallback。

## 3. 参数单一事实源

为避免“两个配置文件都能改同一个物理事实”造成静默覆盖，正式 MFAC 按以下所有权执行。

### 3.1 `plant_config.py`：厂级物理事实唯一来源

`PLANT_CONFIG` 独占以下参数：

```text
scheme2.target_supply_flow.minimum
scheme2.target_supply_flow.maximum
scheme2.target_supply_flow.feedback_column
scheme2.target_supply_flow.unit

towers[].supply_flows[].column
towers[].ph_column
towers[].ph_safe_range
towers[].ph_operating_range
towers[].ph_guard_band

scheme2.qbase.*                 # Qbase厂级物理/标定参数
```

`system/model/config/mfac_plant_contract.py` 只负责读取和校验这些值，**不定义第二套数值**。

正式 runtime 会从该 contract 派生：

```text
供浆硬上下限
实际供浆反馈字段
主塔 tower_id
pH字段
pH安全范围
pH运行范围
pH guard band
```

如果 `scheme2.target_supply_flow.feedback_column` 与塔内显式 `supply_flows[].column` 不一致，正式 MFAC 启动失败，不允许单塔场景静默兜底。

### 3.2 `standard_fields.py`：标准过程字段唯一来源

SO2目标字段固定为：

```text
TARGET_SO2_COLUMN = outlet_so2_target
```

Dynamic Qbase 不允许再拥有另一套 target 字段映射。`plant_config.scheme2.qbase.target_so2_column` 仅作为历史兼容检查存在；若其值与标准字段不同，Qbase 构造直接失败。

### 3.3 `mfac_paths.py`：MFAC 文件路径唯一来源

以下路径只在 `system/model/config/mfac_paths.py` 定义：

```text
PROJECT_ROOT
CONDITION_ROOT
MODEL_CSV_ROOT
MFAC_ROOT
MFAC_OUTPUT_ROOT
MFAC_SNAPSHOTS_DIR
MFAC_ACTIVE_VERSION_FILE
MFAC_RUNTIME_DIR
```

`mfac_primary_config.py`、`runtime_config.py`、`mfac_core_bridge_config.py` 都只能引用这组 Path 常量，不再各自拼接同一目录。因此：

```text
版本builder写入路径
activate_mfac_version.py激活路径
P4PC在线读取active_version路径
runtime持久化路径
```

都来自同一文件路径合同。

旧 standalone 配置若仍指向：

```text
files/slurry_policy_model_output/active_version.json
```

`IntegratedVersionManager` 会明确重定向到 canonical `MFAC_ACTIVE_VERSION_FILE`；其他显式自定义路径不会被覆盖。

### 3.4 `mfac_primary_config.py`：artifact 运行身份

该文件负责正式 MFAC artifact 配置，并引用 `mfac_paths.py`。正式模式只定义一次：

```text
MFAC_PRIMARY_MODE = MFAC_PRIMARY_SHADOW
```

manifest builder 与 activate pointer 都引用同一常量，不能分别再写一套 mode 字符串。

### 3.5 `runtime_config.py`：MFAC标定参数唯一来源

runtime config 只拥有真正属于算法标定的参数，例如：

```text
tracking timing/tolerance
SO2 response delay/window
pH response delay/window
SO2 adaptation eta/mu/phi bounds
pH adaptation eta/mu/phi bounds
SO2 residual controller parameters
pH arbitration min_confidence
eligibility thresholds
```

它**不能覆盖**厂级物理事实。以下配置若出现在正式 runtime config 中会被拒绝：

```text
continuous_target.hard_min_supply_flow
continuous_target.hard_max_supply_flow

ph_arbitration.safe_min
ph_arbitration.safe_max
ph_arbitration.operating_min
ph_arbitration.operating_max
ph_arbitration.guard_band
```

即使绕过 builder 手工构造 `Scheme2RuntimeCoordinator`，`MFACUnifiedRuntimePolicy.configure_runtime_coordinator()` 仍会再次比对 plant contract，数值漂移同样被拒绝。

## 4. 哪些“重复参数”是故意保留的

以下名字看起来相似，但职责不同，**不能合并**：

```text
SO2 response delay/window
pH response delay/window
```

两种响应具有独立物理延迟和观察窗口。

```text
SO2 adaptation eta/mu/phi bounds
pH adaptation eta/mu/phi bounds
```

两条响应分别学习 `phi_so2` 与 `phi_ph`，方向也不同。

```text
P4PC data-validation thresholds
MFAC response/learning thresholds
```

前者判断实时数据/校验状态，后者判断因果响应证据，不是同一参数。

```text
plant outlet-SO2 hard safety range
normal SO2 control target range
```

前者是硬安全边界，后者是正常控制目标允许范围，也不能混为一谈。

历史 `historical_episode_engine` 的离线响应窗口与在线 Coordinator 窗口也保持独立：历史窗口用于离线证据发现/标定，在线窗口用于实时因果归因。历史引擎的 `plant` / `training` 都由调用方传入，它本身不再定义第二套在线厂级事实。

## 5. 重复安全检查是有意的

以下检查虽然出现在 builder、runtime、P4PC、manifest/active pointer 多层，但属于**防御性安全栅栏**，不是多个可配置事实源：

```text
LEARN = 0
Residual = 0
DCS write = off
```

任何一层发现不安全状态都应拒绝，而不是依赖后定义值覆盖前定义值。

## 6. 双响应 MFAC

正式 MFAC 必须同时具备 SO2 与 pH 两个响应通道：

```text
phi_so2 = delta_SO2 / delta_Q_actual    # 正常 < 0
phi_ph  = delta_pH  / delta_Q_actual    # 正常 > 0
```

两条响应都从真实：

```text
actual_flow_reached_time
```

开始归因，但拥有独立 delay / observation / measurement window、置信度和递推状态。

SO2 是唯一控制产生通道：

```text
SO2 residual candidate
-> PHResidualArbiter
-> PASS / SCALE / BLOCK
-> residual_mfac_final
-> residual HOLD
```

禁止：

```text
residual_final = residual_so2 + residual_ph
```

实时 pH 不进入 Qbase。

## 7. Dynamic Qbase

Qbase 使用：

```text
yyq_SO2
outlet_so2_target
plant-configured gas-flow field
plant-configured slurry-density field
```

当前钢厂确认：

```text
omega = 0.0013 * rho - 1.3
reference pH = 6.0
Ca/S = 1.7
```

实时 pH 仅用于响应学习和 residual safety arbitration。

## 8. Runtime 配置状态

仓库默认：

```text
enabled = false
status = DISABLED_UNCALIBRATED
learning_enabled = false
residual_control_enabled = false
dcs_write_enabled = false
```

默认空的是**尚未标定的算法参数**：

```text
tracking = {}
so2_response = {}
so2_adaptation = {}
residual = {}
ph_response = {}
ph_adaptation = {}
ph_arbitration = {}     # 可只填算法项，例如 min_confidence
```

其中 pH安全/运行范围和供浆硬上下限并不是“缺失标定”，而是自动从 `PLANT_CONFIG` 注入。

状态语义：

```text
DISABLED_UNCALIBRATED
INVALID_INCOMPLETE_CALIBRATION
INVALID_CALIBRATION_CONFIG
CONFIGURED_SHADOW
```

代码不会把测试参数或经验值伪装成生产标定值。

## 9. Version lifecycle 与 plant-contract 绑定

```text
condition training/update
-> condition snapshot v###
-> MFAC version artifact v###
-> activate_mfac_version.py
-> canonical active_version.json
```

新 manifest 会记录：

```text
primary_mode = MFAC_PRIMARY_MODE
runtime_semantics = Q_TARGET=CLIP(...当前plant min/max...)
plant_contract_snapshot = {...}
```

`plant_contract_snapshot` 是**只读审计快照，不是新的参数事实源**。它记录版本生成时的：

```text
TARGET_SO2_COLUMN
供浆 min/max/feedback/unit
主塔 tower_id / pH column
pH safe/operating range / guard band
```

启动和热更新时，如果 manifest 中存在该快照，`IntegratedVersionManager` 会与当前 `PLANT_CONFIG` 比对。厂级物理参数已经变化但 MFAC artifact 没有重建时，旧 artifact 会被拒绝，避免新 plant 参数和旧 MFAC 版本静默混用。历史没有该快照的旧 manifest 保持读取兼容。

新 active pointer 只发布：

```text
integrated_version
backend = MFAC
condition {...}
mfac {...}
```

`IntegratedVersionManager` 同时校验：

```text
condition snapshot version/hash
MFAC manifest version/hash
MFAC -> condition version binding
MFAC primary_mode
optional plant-contract snapshot
legacy_second_module_present = false
LEARN/Residual/DCS-write activation flags = false
```

旧 `policy_*` / `slurry_policy` 仅用于历史读取兼容。

## 10. Canonical 输出与数据库

正式在线主命名空间：

```text
mfac_*
```

`slurry_policy_*` 与 `scheme2_shadow_*` 仅是迁移 alias，全部来自同一个 MFAC decision。

`t_model_result_*` 正式持久化 canonical `mfac_*` 字段；旧列保留用于历史兼容。`DataHandler` 对可兼容字段使用：

```sql
COALESCE(mfac_field, slurry_policy_field)
```

数据库 schema 采用非破坏式：

```text
CREATE TABLE IF NOT EXISTS
ALTER TABLE ADD COLUMN IF NOT EXISTS
```

因此不会删除旧月份历史数据。

## 11. Runtime restore 与模式切换

恢复必须满足：

```text
MFAC semantics version match
condition_snapshot_version match
mfac_context_id match
```

fallback 与 Coordinator 切换会迁移：

```text
last_valid_algorithm_target
```

因此切换点输入异常时继续使用 `HOLD_LAST_INVALID_INPUT`，不会因为 runtime owner 切换导致目标跳空。

## 12. 历史证据

原第二模块仍有价值的历史供浆事件能力已迁入：

```text
mfac_model/historical_episode_engine/
```

只用于：

```text
10s production-equivalent history
-> real supply-flow episode extraction
-> real delta_Q_actual
-> SO2 / pH response evidence
-> MFAC bootstrap / calibration
```

历史回放保持：

```text
replay_semantics = COUNTERFACTUAL_SHADOW
```

历史 actual flow 不能伪装成算法 target。

## 13. 当前完成状态

已经完成：

- 原 `slurry_policy_model` 物理删除；
- MFAC 正式替代第二模块；
- 单一 `MFACUnifiedRuntimePolicy`；
- Dynamic Qbase；
- SO2/pH 双响应与独立递推；
- SO2 residual + pH PASS/SCALE/BLOCK；
- non-accumulating residual HOLD；
- runtime persistence；
- canonical MFAC version lifecycle；
- canonical `mfac_*` 输出/数据库；
- fail-closed runtime builder；
- plant physical contract 单一事实源；
- `mfac_paths.py` 文件路径单一事实源；
- target SO2 标准字段单一事实源；
- `MFAC_PRIMARY_MODE` 运行身份单一事实源；
- runtime 禁止覆盖 plant-owned 硬边界；
- 手工 Coordinator 注入同样校验 plant contract；
- legacy active-version 路径定向迁移；
- manifest plant-contract 快照兼容校验；
- 对应回归测试已写入仓库。

## 14. 仍未打开的后续阶段

1. 用历史和 mock-DCS/现场数据完成 tracking、SO2 response、pH response 标定；
2. 将真实标定参数填入 runtime config；
3. 接入 formal DCS target-applied/readback adapter；
4. 证据合格后单独评审 online LEARN；
5. 再评审 non-zero residual；
6. 最后评审 DCS write。

实际 syntax / Scheme2 unit / integration 测试必须以真实执行环境结果为准，不能仅凭测试文件存在宣称通过。
