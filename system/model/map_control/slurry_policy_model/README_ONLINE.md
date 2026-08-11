# 湿法脱硫供浆在线策略模块 V1.0

在线代码直接放在当前第二模块离线训练目录下：

```text
slurry_policy_model/
├─ _engine/                         # 现有离线公共引擎
├─ slurry_policy_online/            # 新增在线模块
│  ├─ online_slurry_policy.py       # 统一入口
│  ├─ policy_snapshot_loader.py     # 激活版本、哈希和版本握手
│  ├─ realtime_state_builder.py     # 与离线一致的状态编码
│  ├─ disturbance_monitor.py        # REGULAR / FAST_CHANGE / FAST_RECOVERY
│  ├─ target_control.py             # commanded/effective SO2目标
│  ├─ demand_analyzer.py            # 排放安全与目标需求分级
│  ├─ candidate_retriever.py        # TRANSIENT/LOCAL/NEIGHBOR/PRIOR/RULE
│  ├─ candidate_filter.py           # 方向、pH、设备、历史质量硬过滤
│  ├─ candidate_ranker.py           # 可解释字典序排序
│  ├─ valve_action_resolver.py      # 历史增量、限幅和阀位投影
│  ├─ decision_state_machine.py     # 执行反馈、等待响应、反向锁
│  └─ runtime_store.py              # 状态持久化与JSONL日志
├─ activate_policy_version.py       # 验证并激活v###版本
├─ slurry_policy_config.py          # 离线+在线统一配置
└─ ...                              # 现有初次/增量训练代码
```

## 1. 控制目标

`PLANT_CONFIG["outlet_so2_safe_range"] = [0, 35]` 是安全范围，不是控制目标。

默认目标配置在：

```python
ONLINE_POLICY_CONFIG["so2_control"]["default_target"] = 20.0
```

运行时 `MainControl` 或 DCS 传入的目标优先：

```python
decision = policy.evaluate(first_module_row, target=15.0)
```

在线模块同时维护：

```text
commanded_target：操作员/DCS设置值
effective_target：按最大变化率缓变后真正参与控制的目标
```

离线PKL仍然与具体目标解耦。

## 2. 激活离线策略版本

完成同版本的第一、第二模块训练后执行：

```bash
python activate_policy_version.py --version v006
```

脚本会验证：

```text
第一模块版本 = 第二模块版本
condition_snapshot.json哈希
manifest必要文件大小与SHA256
塔、pH、阀门和SO2安全结构
```

全部通过后才原子写入：

```text
F:\tlgj\files\slurry_policy_model_output\active_version.json
```

在线程序不会直接把目录中最大的版本自动上线。

## 3. 初始化

```python
from slurry_policy_online import OnlineSlurryPolicy

policy = OnlineSlurryPolicy()
```

启动时必须已有有效 `active_version.json`。

## 4. 每周期调用

第一模块输出为“原始实时数据 + 工况字段”，可以直接传入：

```python
first_module_row = {
    "date": "2026-08-03 15:30:00",
    "jzfh": 350.0,
    "yyq_SO2": 3200.0,
    "jyq_SO2": 24.3,
    "xstjy_PH": 5.10,
    "aptjy_PH": 6.00,
    "xst_FMKD1": 30.0,
    "xst_FMKD2": 31.0,
    "apt_FMKD": 25.0,

    "condition_snapshot_version": "v006",
    "raw_grid_id": "P12-S13",
    "raw_condition_label": "366",
    "stable_condition_label": "365",
    "condition_label": "365",
    "condition_stable": True,
    "condition_switch_state": "STABLE",
    "condition_valid": True,
    "state_key": "..."
}

decision = policy.evaluate(
    first_module_row,
    target=20.0,
    execution_context={
        "automatic_control_allowed": True,
        "manual_valves": [],
        "faulted_valves": [],
        "supply_pump_state_changing": False,
    },
)
```

也支持嵌套输入：

```python
policy.evaluate({
    "timestamp": "2026-08-03 15:30:00",
    "process": {...},
    "condition": {...},
    "target": {"outlet_so2_target": 20.0},
    "execution": {...},
})
```

## 5. MainControl执行反馈

非HOLD推荐不会自动进入响应等待。MainControl完成最终联锁和实际下发后必须回传：

```python
feedback = policy.record_execution({
    "decision_id": decision["decision_id"],
    "recommendation_accepted": True,
    "actual_action_executed": True,
    "actual_execution_time": "2026-08-03 15:30:05",
    "actual_action_id": decision["action_id"],
    "actual_action_family": decision["action_family"],
    "actual_action_direction": decision["action_direction"],
    "actual_action_magnitude": decision["action_magnitude"],
    "actual_valve_before": {
        "xst_v1": 30.0,
        "xst_v2": 31.0,
        "apt_v1": 25.0,
    },
    "actual_valve_after": {
        "xst_v1": 31.0,
        "xst_v2": 32.0,
        "apt_v1": 25.0,
    },
})
```

没有实际执行时回传：

```python
policy.record_execution({
    "decision_id": decision["decision_id"],
    "recommendation_accepted": False,
    "actual_action_executed": False,
    "actual_execution_time": "2026-08-03 15:30:05",
})
```

在线状态机只依据实际执行动作进入 `WAITING_EFFECT`，不会把未执行的推荐误当成历史动作。

## 6. 稳定机制

在线稳定性由多层规则共同保证：

```text
第一模块6点condition_label众数
目标死区内优先HOLD
目标变化采用effective_target缓变
工况切换后暂停普通经济动作
动作实际执行后等待迟延+响应窗口
最小动作间隔、每小时动作次数上限
普通反向动作锁
FAST最小保持、连续退出确认和恢复期
pH、阀位、手动/故障、供浆泵切换硬过滤
排放WARNING/EMERGENCY优先于经济目标
```

普通候选顺序：

```text
LOCAL_CONDITION
→ NEIGHBOR_STATE
→ PLANT_ACTION_PRIOR
→ RULE_BASELINE
```

快变候选顺序：

```text
匹配disturbance_mode的TRANSIENT
→ RULE_BASELINE
```

默认不在FAST缺少历史档案时回退到普通本地策略。

## 7. 输出边界

在线模块输出：

```text
动作族
动作方向
动作强度
推荐阀门增量
投影阀位
历史可靠性/安全性/方向一致性
经验来源和原因码
```

`MainControl.py` 仍负责：

```text
最终设备联锁
最终阀位限幅
自动/手动权限
指令下发
执行结果回传
```

## 8. 日志和运行状态

默认目录：

```text
F:\tlgj\files\slurry_policy_online_runtime\
├─ online_runtime_state.json
├─ online_decisions.jsonl
└─ online_executions.jsonl
```

运行状态文件保存上次实际动作、响应等待时间、目标缓变状态、FAST状态和动作次数。程序重启后不会立即忘记尚未结束的动作等待。

## 9. 测试

```bash
python -m unittest tests.test_online_policy tests.test_online_stability -v
```

离线回归测试：

```bash
python tests/test_performance_equivalence.py
python tests/test_neighbor_optimization_equivalence.py
python tests/test_pickle_only_pipeline.py
python tests/test_version_alignment_pipeline.py
```
