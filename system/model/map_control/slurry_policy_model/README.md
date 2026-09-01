# 第二模块：物理基准供浆 + 非预测自适应反馈

第二模块当前规范方向已经从 DMC/MPC/未来轨迹预测切换为：

```text
物料衡算 Qbase
+
FAST 当前扰动前馈
+
SO2 自适应反馈
+
pH 储备反馈/约束
+
Pending Action 延迟管理
+
Q_actual -> SO2 / pH 双响应学习
    ↓
TARGET_SUPPLY_FLOW
```

在线控制核心不训练或调用未来 SO2/pH 轨迹预测模型，也不以预测 R2 作为控制验收指标。

详细设计见：

`BASELINE_ADAPTIVE_FEEDBACK_CONTROL_PLAN.md`

## 1. 模块边界

第一模块继续提供稳定工况上下文：

```text
condition_label / region
condition_valid
EDGE / CORE / OOD
context shift / review state
```

第二模块不重新划分工况，只消费这些状态。

FAST 模块继续负责当前入口扰动的 level/rate/severity 识别；它在第二模块中用于有限时前馈补偿，而不是用于预测未来轨迹。

规范动作仍然是塔级：

```text
recommendation_type = TARGET_SUPPLY_FLOW
```

不直接输出阀门增量，不绕过 Safety Envelope，不直接写 DCS。

## 2. Qbase

单位严格的物料衡算实现位于：

`adaptive_feedback/qbase.py`

核心：

```text
removed_SO2_kg_h = (c_in - c_out_target) * G / 1e6
stoich_CaCO3_kg_h = removed_SO2_kg_h * 100 / 64
q0 = stoich_CaCO3_kg_h / (purity * solids_fraction * density)
Qbase = CaS_ref * q0
```

其中：

- `c_in`：入口 SO2，mg/Nm3；
- `c_out_target`：目标/设计出口 SO2，不使用当前出口反馈值；
- `G`：烟气量，Nm3/h；
- `density`：浆液密度，kg/m3；
- `solids_fraction`：0..1 质量分数，例如 20% 必须写 0.20；
- `purity`：石灰石纯度，默认工程值 0.90；
- `CaS_ref`：基准钙硫比，初版优先固定工程参考值；
- pH 单独进入反馈/约束层。

SO2 浓度与烟气量必须处在相互一致的标态/干基/O2 基准，代码不会进行只有一侧的隐式 O2 修正。

## 3. 双响应自适应

历史与在线都学习实际执行反馈：

```text
phi_so2 = Delta(jyq_SO2) / Delta(Q_actual)
phi_ph  = Delta(pH)      / Delta(Q_actual)
```

两条响应分别维护 delay、观察窗口、置信度与支持样本。

控制层不要求精确预测未来值，只要求：

- 响应方向可信；
- 动作增量可解释；
- 延迟期不重复追加；
- 响应不足时小步 increment；
- 过响应时 rollback；
- 不确定时 HOLD。

## 4. Pending Action

每次有效实际 Q 变化都进入 pending ledger。等待对应 SO2/pH 响应窗口完成前，默认 HOLD；入口扰动继续明显恶化时才允许受限补充动作。

这部分用状态机解决装置延迟，而不是靠未来轨迹预测解决延迟。

## 5. pH

pH 是化学储备状态和安全约束，而不是与 SO2 并列的自由预测目标。

初版建议：

```text
Q_nominal = Qbase + DeltaQ_FAST
Q_feedback = Q_nominal + DeltaQ_SO2
Q_ph_guarded = apply_pH_guard(Q_feedback)
Q_pending = apply_pending_state(Q_ph_guarded)
TARGET_SUPPLY_FLOW = SafetyEnvelope(Q_pending)
```

pH 偏低时提高供浆下限/禁止减浆；pH 偏高且 SO2 已低时促进减浆/rollback。

## 6. 历史原型逻辑

仓库中原有 `STEP / PULSE / BOOST_STEP` 事件检测和历史 `supply_flow_prototypes` 训练路径暂时保留为历史审计/数据提取工具，但它不再代表新的控制核心，也不应被理解为“复制历史人工动作作为最优动作”。

后续可复用其中的事件窗口、执行反馈和效果统计能力来 bootstrap `phi_so2 / phi_ph`，而不是用于 DMC/MPC。

## 7. 执行边界

当前执行层继续保持 DRY_RUN / DCS write off。新的非预测控制器必须先通过长周期 shadow replay，重点验收：

```text
SO2 超目标时间
pH 越界时间
浆液总消耗
动作次数和 DeltaQ 总变差
pending 期间重复追加率
响应有效率
rollback 命中率
phi 方向稳定性和置信度
```

## 8. 10 秒数据语义

在线仍每 10 秒决策一次。连续量采用当前既定的 10 秒语义，离散状态使用当前状态；离线 bootstrap 与在线必须保持同样的数据定义。
