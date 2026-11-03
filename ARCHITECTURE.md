# 活动预测架构

兼容入口 `runtime/predict_active.py` 保留 CLI、run 和原有公开辅助函数。

1. `prediction_engine.py`：注册表、工件哈希、当前/test路由与矩阵支持对齐。
2. `prediction_service.py`：研究旁路编排、执行审计及输出组装。
3. `prediction_diagnostics.py`：进球分布、选分研究、市场变化和证据报告。
4. 兼容入口的 `main()`：追加冻结及输出交付。

`research/match_analysis.py` 继续通过已拆分的证据校验、研究执行和渲染模块生成报告。

本阶段不迁移模型文件、不合并ML实现、不改活动注册表。ML源码参与工件身份校验，普通联赛与欧冠策略不能机械合并。旧版本目录保留为实际依赖；experiments/legacy目录迁移仍待后续实施。

当前诊断模块的异常策略保持原行为；本次没有统一加入吞异常降级。执行审计继续记录兼容入口及实际模型引擎身份。
