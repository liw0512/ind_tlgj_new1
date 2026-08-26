# Scheme 2 MFAC Model

本目录是方案2独立 MFAC sidecar，不替代 `condition_model`，也不修改方案1现有 `slurry_policy_model` 的在线行为。

## Phase 1 边界

当前只建立数据合同与上下文映射：

```text
condition_model output
-> MFACContextResolver
-> mfac_context_id
```

以及未来工况合并验证接口：

```text
ActionResponseEvent statistics
-> MFACCompatibilityGate
-> compatible / incompatible / insufficient evidence
```

## 为什么独立目录

真实第一模块已经具备：

- condition snapshot 版本；
- previous snapshot version；
- base grid / condition label / policy region；
- 自动合并；
- region lifecycle events；
- 增量快照。

MFAC 不应把 `phi`、confidence、动作响应事件写回 `GridCell` 或 `PolicyRegion`，否则会把第一模块的工况职责与第二模块的控制学习职责重新耦合。

## V1 默认 Context 规则

默认：

```text
condition_label -> MFAC-COND-<condition_label>
```

当方案1某个合并对 MFAC 不兼容时，可在 resolver artifact 中按 `base_condition_id` 发布 override：

```text
condition_label = 8
base_condition_id = 12 -> MFAC-BASE-12
base_condition_id = 13 -> MFAC-BASE-13
```

因此方案1可以继续共享同一 condition/policy region，而方案2仍可保留不同灵敏度上下文。

## 下一阶段

1. ActionDetector
2. ResponseDetector
3. EligibilityGate
4. ActionResponseEvent 审计表
5. 每 context `phi_prior / delay / confidence`
6. 递推历史回放
7. shadow online
