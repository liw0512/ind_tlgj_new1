# 正式前端

本目录是湿法脱硫智能控制系统当前正式 PyQt5 Widgets 前端。

`Application.py` 直接启动本目录的 LIVE 界面。

## 正式入口

```powershell
D:\anaconda\envs\py3921\python.exe Application.py
```

正式入口启动 `DataClientMain + DataHandler + MokeSlaveClient` 后台链路，并由
`GlobalDataAdapter` 只读适配 `GLOBAL_DATA` 到各个页面。前端不直接调用模型，
也不绕过后端执行控制。

## 数据模式

前端同时保留两种运行模式：

### 1. MOCK 演示模式

只测试界面，不启动现有数据库、模型和现场数据链路。

```bat
D:\anaconda\envs\py3921\python.exe -m system.gui.demo_dashboard
```

### 2. LIVE 正式模式

启动当前仓库已有的后台链路，并由 `GlobalDataAdapter` 读取：

```text
GLOBAL_DATA["data"][-1]
        +
GLOBAL_DATA["map_control"]
        ↓
GlobalDataAdapter
        ↓ Qt signal
OverviewPage.update_data()
```

也可以直接运行正式前端模块：

```bat
D:\anaconda\envs\py3921\python.exe -m system.gui.live_dashboard
```

如果已经激活环境，也可以执行：

```bash
python -m system.gui.live_dashboard
```

> LIVE 模式会连接现有数据库、模型目录和后台处理链。当前现场客户端仍为 `MokeSlaveClient`；切换真实 Modbus 客户端时只需修改正式后台启动函数。

## 首页已经接入的真实字段

`GlobalDataAdapter` 优先读取 `GLOBAL_DATA["map_control"]`，缺失时再从最新原始帧兜底。

### 实时过程量

- 原烟气 SO₂：`yyq_SO2`
- 净烟气 SO₂：`jyq_SO2`
- 吸收塔 pH：`xstjy_PH`
- 供浆阀位：优先 `xst_FMKD`，兼容旧字段 `xst_FMKD1`
- 供浆流量：`xstshsjy_LL`
- 供浆泵频率：`xstshsjy_APL`、`xstshsjy_BPL`
- 旧现场供浆泵电流兼容：`xstgjb_ADL`、`xstgjb_BDL`

### 第一模块 / 集成版本

- 当前工况：优先 `stable_condition_label`，否则 `condition_label`
- 工况稳定：`condition_stable`
- 工况切换状态：`condition_switch_state`
- 集成模型版本：`integrated_active_version`

### 第二模块在线结果

- 当前有效目标：`slurry_policy_effective_target`
- 经验来源：`slurry_policy_experience_source`
- 动作对象：`slurry_policy_action_family`（塔级供浆流量）
- 供浆方向：`slurry_policy_action_direction`
- 流量形态：`slurry_policy_action_magnitude`（STEP / PULSE / BOOST_STEP）
- 目标供浆流量：`slurry_policy_target_supply_flow`
- 执行预览：`slurry_policy_target_flow_execution_preview`
- 控制模式：`slurry_policy_control_mode`
- 决策状态：`slurry_policy_decision_status`
- 决策原因：`slurry_policy_reason_codes`
- 历史可靠性：`slurry_policy_historical_reliability`
- 历史安全得分：`slurry_policy_historical_safety_score`
- 历史方向一致率：`slurry_policy_historical_direction_consistency`
- 第二模块桥接有效性：`slurry_policy_integration_valid`

“供浆控制”页展示当前流量、动作形态、目标峰值和目标最终流量。阀位只属于实时过程监测，不是第二模块的推荐动作或回退输出。

## 当前页面

- 运行总览：支持 MOCK / LIVE；
- 实时监控：展示现场过程量；
- 供浆控制：展示目标供浆流量与执行状态；
- 历史趋势：展示过程量与供浆动作历史；
- 报警信息：展示当前告警；
- 系统配置：展示运行配置。

## 架构约束

1. 页面只负责显示，不直接查询数据库或调用模型；
2. 后端数据统一由 Adapter 转成 UI 字段；
3. 所有页面继续使用 Qt Layout，不回到大量 `setGeometry()`；
4. 颜色、字体和边框继续集中在 `theme.py`；
5. Windows 开发和银河麒麟部署共用同一套 UI 代码，避免 Windows 专属 API 和字体依赖。
