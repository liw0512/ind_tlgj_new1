# GUI V2 独立测试版

本目录是一个**独立于现有 `system/gui` 正式前端**的 PyQt5 Widgets 新前端。

目标是先完成新前端架构、真实数据适配和页面设计，再决定何时替换旧 `ExtSingleWindow`。

## 当前不会影响的内容

- 不修改 `Application.py`；
- 不修改 `system/gui/ExtSingleWindow.py`；
- 不修改旧 `.ui` / `SingleMainRootWindow.py`；
- 不改变 `condition_model`、`slurry_policy_model` 的算法逻辑；
- `GlobalDataAdapter` 只读取 `GLOBAL_DATA`，不会修改后端控制状态；
- 前端不再单独设置“工况模型”页面，必要工况信息按需显示在运行总览/供浆控制中。

## 数据模式

GUI V2 现在支持两种模式：

### 1. MOCK 模式

只测试界面，不启动现有数据库、模型和现场数据链路。

```bat
D:\anaconda\envs\py3921\python.exe -m system.gui_v2.demo_dashboard
```

### 2. LIVE 模式

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

Windows 当前开发环境运行：

```bat
D:\anaconda\envs\py3921\python.exe -m system.gui_v2.live_dashboard
```

如果已经激活环境，也可以执行：

```bash
python -m system.gui_v2.live_dashboard
```

> `live_dashboard.py` 目前与正式 `Application.py` 保持一致，使用当前仓库配置的 `DataClientMain + DataHandler + MokeSlaveClient`。它会连接现有数据库、模型目录和后台处理链，因此这些依赖未准备好时 LIVE 模式可能启动失败。后续现场切换真实 Modbus 客户端时，应与正式入口统一修改。

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
- 动作族：`slurry_policy_action_family`
- 动作方向：`slurry_policy_action_direction`
- 动作强度：`slurry_policy_action_magnitude`
- 推荐阀门增量：`slurry_policy_recommended_valve_deltas`
- 推荐后阀位：`slurry_policy_projected_valve_openings`
- 控制模式：`slurry_policy_control_mode`
- 决策状态：`slurry_policy_decision_status`
- 决策原因：`slurry_policy_reason_codes`
- 历史可靠性：`slurry_policy_historical_reliability`
- 历史安全得分：`slurry_policy_historical_safety_score`
- 历史方向一致率：`slurry_policy_historical_direction_consistency`
- 第二模块桥接有效性：`slurry_policy_integration_valid`

首页目前展示其中最关键的一部分；可靠性、安全得分、完整 reason_codes 等字段已经由适配器保留下来，下一步用于“供浆控制”详细页。

## 当前页面

- 运行总览：已支持 MOCK / LIVE；
- 实时监控：占位页；
- 供浆控制：占位页；
- 历史趋势：占位页；
- 报警信息：占位页；
- 系统配置：占位页。

## 架构约束

1. 页面只负责显示，不直接查询数据库或调用模型；
2. 后端数据统一由 Adapter 转成 UI 字段；
3. 所有页面继续使用 Qt Layout，不回到大量 `setGeometry()`；
4. 颜色、字体和边框继续集中在 `theme.py`；
5. Windows 开发和银河麒麟部署共用同一套 UI 代码，避免 Windows 专属 API 和字体依赖。
