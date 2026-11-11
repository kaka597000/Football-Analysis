# 当前状态（2026-11-11）

本页统一维护当前入口；项目目标和执行规范分别以 [project-goals.json](project-goals.json)、[AGENTS.md](AGENTS.md) 为准。

- 默认预测入口：`runtime/predict_active.py`；当前/test独立，综合判断另轨追加冻结。
- 本次现场注册表：`/workspace/WCPP/runtime/active-model.json`，版本 `WCPP-DUAL-4`，19个历史路由。注册表登记不保证每个模型当前可加载或通过验收；其他环境的21路由快照不代表本环境。
- 新分析范围：配置 [analysis-scope.json](configs/analysis-scope.json) 中20项赛事。范围准入、注册表路由、研究候选和正式验收是不同状态，不用单个“支持数量”替代。
- 日职联：加载与依赖问题见[专项核查](docs/j1-nations-model-readiness-20261111.md)；不能把注册表有条目等同于可用。
- 欧国联：已有分组[研究模型](docs/nations-league-model.md)，尚未通过活动模型验收。
- 日联杯：已有[八强首、次回合研究候选](docs/jleague-cup-quarterfinal-research.md)，尚未通过活动模型验收，不借用J1模型。

[模型与代码用途](models/README.md) · [架构](ARCHITECTURE.md) · [运行与验证](OPERATIONS.md)

历史评估、旧模型快照和冻结预测保留用于复现。文档整理不代表重新训练、模型晋级或准确率改善。
