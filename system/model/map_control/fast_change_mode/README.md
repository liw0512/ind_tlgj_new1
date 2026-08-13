# fast_change_mode

独立的快速变化风险识别模块。

## 1. 职责

本模块只负责判断：

```text
当前是否处于入口快速变化阶段？
入口是快速上升、快速下降还是混合变化？
当前净烟气 SO2 的效果/排放风险有多高？
FAST_CHANGE 当前处于进入、保持、恢复还是退出状态？
```

本模块**暂时不负责具体供浆阀动作**，也不直接修改 `slurry_policy_model`。
后续由第二模块读取这里输出的 FAST 上下文，再讨论 TRANSIENT 历史经验、动作允许域和规则回退。

## 2. 核心原则

### 2.1 FAST 由入口趋势触发

实际趋势字段来自：

```text
system/model/config/plant_config.py
    ↓
condition_axes
```

因此支持 1 或 2 个任意工况轴，不再写死负荷、锅炉风量或原烟气 SO2。

当前配置若仍为：

```text
axis1 = jzfh
axis2 = yyq_SO2
```

则自然等价于监测负荷和原烟气 SO2 快变。

### 2.2 出口 SO2 不单独触发 FAST

`jyq_SO2` 用于：

```text
目标偏差
WARNING
EMERGENCY
出口变化率
```

这些信息会影响 `effect_risk` 和 `overall_risk`，但不会把一个入口稳定的过程错误标记成 FAST_CHANGE。

因此：

```text
入口稳定 + jyq_SO2 超标
→ 不是 FAST_CHANGE
→ 但 effect_risk = EMERGENCY
```

而：

```text
入口快速上升 + jyq_SO2 仍在目标附近
→ FAST_CHANGE
→ effect_risk 可以仍为 LOW
```

这正是 FAST 前馈保护和出口反馈控制之间的职责分离。

## 3. 趋势识别

借鉴旧项目 `ind_optim_serv_xire/fast_change_mode` 的思路：

```text
原始入口信号
→ 因果 DEMA 平滑
→ 时间窗口变化率
→ 同方向变化占比
→ SLOW / FAST
```

当前默认阈值按每个 condition axis 的 grid step 自动换算：

```text
slow = 0.10 × grid_step / min
fast = 0.30 × grid_step / min
```

例如：

```text
jzfh step = 10
→ slow ≈ 1 MW/min
→ fast ≈ 3 MW/min

yyq_SO2 step = 200
→ slow ≈ 20 mg/Nm3/min
→ fast ≈ 60 mg/Nm3/min
```

如现场需要，可以在 `fast_change_config.py -> trend.axis_overrides` 中按字段覆盖。

## 4. 趋势模式

典型输出：

```text
STEADY
AXIS1_RISE_SLOW
AXIS1_RISE_FAST
AXIS2_DROP_FAST
AXIS1_AND_AXIS2_RISE_FAST
MIXED_DISTURBANCE_FAST
```

同时输出抽象方向：

```text
NONE
RISE
DROP
MIXED
```

第二模块后续可以使用抽象方向做 FAST 动作逻辑，同时保留 exact mode 做历史 TRANSIENT 精细匹配。

## 5. 效果风险

效果风险只看标准出口字段：

```text
jyq_SO2
outlet_so2_target
outlet_so2_safe_range
```

主要状态：

```text
TARGET_BAND
ABOVE_TARGET
ABOVE_TARGET_FAR
BELOW_TARGET
BELOW_TARGET_FAR
WARNING
EMERGENCY
```

并额外保留：

```text
outlet_so2_rate
RISING_FAST / RISING / STABLE / FALLING / FALLING_FAST
```

出口变化率只用于风险升级，不参与 FAST 触发。

## 6. 状态机

```text
REGULAR
   ↓ 入口检测到 FAST
FAST_CHANGE
   ↓ FAST 消失 + 最短保持完成 + 连续稳定周期满足
FAST_RECOVERY
   ↓ recovery_hold_minutes 完成
REGULAR
```

FAST 在 recovery 过程中再次出现时，立即重新进入 `FAST_CHANGE`。

## 7. 主要输出字段

```text
fast_change_mode
fast_change_active
fast_change_recovery_active
fast_change_raw_trigger
fast_change_direction
fast_change_severity
fast_change_exact_trend_mode
fast_change_trend_risk_level
fast_change_effect_risk_level
fast_change_effect_state
fast_change_effect_direction
fast_change_overall_risk_level
fast_change_axis_rates
fast_change_axis_levels
fast_change_axis_direction_ratios
fast_change_trigger_axes
fast_change_outlet_so2_rate
fast_change_outlet_so2_trend
fast_change_reason_codes
```

所有字段统一使用 `fast_change_` 前缀，避免现在就和第二模块已有 `control_mode / disturbance_mode` 字段发生冲突。

## 8. 当前阶段边界

本次只搭建 FAST_CHANGE 独立模块，因此尚未修改：

```text
slurry_policy_model 的 TRANSIENT 聚合
FAST 专属历史效果评价
FastActionEnvelope
候选过滤
FAST_RULE_BASELINE
WAITING_EFFECT / reverse lock
P4PC 在线主链
```

这些等 FAST 模块本身稳定后再逐项衔接。


## 9. 离线/在线生命周期与容量控制

FAST 模块不会长期保存一份不断膨胀的完整标注 CSV。

- 在线：每条数据只更新 detector 的短时间窗口，`runtime/checkpoint.json` 定期覆盖写；
- FAST 事件只在闭合后写入月度 JSONL 摘要，不保存每个普通采样点；
- 离线初次：历史数据按 `date` 排序后因果回放同一个 detector；
- 离线增量：读取上一版 checkpoint，只回放新增数据；
- 与第二模块一起发布时，FAST 使用同一个 `v###` 版本号，并滚动保留最近配置数量的快照；
- 第二模块训练过程中需要的逐行 FAST 标签只存在于本次训练 DataFrame/context tail 中，不额外永久复制原始 CSV。

因此“逐行调用同一个 FastChangeModeDetector”既用于真实在线，也用于离线历史回放；
离线逐行的目的，是严格模拟在线因果状态机，而不是永久积累逐行文件。

## 10. 与 slurry_policy_model 的关系

第二模块从 V4 开始不再拥有独立的 `DisturbanceMonitor`。FAST 唯一事实源为本模块：

```text
fast_change_mode
  -> FAST exact/direction/effect risk
  -> slurry_policy_model
     -> TRANSIENT_EXACT
     -> TRANSIENT_DIRECTION_POOL
     -> FAST_RULE_BASELINE
```

TRANSIENT 历史评价额外统计动作前后净烟气 SO2 变化率、变化率抑制量、响应期峰值、
安全时间占比以及 WARNING/EMERGENCY 时间占比。因此 FAST_RISE 时即使 SO2 绝对值仍有
上升，只要上涨速度被压制且过程保持安全，也不会简单按“SO2 没下降”判成无效动作。
