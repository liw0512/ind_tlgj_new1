# 第二模块 V2：自适应预测供浆控制改造方案

## 1. 改造目标

现有第二模块以历史 `STEP / PULSE / BOOST_STEP` 供浆事件为事实源，统计供浆动作及 SO2/pH 效果后形成 `supply_flow_prototypes.pkl`，在线根据当前工况和控制需求选择历史动作原型并输出 `TARGET_SUPPLY_FLOW`。

V2 不再把历史人工动作直接视为“应该复刻的最优动作”。历史动作改作装置系统辨识的自然激励，主要用于学习：

1. 外部扰动对净烟气 SO2 的动态传播；
2. 外部扰动对 pH 储备/消耗的动态影响；
3. 实际供浆变化对净烟气 SO2 的动态作用；
4. 实际供浆变化对 pH 的动态作用；
5. 上述响应的延迟、增益、时间常数、置信度和随工况变化的修正系数。

在线控制目标保持为塔级 `TARGET_SUPPLY_FLOW`，不直接写 DCS，不退回阀位动作。

最终控制思想：

```text
当前实时状态
  +
FAST_CHANGE 外扰上下文
  +
动态响应模型
        ↓
未来 5~10 min SO2 / pH 轨迹预测
        ↓
滚动优化多个供浆候选轨迹
        ↓
满足 SO2 目标 + pH 储备约束 + 安全约束
同时最小化供浆量和 ΔQ 波动
        ↓
只执行下一个 10 s TARGET_SUPPLY_FLOW
        ↓
真实 Q_actual / SO2 / pH 回传
        ↓
预测偏差修正 + 慢参数自适应
```

---

## 2. 不修改的系统边界

### 2.1 第一模块 condition_model

职责保持：工况划分和稳定工况上下文。

V2 不要求第一模块承担供浆响应学习。第二模块只消费第一模块已经冻结的工况标签、condition snapshot 和 `condition_axes` 配置。

### 2.2 fast_change_mode

职责保持：检测外部快速扰动，而不是学习具体供浆动作。

FAST 输出在 V2 中的用途从“匹配 FAST 历史供浆动作”升级为“未来扰动预测上下文”。

钢厂典型：`condition_axes = [yyq_SO2]`。

电厂典型：`condition_axes = [jzfh, yyq_SO2]`。

不得在第二模块中硬编码 `jzfh`。换厂时以 `plant_config.py -> condition_axes` 为事实源。

`yyq_LL` 第一版不进入核心快速扰动模型；如后续残差分析证明它确有稳定增益，再作为低频/强平滑辅助变量加入。

### 2.3 TARGET_SUPPLY_FLOW 输出契约

对外保持：

```text
recommendation_type = TARGET_SUPPLY_FLOW
tower_id
current_flow
target_final_flow
target_final_flow_range
action_direction
reason_codes
```

V2 可增加预测诊断字段，但不能破坏旧执行适配器和 DCS 边界。

### 2.4 Safety Envelope / 执行层

最终目标流量仍必须经过现场工程限幅、pH 安全边界、流量计有效性、泵状态、自动许可等约束。

预测控制器没有权限绕过安全层。

---

## 3. 核心设计原则

### 3.1 学装置响应，不学人工答案

历史人工动作可能过量、滞后或脉冲明显，因此：

- `STEP / PULSE / BOOST_STEP` 保留；
- 事件类型用于识别“系统受到何种供浆激励”；
- 不再默认把动作峰值/最终流量当成在线目标；
- 好动作、动作不足、动作过量都可以提供系统辨识信息；
- 真正应剔除的是无法归因、信号错误或执行不明确的数据。

### 3.2 可辨识性与动作质量分离

V2 新增独立标签：

```text
IDENTIFIABLE
WEAKLY_IDENTIFIABLE
UNIDENTIFIABLE
```

它回答“这个片段能不能用于学习物理响应”，而不是“人工动作是否优秀”。

另外保留效果标签用于审计：

```text
EFFECTIVE
UNDER_ACTION
OVER_ACTION
UNSAFE
UNKNOWN_EFFECT
```

