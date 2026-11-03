# 活动预测架构

兼容入口 `runtime/predict_active.py` 保留 CLI、run 和原有公开辅助函数。

1. `prediction_engine.py`：注册表、工件哈希、当前/test路由与矩阵支持对齐。
2. `prediction_service.py`：研究旁路编排、执行审计及输出组装。
3. `prediction_diagnostics.py`：进球分布、选分研究、市场变化和证据报告。
4. `publication.py`：事务日志、幂等追加冻结和可恢复文件交付；兼容入口的 `main()` 调用它。

`research/match_analysis.py` 继续通过已拆分的证据校验、研究执行和渲染模块生成报告。

ML共享数学在 `ml_core.py`，各自特征缺失策略保留在版本适配器。本阶段不迁移模型文件、不改活动注册表。ML源码参与工件身份校验，普通联赛与欧冠策略不能机械合并。旧版本目录保留为实际依赖；experiments/legacy目录迁移仍待后续实施。

当前诊断模块的异常策略保持原行为；本次没有统一加入吞异常降级。执行审计继续记录兼容入口及实际模型引擎身份。
