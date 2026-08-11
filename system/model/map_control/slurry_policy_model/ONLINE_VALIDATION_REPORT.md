# 在线模块验证报告

## 版本

```text
整合包：1.9.0
离线核心：V1.8B.1 / 1.8.2 Python兼容修复
在线模块：V1.0
```

## 离线逻辑边界

本次没有修改离线训练的以下定义：

```text
ACTION/HOLD识别
基线、迟延、响应窗口
工况锚点和历史episode重映射
临近工况±2/±3映射与权重
SO2/pH响应统计
可靠性公式
工况、网格、临近、全厂先验和transient PKL结构
```

`slurry_policy_config.py` 仅新增在线路径和 `ONLINE_POLICY_CONFIG`；`PLANT_CONFIG`、`TRAINING_CONFIG` 的既有训练含义保持不变。离线聚合继续使用移除 `zip(..., strict=True)` 后的 Python 3.9 兼容文件。

## 已执行测试

### 1. 语法编译

```text
python -m compileall -q .
```

通过。

### 2. 在线合成快照端到端测试

```text
python -m unittest tests.test_online_policy tests.test_online_stability -v
```

通过项目：

```text
激活版本manifest/哈希加载
第一、第二模块版本握手
在线状态键与离线状态键一致
本地工况动作档案检索
历史代表阀门增量解析和限幅
实际执行反馈后进入WAITING_EFFECT
等待响应期间禁止普通重复动作
第一模块工况未稳定时HOLD
FAST_CHANGE进入、退出确认和FAST_RECOVERY
commanded_target/effective_target缓变
```

### 3. 原离线等价和回归测试

以下脚本通过：

```text
python tests/test_performance_equivalence.py
python tests/test_neighbor_optimization_equivalence.py
python tests/test_pickle_only_pipeline.py
python tests/test_version_alignment_pipeline.py
```

覆盖：

```text
时间窗口和HOLD排除区间等价
临近工况映射和聚合等价
Pickle-only初次/增量继承
v001→v002工况合并后历史episode和PKL对齐
```

## 在线稳定性实现

```text
第一模块stable_condition_label唯一工况入口
目标死区优先HOLD
目标变化缓变和切换暂停
普通动作初始最大SMALL，完成响应后才允许逐级升级
实际执行反馈驱动WAITING_EFFECT/EVALUATING_EFFECT
最小动作间隔、反向锁、每小时动作次数限制
FAST最小保持、连续退出确认和恢复期
排放WARNING/EMERGENCY分级
pH保护带、阀门边界、手动/故障、供浆泵切换硬过滤
```

## 尚未替代的现场环节

在线模块不会直接下发阀门指令，也不会绕过 `MainControl.py`。以下内容仍需在现场主控中完成并联调：

```text
DCS通信和实时周期调度
自动/手动权限和设备联锁
最终阀位指令下发
实际执行确认
断线、测点质量码和通信超时
紧急工艺保护
```

规则基线和配置示例值必须先以“建议模式”回放现场历史/实时数据，再按厂确认。当前测试证明代码路径和状态机符合设计，不构成对未知设备故障或全部现场扰动下绝对稳定的保证。