两类标签不得混用。

### 3.3 闭环历史数据不能做简单相关回归

人工供浆本来就受 `jyq_SO2 / pH / yyq_SO2 / jzfh` 等状态驱动，因此 `Q_actual` 是内生变量。

V1 预测模型至少采用：

1. 因果时间序列特征；
2. 只使用过去和当前信息；
3. ARX/FIR + Ridge 正则作为可解释基线；
4. 动作与扰动同时进入模型，避免把入口扰动误归给供浆；
5. 后续升级采用残差化/orthogonalized response learning 降低闭环混杂；
6. 任何离线未来数据只能用于标签、评估和辨识训练，不能成为在线输入。

### 3.4 pH 是储备状态，不是唯一主目标

一级目标：净烟气 SO2 相对运行时目标值的风险。

二级目标：pH 保持在安全且足够的储备区间。

三级目标：尽量少供浆。

四级目标：供浆轨迹尽量平滑。

不得建立固定的 `yyq_SO2 rate -> pH rate` 单变量映射作为主控制逻辑。

---

## 4. V2 技术栈

### 4.1 Offline Identification

输入：第一模块标注后的 10 s 历史数据。

处理：

```text
数据质量校验
  ↓
供浆事件检测（复用现有 detector）
  ↓
可辨识性评分
  ↓
构造因果训练样本
  ↓
动态系统辨识
  ↓
response_model.pkl/json
```

第一版辨识四类关系：

```text
condition_axes -> jyq_SO2
condition_axes -> tower pH
Q_actual       -> jyq_SO2
Q_actual       -> tower pH
```

实际实现按“一个输出一个因果动态模型”组织，模型可同时包含多个外部扰动和一个塔级总供浆输入。

### 4.2 State Estimation

实时维护：

- 过去一段时间 `Q_actual`；
- `yyq_SO2 / jzfh` 等 condition axis 历史；
- `jyq_SO2 / pH` 历史；
- 当前预测 bias；
- 已经发出但尚未完全兑现的供浆动态作用；
- 模型置信度和模型健康度。

第一版采用输出偏差校正；后续可升级 Kalman / disturbance observer。

### 4.3 Future Predictor

每 10 s 滚动预测未来 5~10 min：

```text
自由响应
+
过去供浆动作的剩余响应
+
未来候选供浆轨迹的响应
+
外部扰动预测
```

预测重点不是精确报出某分钟 SO2 小数点值，而是正确判断：

- 是否会跨越 `jyq_SO2 target`；
- 大约什么时候跨越；
- pH 储备是否会不足；
- 当前供浆动作是否足够；
- 哪个候选轨迹在安全范围内最平滑、最经济。

### 4.4 DMC/MPC-lite Optimizer

第一版不直接引入复杂非线性优化器，先实现候选轨迹/二次代价选择：

目标函数概念：

```text
J = SO2_target_risk
  + pH_zone_violation
  + slurry_usage
  + delta_Q_smoothness
  + out_of_distribution_penalty
```

硬约束：

- `Q_min <= Q_target <= Q_max`；
- `|ΔQ_10s| <= max_delta_q_per_cycle`；
- pH 安全范围；
- Safety Envelope；
- 数据质量和模型置信度。

只执行优化结果的第一个 10 s 动作，下一周期重新预测和求解。

### 4.5 Online Adaptation

分三个时间尺度：

1. **快偏差修正**：预测值和实际值产生偏差后，修正未来轨迹 offset；
2. **慢参数自适应**：在可辨识窗口内，用 RLS/带遗忘因子的参数估计更新少量 gain/delay/efficiency；
3. **离线增量训练**：累计新高质量 episode 后重新完整辨识，发布新的 `v###` 正式版本。

在线参数必须有：

- 参数上下界；
- 最大更新速率；
- 最小激励门槛；
- 传感器健康门槛；
- 可辨识性门槛；
- 漂移过大时冻结学习。

---

## 5. 跨厂泛化设计

目标不是“A 厂参数直接复制给 B 厂”，而是：

