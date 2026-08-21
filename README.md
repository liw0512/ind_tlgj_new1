# 脱硫供浆智能控制系统

## 1. 系统目标

本项目面向湿法烟气脱硫系统，使用历史数据和实时测点完成：

1. 工况离线划分、增量更新与在线识别；
2. 识别实际供浆流量中的阶跃、脉冲和强化阶跃动作；
3. 学习不同工况下供浆动作对净烟气 SO₂、吸收塔 pH 的影响和响应时间；
4. 在线给出目标供浆流量、动作形态和执行阶段建议；
5. 在工况切换、数据异常、pH 越界和排放风险下执行保持或阻断。

第二模块的控制对象是**供浆流量**，不是供浆阀位。阀位、电流和泵频率可以作为实时监测量，但不属于第二模块的推荐动作。

## 2. 当前主流程

```text
1 秒实时数据
  → 每 10 秒形成一次模型快照
  → 数值测点取当前时刻及前 2 秒均值
  → 状态、字符串和 0/1 字段取当前时刻值
  → 第一模块在线工况识别
  → SO₂目标偏差与安全状态判断
  → 第二模块匹配历史供浆流量动作原型
  → 输出目标供浆流量、动作形态和执行阶段
  → 写入历史库并展示到前端
```

模型推理、状态判断和模型结果写库均按 10 秒周期运行。项目不会把原始 1 秒 CSV 预先改写成 10 秒 CSV。

程序入口：

```powershell
D:\anaconda\envs\py3921\python.exe Application.py
```

## 3. 两个模型模块

### 3.1 第一模块：工况模型

目录：`system/model/map_control/condition_model/`

职责：

- 从历史数据建立工况网格和工况标签；
- 增量训练时更新工况快照；
- 在线输出当前工况、稳定工况和切换状态；
- 与第二模块使用相同的 `v###` 版本原子激活。

### 3.2 第二模块：供浆流量策略

目录：`system/model/map_control/slurry_policy_model/`

第二模块从**实际供浆流量曲线**提取动作，不再把阀位变化作为控制动作：

| 动作形态 | 含义 |
|---|---|
| `STEP` | 流量变化后保持在新的稳定平台 |
| `PULSE` | 流量短时升高或降低，随后回到原平台 |
| `BOOST_STEP` | 先进入临时强化平台，再稳定到新的最终平台 |

每条有效供浆动作会记录吸收塔、工况、方向、峰值和最终流量、持续时间、累计供浆量、SO₂/pH 响应、安全性、响应延迟与稳定时间。

离线训练按“工况 + 吸收塔 + 动作方向 + 动作形态 + 执行形态”生成供浆流量原型。在线控制只从满足可靠性、安全性和方向一致性门槛的原型中选择建议。

## 4. 初次训练与增量训练

自动训练入口位于 `system/model/Process4MapControl.py`，数据源配置位于 `system/model/config/process4map_config.py`。

初次训练与增量训练均支持数据库或 CSV 数据源：

```python
initial_data_source = "database"       # 或 "csv"
incremental_data_source = "database"   # 或 "csv"
```

统一训练链路：

```text
数据库或原始 CSV
  → 统一训练工作 CSV
  → 第一模块生成同版本工况快照并标注数据
  → 第二模块检测供浆流量动作
  → 分类 STEP / PULSE / BOOST_STEP
  → 筛选 CLEAN 且响应完整的动作证据
  → 生成供浆流量原型
  → 第一、第二模块同版本校验与原子激活
```

增量训练会继承上一版本的历史动作证据，与新增证据合并、去重后重新计算原型。默认不会自动激活未经检查的新版本。

第二模块独立训练入口：

```powershell
# 初次训练
D:\anaconda\envs\py3921\python.exe system/model/map_control/slurry_policy_model/initial_slurry_policy_trainer.py --input <第一模块标注CSV>

# 增量训练
D:\anaconda\envs\py3921\python.exe system/model/map_control/slurry_policy_model/incremental_slurry_policy_trainer.py --input <新增标注CSV>
```

## 5. 在线推荐含义

第二模块的规范推荐类型为 `TARGET_SUPPLY_FLOW`，结果包含：

- 当前供浆流量；
- 目标峰值流量和目标最终流量；
- 峰值和最终目标允许范围；
- `STEP`、`PULSE` 或 `BOOST_STEP` 动作形态；
- 历史原型编号、可靠性和预期 SO₂/pH 效果；
- 当前执行阶段和阻断原因。

没有满足条件的历史流量原型时，系统输出 `HOLD`，不会回退生成阀位增量建议。

## 6. 执行安全边界

当前仓库内置的目标流量执行适配器为 `DRY_RUN`：

- 可以校验和展示目标流量执行阶段；
- 不包含 DCS 写操作；
- 不会把目标流量换算成阀位开度；
- 不会自动控制现场设备。

若要投入现场闭环，需要单独实现并验证现场目标流量执行适配器，同时保留工程量程、联锁、反馈确认、超时和回退保护。前端显示“目标流量建议”不代表已经向 DCS 发出命令。

## 7. 关键配置

| 配置文件 | 作用 |
|---|---|
| `system/model/config/process4map_config.py` | 10 秒调度、训练数据源、数据库、队列、停机与维护参数 |
| `system/model/config/plant_config.py` | 吸收塔、供浆流量测点、pH 测点、泵和监测阀门拓扑 |
| `system/model/map_control/condition_model/condition_config.py` | 第一模块工况训练与在线识别参数 |
| `system/model/map_control/slurry_policy_model/slurry_policy_config.py` | 供浆动作检测、响应窗口、可靠性和在线安全参数 |
| `system/model/map_control/fast_change_mode/fast_change_config.py` | 快变工况识别参数 |

相同业务阈值只保留一个事实来源。排放安全范围、塔 pH 安全范围和设备工程量程优先由中央厂级配置提供，第二模块不重复维护另一套数值。

## 8. 训练产物

第一、第二模块产物均按 `v###` 管理。第二模块主要产物包括：

```text
snapshots/v###/
  effective_config.json
  training_summary.json
  datasets/
  global/supply_flow_prototypes.json
  global/supply_flow_prototypes.pkl
```

在线只加载经过完整性校验并由 `active_version.json` 指向的第一、第二模块同版本产物。

## 9. 前端与历史数据

正式前端位于 `system/gui/`，由根目录 `Application.py` 直接启动。供浆控制页展示当前工况、SO₂、增加/减少/保持供浆、当前流量、目标峰值流量、目标最终流量、动作形态、历史原型依据、DRY_RUN 执行阶段和阻断原因。

供浆阀位、泵电流和泵频率仅作为设备监测信息显示，不作为第二模块的推荐动作。

数据库 JSON 字段用于保存目标流量原型、规范推荐和执行预览。旧月表通过非破坏性加列兼容，旧记录缺少新字段时前端按空对象处理。

## 10. 测试环境

项目统一使用 `D:\anaconda\envs\py3921\python.exe` 进行语法检查和回归测试。仓库不保留临时回归测试脚本；修改完成后应在本地完成验证再交付。
