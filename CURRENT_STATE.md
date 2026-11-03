# 当前状态（2026-11-03）

本文件是当前状态导航入口；项目目标和用户执行规范仍分别以 project-goals.json、AGENTS.md 为准。

现场注册表版本：`WCPP-DUAL-4`，实际登记 **19** 个队列。工件实测身份见 [重构基线](manifests/runtime-assets-refactor-20261103.json)，不能把其他环境的21队列/42工件快照当作本环境状态。

默认预测入口为 `runtime/predict_active.py`。当前/test独立，综合判断另轨冻结。源码重构不代表训练、晋级或命中率改善。

架构见 [ARCHITECTURE.md](ARCHITECTURE.md)，运行见 [OPERATIONS.md](OPERATIONS.md)。旧文档中的入口和模型清单为历史状态，使用前必须与实际注册表核对。

正式新增分析范围已统一为[18项赛事白名单](docs/analysis-scope-18.md)，配置见 configs/analysis-scope.json；14项有活动路由、4项明确缺口。注册表外观仍保留历史19队列。
