# 第二模块：基准供浆 + FAST 前馈 + 双响应自适应反馈

## 1. 方向重置

第二模块不再采用 DMC/MPC、未来轨迹预测或 ARX 预测模型作为控制核心。

新的核心目标不是回答“未来 5~10 min SO2/pH 会是多少”，而是持续回答：

1. 当前烟气负荷下理论上应连续供多少浆；
2. 当前入口扰动是否要求提前增加/减少供浆；
3. 已经执行的实际供浆变化是否正在等待响应；
4. 实际 Q 变化最终带来了多少 SO2 和 pH 响应；
5. 当前 SO2/pH 偏差还需要补多少、减多少，或是否应等待/回退。

规范结构：

```text
物料衡算 Qbase
    +
FAST disturbance feedforward
    +
SO2 adaptive feedback
    +
pH reserve feedback / constraint
    +
pending-action delay management
    +
dual-response online learning
    ↓
TARGET_SUPPLY_FLOW
    ↓
Safety Envelope / DRY_RUN execution boundary
```

任何预测模型都不进入在线控制核心。

---

## 2. Qbase：物理基准供浆

单位严格采用：

```text
c_in, c_out_target : mg/Nm3
G                    : Nm3/h
rho                  : kg/m3
omega                : 0..1 质量分数
purity               : 0..1
Q                     : m3/h
```

计算：

```text
removed_SO2_kg_h = (c_in - c_out_target) * G / 1e6
stoich_CaCO3_kg_h = removed_SO2_kg_h * 100 / 64
q0 = stoich_CaCO3_kg_h / (purity * omega * rho)
Qbase = CaS_ref * q0
```

关键约束：

- c_out_target 使用运行目标/设计出口 SO2，不使用当前 jyq_SO2；
- 当前 jyq_SO2 只进入反馈层；
- omega 在计算中必须是 0..1 质量分数；若现场标定给出百分数，先除以 100；
- 不允许同时对 SO2 浓度和烟气量做不一致的 O2 基准换算；
- 初版建议 Qbase 使用固定工程 Ca/S_ref（优先 1.7），pH 单独进入反馈/约束层；
- pH->Ca/S 表只作为离线灵敏度核算或后续可选工程模式，避免 pH 在 Qbase 和反馈中被重复使用。

### 2.1 固含量

工程表给出 `omega = k*rho + C` 时必须明确输出单位：

```text
relation_output_unit = percent | fraction
```

当前训练数据的粗质量衡算表明，`k=0.0013, C=1.3` 与本厂数据不匹配，不能直接用于生产 Qbase。

上线前应使用“实验室固含量 + 同时刻密度”样本重新标定 k/C，或先使用经过确认的固定固含量。

---

## 3. FAST：当前扰动前馈，不做未来预测

Qbase 已经响应入口 SO2 和烟气量 level；FAST 只负责补偿“变化太快、装置存在传输/反应延迟”的情况。

输入：

```text
yyq_SO2 level / rate / acceleration
烟气量 level / rate（若质量流量语义可靠）
第一模块 condition / OOD / context-shift 状态
```

输出：

```text
DeltaQ_FAST
FAST_UP / FAST_DOWN / HOLD
severity
expire_after
reason_codes
```

FAST 只产生有限时、有限幅度的提前补偿，不预测未来出口轨迹。

---

## 4. 双响应学习

分别维护：

```text
phi_so2 = Delta(jyq_SO2) / Delta(Q_actual)
phi_ph  = Delta(pH)      / Delta(Q_actual)
```

两条响应必须独立维护：

- delay；
- response observation window；
- effective delta；
- confidence；
- sample/support count；
- last update；
- global estimate；
- supported condition correction。

物理方向先验：

```text
phi_so2 < 0
phi_ph  > 0
```

方向不符的单次片段不能直接更新在线参数，应进入异常/混杂审计。

### 4.1 Offline bootstrap

从历史 Q_actual 动作中寻找可归因 excitation：

- Q_actual 确实发生足够变化；
- 流量计完整；
- 供浆泵/循环泵拓扑在核心窗口内不改变；
- 响应窗口完整；
- 入口 SO2/烟气负荷剧烈扰动时降权或剔除；
- 分开估计 SO2 与 pH delay/window；
- 采用 robust median / trimmed statistics 建立初始 phi 与置信度。

### 4.2 Online recursion

只有完成实际响应观察后才更新 phi。

推荐采用有界递推：

```text
phi_new = clip(
    (1-alpha) * phi_old + alpha * phi_observed,
    physical_lower,
    physical_upper,
)
```

alpha 随置信度、工况稳定度、扰动污染程度调整。

第一版不允许单次动作大幅重写长期 phi。

---

## 5. SO2 自适应反馈

定义：

```text
e_so2 = jyq_SO2 - SO2_target
```

死区内不动作。

有可靠 phi_so2 时，可采用正则化逆响应增量：

```text
DeltaQ_SO2_raw = -rho_u * phi_so2 * e_so2 / (lambda_u + phi_so2^2)
```

因为正常 `phi_so2 < 0`，当出口 SO2 高于目标时自然得到正的增浆方向。

最终必须经过：