```text
同一代码
同一训练 pipeline
同一 runtime
同一预测控制器
同一自适应机制
       ↓
不同厂仅重新辨识：
动态系数 / 延迟 / 增益 / 约束 / 目标 / 置信度
```

### 5.1 厂级参数来源

继续以 `plant_config.py` 为唯一事实源：

- `condition_axes`；
- tower / pH；
- supply flow；
- circulation pump；
- pH safe range；
- outlet SO2 safe range。

第二模块不能再写死“负荷 + 原烟气 SO2”两个轴。

### 5.2 工厂慢漂移

煤质、石灰石活性、设备磨损、喷淋效率下降等首先表现为预测残差持续偏移。

短期由 bias/state estimator 吸收，长期由 gain/delay 自适应和增量重训吸收。

结构性变化（设备拓扑、传感器位置、循环泵结构等）不能仅靠在线自适应解决，应触发模型重辨识和新版本发布。

---

## 6. 版本快照 V2 目标结构

保持现有 `v###` 和第一模块版本原子对齐。

目标新增：

```text
snapshots/v###/
  effective_config.json
  manifest.json
  training_summary.json
  datasets/
    valid_decision_episodes.*
    invalid_decision_episodes.*
    identification_episodes.csv
    identification_episodes.pkl
  global/
    supply_flow_prototypes.pkl          # 兼容/安全先验，降级为 fallback + OOD 护栏
    adaptive_response_model.pkl         # 新核心
    adaptive_response_model.json        # 可审计摘要
    identification_summary.json
```

在线加载新模型时仍使用 manifest/hash 校验。

---

## 7. 现有代码的修改映射

### 保留并复用

- `_engine/supply_flow_event_detector.py`：继续识别供浆激励；
- `_engine/supply_flow_event_classifier.py`：继续描述动作形态；
- `_engine/supply_flow_effect_profiler.py`：逐步升级为响应轨迹/延迟提取；
- `fast_change_mode/*`：保持外扰识别职责；
- `slurry_policy_online/target_flow_execution_adapter.py`：保持 DRY_RUN；
- `slurry_policy_online/fast_action_envelope.py`：保留安全/FAST实时约束；
- `RuntimeStore / active_version / manifest`：继续使用现有机制。

### 新增

```text
slurry_policy_model/adaptive_predictive/
  __init__.py
  config.py
  identifiability.py
  model_types.py
  feature_builder.py
  arx_identifier.py
  response_model.py
  predictor.py
  dmc_optimizer.py
  bias_corrector.py
  parameter_adapter.py
  model_health.py
  shadow_advisor.py
```

### 后续替换点

现有：

```python
target_supply_flow = self.flow_advisor.recommend(...)
```

迁移：

```text
legacy_target = SupplyFlowAdvisor.recommend(...)
predictive_target = PredictiveFlowAdvisor.recommend(...)

SHADOW 阶段：
  legacy_target 继续作为主输出
  predictive_target 仅写 debug/log

验证通过后：
  predictive_target 成为主输出
  legacy prototype 仅作为 fallback / OOD safety prior
```

---

## 8. 实施阶段

## P0：基础语义和安全迁移

目标：建立 V2 代码骨架，不改变现有在线主输出。

任务：

- 新分支；
- 本方案文档；
- 新增 adaptive_predictive package；
- 新增“可辨识性”分类器；
- 新增跨厂 measured-disturbance 解析；
- 明确不默认使用 `yyq_LL`；
- 新增模型 artifact 数据结构；
- 单元测试；
- 旧 Advisor 不改。

验收：旧测试语义不受影响，新模块可以独立导入和测试。

## P1：离线动态辨识 V1

目标：从历史数据得到第一版可审计动态模型。

任务：

- 因果 lag feature builder；
- 塔级总供浆构造；
- `ARX/FIR + Ridge` 基线辨识；
- `condition_axes` 同时进入扰动模型；
- 分别建立 `jyq_SO2` 和每塔 pH 输出模型；
- 时间切分验证，禁止随机泄漏；
- 输出 MAE/RMSE/R2、方向正确率、target-crossing recall；
- 输出 lag/gain/delay 诊断；
- 写入 snapshot + manifest；
- 增量训练重新辨识全量累计 episode，而不是简单拼参数。

