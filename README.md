# 脱硫供浆智能控制系统

## 1. 项目简介

本项目面向湿法烟气脱硫供浆控制场景，利用历史数据和实时过程测点完成工况识别、供浆动作学习、安全约束判断及供浆阀门推荐。

系统核心由两个模型组成：

1. `condition_model`：根据配置的工况轴划分基础网格，生成稳定、可版本化的工况标签；
2. `slurry_policy_model`：从历史供浆阀动作及其 SO2、pH 响应中学习供浆经验，在线输出阀门调整建议。

系统支持单塔或双塔、每塔任意数量的供浆阀，以及供浆泵到阀门的可用路径配置。第二模块只生成推荐结果，不直接写 DCS；最终执行仍由上层控制、联锁和现场条件决定。

## 2. 系统流程

```text
历史/实时过程数据
  → DataPreprocessor 数据清洗与字段透传
  → condition_model 工况识别与稳定
  → slurry_policy_model 供浆候选检索
  → SO2 目标、pH、阀位和供浆泵可用性校验
  → 推荐阀门增量与投影阀位
  → Process4MapControl 写库、前端及 DCS 接口
```

离线训练链路：

```text
原始训练 CSV
  → condition_model 生成带 condition_label 的 CSV 和工况快照
  → slurry_policy_model 学习 ACTION/HOLD 历史响应
  → 校验两模块版本及快照哈希
  → 原子更新 active_version.json
```

## 3. 目录结构

```text
Application.py
system/
  base/                         数据预处理和公共基础能力
  data_opts/                    数据采集、通信和处理
  gui/                          前端界面
  model/
    Process4MapControl.py       供浆模型运行、训练和持久化集成
    config/
      plant_config.py           厂级工况轴、塔、阀门和供浆泵配置
      standard_fields.py        标准过程字段
      process4map_config.py     运行、训练、校验和写库配置
      slurry_core_bridge_config.py
                               两模块脚本、快照和版本路径
    map_control/
      condition_model/          第一模块：工况划分与在线识别
      slurry_policy_model/      第二模块：供浆动作学习与在线策略
      model_csv/                训练流程的工作 CSV
files/
  slurry_policy_model_output/   策略快照和 active_version.json
```

## 4. 快速启动

### 4.1 安装依赖

建议使用 Python 3.9 环境：

```powershell
python -m pip install -r requirements.txt
```

### 4.2 启动系统

Windows：

```powershell
python Application.py
```

Linux：

```bash
./startup.sh
```

`Application.py` 中的 `ENABLE_FRONTEND` 控制前端是否启用。关闭前端时，数据采集、处理和模型线程仍可独立运行。

## 5. 厂级供浆配置

换厂或调整现场结构时，统一修改：

```text
system/model/config/plant_config.py
```

该文件是现场物理结构的唯一配置源，主要包括：

- 净烟气 SO2 安全范围；
- 1～2 个工况轴及其范围、步长；
- 单塔或双塔启用状态；
- 各塔 pH 字段、安全范围和保护带；
- 每塔供浆阀字段、开度范围和动作阈值；
- 定频供浆泵电流阈值及 `pump → valve` 拓扑。

供浆泵运行状态按以下规则判断：

```text
current > run_current_threshold → 运行
current <= run_current_threshold → 停止
字段缺失或无效                  → 停止（fail-safe）
```

一个阀只要存在至少一台运行中的服务泵，即认为该供浆路径可用；全部服务泵停止时，对应供浆动作不可执行。

## 6. 算法与运行配置

| 配置文件 | 作用 |
|---|---|
| `system/model/config/plant_config.py` | 厂级工况轴、塔、pH、供浆阀和供浆泵拓扑 |
| `system/model/map_control/condition_model/condition_config.py` | 工况成熟度、自动合并阈值和在线稳定窗口 |
| `system/model/map_control/slurry_policy_model/slurry_policy_config.py` | 响应窗口、动作强度、可靠性、安全过滤和在线限幅 |
| `system/model/config/process4map_config.py` | 数据校验、停机判定、线程队列、训练周期和数据库写入 |
| `system/model/config/slurry_core_bridge_config.py` | 两模块训练入口、工作 CSV、快照目录和活动版本文件 |

