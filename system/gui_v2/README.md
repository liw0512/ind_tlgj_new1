# GUI V2 独立测试版

本目录是一个**完全独立于现有 `system/gui` 正式前端**的 PyQt5 Widgets 原型。

目的只有一个：先验证新的前端架构、布局方式和视觉层级，再决定是否逐步替换旧前端。

## 当前不会影响的内容

- 不修改 `Application.py`；
- 不修改 `system/gui/ExtSingleWindow.py`；
- 不修改旧 `.ui` / `SingleMainRootWindow.py`；
- 不调用 DCS；
- 不调用 `condition_model` 或 `slurry_policy_model`；
- 当前首页数据来自 `MockDataSource`，只用于演示刷新。

## 运行

在仓库根目录执行：

```bash
python -m system.gui_v2.demo_dashboard
```

当前依赖沿用仓库已有 PyQt5 环境，不需要安装 PySide6。

## 这个版本重点测试什么

1. **Layout 响应式布局**：不再用大量 `setGeometry()` 固定坐标；
2. **左侧导航 + QStackedWidget**：页面可以独立拆分；
3. **组件化**：MetricCard、TowerCard、ActionCard、StatusPill、TrendWidget 可以复用；
4. **UI 与数据源分离**：页面只接收字典并显示，不在按钮事件里运行模型；
5. **设计 Token**：颜色、边框、文本层级集中在 `theme.py`；
6. **后续可平滑接入现有 `GLOBAL_DATA`**。

## 当前页面

- 运行总览：已经做成可刷新的原型；
- 实时监控：占位页；
- 供浆控制：占位页；
- 历史趋势：占位页；
- 报警信息：占位页；
- 系统配置：占位页。

> 前端不再单独设置“工况模型”页面。`condition_model` 仍作为后端算法模块保留，其必要状态只在运行总览、供浆控制或诊断信息中按需展示。

## 下一步接真实数据时的建议

保留页面接口：

```python
page.update_data(data)
```

新增一个 `GlobalDataAdapter(QObject)`，负责从现有 `GLOBAL_DATA` 安全读取最新：

```text
原始实时测点
+ map_control
+ condition_model 输出
+ slurry_policy_model 输出
```

再通过 Qt signal 发给 UI。这样旧后端线程和新 UI 不需要互相耦合。