- deadband；
- per-cycle DeltaQ limit；
- total correction limit；
- confidence scaling；
- pending-action gate；
- safety envelope。

置信度低时不使用精确反算，退化为小步 bounded increment/decrement。

---

## 6. pH 反馈与约束

pH 不作为与 SO2 同等级的自由优化目标，而作为化学储备状态与安全约束。

建议定义：

```text
pH_low_guard
pH_low_target
pH_high_target
pH_high_guard
```

作用：

- pH 过低：提高供浆下限/禁止减浆；
- pH 低且 SO2 高：允许更强增浆；
- pH 正常：SO2 控制为主；
- pH 偏高且 SO2 低：优先减浆；
- pH 快速上升且 SO2 已明显改善：触发 rollback 候选。

若 phi_ph 可靠，可估计恢复储备所需的 bounded DeltaQ_PH；但它必须服从 SO2 优先级和 safety 状态。

---

## 7. Pending Action Ledger：解决延迟，而不是预测延迟

每次真实 Q_actual 发生有效变化后创建 pending：

```text
action_id
start_time
Q_before
Q_after_actual
DeltaQ_actual
condition_label
FAST context
SO2 evaluation start/end
pH evaluation start/end
status
```

状态机：

```text
IDLE
  -> ACTION_PENDING
  -> WAIT_SO2_RESPONSE
  -> WAIT_PH_RESPONSE
  -> RESPONSE_EVALUATED
  -> CLOSED
```

在 pending 未进入可评价窗口之前：

- 默认 HOLD；
- 不因出口暂时没变化而重复加浆；
- 只有入口扰动继续明显恶化时，允许受限的 supplemental increment；
- 任何 supplemental action 都必须合并/重置对应 pending attribution。

---

## 8. Increment / Hold / Rollback

每个 10s 周期先判断状态，而不是每周期都重新算一个完全独立目标。

### HOLD

- 已有动作仍在 delay/window 内；
- 当前误差在 deadband；
- phi 置信度不足；
- 数据质量异常；
- 工况/OOD 不允许积极自适应。

### INCREMENT

响应窗口结束后，若：

- SO2 仍高；
- 实际改善不足；
- pH 仍有安全储备；
- 外扰未显著反转；

则在上一个实际 Q 平台基础上小步追加。

### ROLLBACK

若：

- SO2 明显低于目标；
- pH 偏高/快速上升；
- 上一动作出现过响应；

则逐步回退，不直接回到动作前平台。

---

## 9. 最终目标流量融合

逻辑上：

```text
Q_nominal = Qbase + DeltaQ_FAST
Q_feedback = Q_nominal + DeltaQ_SO2
Q_ph_guarded = apply_pH_guard(Q_feedback)
Q_pending = apply_pending_state(Q_ph_guarded)
Q_safe = SafetyEnvelope(Q_pending)
TARGET_SUPPLY_FLOW = Q_safe
```

不建议简单写成所有 DeltaQ 无条件相加；pH 与 pending 更适合作为带优先级的 guard/state transformation。

---

## 10. 第一模块的作用

第一模块继续只负责：

```text
condition_label / region
condition_valid
EDGE / CORE / OOD
context shift / review state
```

第二模块响应参数采用：

```text
phi_current = phi_global + Delta_phi_condition
```

原则：

- C1/C2/C3 有支持时允许 condition correction；
- C4 更强 shrinkage；
- EDGE_LOW / EDGE_HIGH Global-only；
- context shift 不自动改变物理响应参数；
- 数据不足始终退回 global estimate。

---

## 11. 实施顺序

### Stage A - Qbase

1. 完成单位严格的物料衡算；
2. 确认 SO2/G 的标态、干基、O2 基准语义；
3. 用实验室固含量重新标定密度->固含关系；
4. 先 shadow 对比长周期实际平均供浆。

### Stage B - Offline dual response bootstrap

1. 提取 Q_actual excitation；
2. 分别统计 SO2/pH delay；
3. 分别统计 phi_so2/phi_ph；
4. 输出 global + condition-support 报告；
5. 不产生在线控制动作。

### Stage C - Pending/Hold 状态机

1. 仅 shadow 跟踪真实动作；
2. 验证不会在响应延迟内重复追加；
3. 验证 response attribution。

### Stage D - Adaptive feedback shadow

1. 生成 TARGET_SUPPLY_FLOW shadow；
2. 与人工实际 Q 对比；
3. 重点评估 SO2 超限、pH 越界、供浆总量、动作次数和回退次数；
4. 不以预测 R2 为验收指标。

### Stage E - Dry-run execution integration

保持现有 Safety Envelope 和 DCS write off，完成长期 shadow 后再讨论激活。

---

## 12. 验收指标

不再使用“未来预测 R2”作为主指标。

重点验收：

```text
SO2 超目标时间占比
SO2 高风险持续时间
pH 低/高越界时间
单位时间浆液消耗
DeltaQ 总变差 / 动作次数
pending 期间重复追加率
动作后有效响应比例
rollback 命中率
phi 方向一致率与置信度
不同 condition 下的响应稳定性
```

最终目标是在满足 SO2 与 pH 安全的前提下，减少过供浆和无效动作，并保持控制可解释、可追溯。