标准过程字段统一定义在 `system/model/config/standard_fields.py`，不要在各模块重复建立现场字段映射。

## 7. 第一模块：工况识别

`condition_model` 将连续过程数据映射为稳定工况标签。当前支持 1 个或 2 个工况轴，pH 只用于工况解释统计，不参与基础网格坐标。

主要输出：

```text
condition_snapshot_version
grid_id
condition_label
policy_region_id
state_key
condition_valid
condition_stable
condition_switch_state
condition_reason
```

详细说明见 `system/model/map_control/condition_model/README.md`。

## 8. 第二模块：供浆策略

`slurry_policy_model` 从历史供浆阀的 ACTION/HOLD 片段中学习动作对净烟气 SO2 和塔 pH 的响应。

动作语义包括：

```text
方向：INCREASE / DECREASE / HOLD
强度：MICRO / SMALL / MEDIUM / STRONG
```

在线阶段依次进行经验检索、目标方向判断、pH 安全判断、阀位限制、供浆泵路径校验和状态机约束，最终将塔级动作映射为具体阀门增量。

主要输出统一使用 `slurry_policy_` 前缀：

```text
slurry_policy_decision_status
slurry_policy_control_mode
slurry_policy_action_family
slurry_policy_action_direction
slurry_policy_action_magnitude
slurry_policy_recommended_valve_deltas
slurry_policy_projected_valve_openings
slurry_policy_reason_codes
```

详细说明见 `system/model/map_control/slurry_policy_model/README.md`。

## 9. 初次训练与增量训练

自动训练的数据源、数据量和触发周期配置在：

```text
system/model/config/process4map_config.py
```

初次训练：

```text
Initial_train.csv
  → Initial_train_after_condition.csv
  → condition snapshot v001
  → slurry policy snapshot v001
  → active_version.json
```

增量训练：

```text
Update_train.csv + 当前 active vN
  → Incremental_train_after_condition.csv
  → condition/policy vN+1
  → 同版本校验
  → 原子激活 vN+1
```

增量训练失败不会覆盖当前活动版本。工况轴、塔、阀门或供浆泵拓扑发生结构变化时，应重新执行初次训练，不应继续沿用旧快照。

## 10. 历史数据在线回放

训练并激活同版本模型后，可使用历史 CSV 验证完整在线判定链：

```powershell
python system/model/map_control/condition_model/online_condition_classifier.py --snapshot active --input <历史数据.csv> --output <回放结果.csv> --target 20
```

如目标值由 CSV 每行提供：

```powershell
python system/model/map_control/condition_model/online_condition_classifier.py --snapshot active --input <历史数据.csv> --output <回放结果.csv> --target-column outlet_so2_target
```

只检查第一模块时增加 `--condition-only`。回放不会写 DCS，也不会把推荐动作自动视为已执行。

## 11. 在线执行反馈

在线 Pipeline 给出供浆推荐后，实际执行结果由上层回传：

```python
pipeline.record_execution(feedback)
```

只有真实执行反馈才会推动 `WAITING_EFFECT`、动作间隔和反向锁等状态，避免将未执行的推荐误认为现场动作。

## 12. 版本管理

线上只加载同一版本的工况模型和供浆策略模型：

```text
condition vN + slurry policy vN
```

活动版本入口为：

```text
files/slurry_policy_model_output/active_version.json
```

版本激活时会校验模型版本、工况快照哈希、工况映射及厂级塔/阀门/供浆泵拓扑，避免不同版本模型混用。

## 13. 代码仓库

```text
http://192.168.8.10:8000/huiyun/desulfurization/ind_tlgj.git
```