验收：历史 replay 上优于“只看当前值/当前趋势”的简单基线，并且系数方向和时延没有明显物理冲突。

## P2：在线 Shadow 预测

目标：每10秒给出未来 SO2/pH 轨迹，但不改变控制动作。

任务：

- predictor；
- 历史缓存；
- external disturbance scenario；
- bias correction；
- 模型置信度；
- 在线日志保存 prediction vs actual；
- dashboard/debug 字段。

验收：Shadow 连续运行时不影响旧目标流量输出；预测误差可追溯。

## P3：DMC/MPC-lite Shadow 动作规划

目标：计算平滑供浆目标，但仍不实际替代旧 Advisor。

任务：

- 候选供浆轨迹生成；
- SO2 target cost；
- pH zone cost；
- slurry usage cost；
- ΔQ smoothness cost；
- OOD/历史支持域 penalty；
- Safety Envelope；
- 对比历史人工/旧 prototype 的动作波动和目标风险。

验收：历史 replay 中 `ΔQ` 波动显著低于旧策略，且 target-crossing 风险不增加。

## P4：在线自适应

目标：解决煤质、浆液品质、设备磨损等慢漂移。

任务：

- prediction bias；
- RLS gain scaling；
- 可辨识性 gate；
- parameter bounds；
- model drift；
- freeze/recovery；
- runtime adaptation state persistence。

验收：人为注入小幅 gain drift 的 replay 中，模型能逐渐收敛且不越过参数安全界。

## P5：主策略切换

目标：Predictive Advisor 成为主 `TARGET_SUPPLY_FLOW` 来源。

前置条件：

- Shadow 预测稳定；
- DMC replay 指标通过；
- 安全边界通过；
- 运行时降级路径通过；
- 无模型/低置信度时可回退保守 prototype/HOLD；
- DCS write 仍由独立执行层控制。

---

## 9. 关键评估指标

### 预测层

- SO2 future MAE/RMSE；
- pH future MAE/RMSE；
- 趋势方向准确率；
- 未来越目标的 recall / precision；
- 预测区间覆盖率；
- 不同 FAST/REGULAR 状态下误差分层。

### 控制层

- `jyq_SO2` 超目标时长；
- `jyq_SO2` 标准差/峰峰值；
- pH 标准差/峰峰值；
- pH 越界时间；
- 单位时间供浆总量；
- `Σ|ΔQ|` 和 `Σ(ΔQ)^2`；
- 大幅脉冲动作次数；
- 反向动作次数；
- Safety block 次数。

### 模型健康

- rolling prediction bias；
- rolling residual variance；
- parameter drift；
- identification confidence；
- OOD ratio。

---

## 10. 第一版明确不做的事情

- 不使用强化学习直接输出供浆；
- 不把历史人工动作作为监督 label；
- 不要求大量现场大阶跃试验；
- 不默认把 `yyq_LL` 放进核心扰动模型；
- 不在第二模块硬编码电厂 `jzfh`；
- 不一开始使用深度神经网络；
- 不在 Shadow 验证完成前替换现网 `SupplyFlowAdvisor`；
- 不开放任何 DCS 写权限。

---

## 11. 当前分支实施顺序

当前新分支：`codex/adaptive-predictive-slurry-v1`

按以下顺序提交：

1. **P0.1** 方案文档；
2. **P0.2** `adaptive_predictive/config.py + identifiability.py + model_types.py`；
3. **P0.3** foundation unit tests；
4. **P1.1** causal feature builder；
5. **P1.2** ARX/FIR identifier；
6. **P1.3** snapshot artifact 接入；
7. **P2** online shadow predictor；
8. **P3** DMC/MPC-lite shadow optimizer；
9. **P4** online adaptation；
10. **P5** 主策略切换。

每个阶段必须保持可运行、可回退、可审计，不跨阶段一次性替换现有第二模块主链。
