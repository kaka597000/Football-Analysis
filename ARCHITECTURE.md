# 活动预测架构

兼容入口 `runtime/predict_active.py` 保留 CLI、run 和原有公开辅助函数。

1. `prediction_engine.py`：注册表、工件哈希、当前/test路由与矩阵支持对齐。
2. `prediction_service.py`：研究旁路编排、执行审计及输出组装。
3. `prediction_diagnostics.py`：进球分布、选分研究、市场变化和证据报告。
4. `publication.py`：事务日志、幂等追加冻结和可恢复文件交付；兼容入口的 `main()` 调用它。

`research/match_analysis.py` 继续通过已拆分的证据校验、研究执行和渲染模块生成报告。

ML共享数学在 `ml_core.py`，各自特征缺失策略保留在版本适配器。模型工件与活动注册表在外部运行目录维护。ML源码参与工件身份校验，普通联赛与欧冠策略不能机械合并。旧版本目录保留为实际依赖，分类见 [模型与代码用途](models/README.md)。

诊断异常遵循各入口的明确错误契约。执行审计继续记录兼容入口及实际模型引擎身份。

## 盘口与复盘

The Odds API 和 InferSports 通过 `runtime/automation/market_identity.py` 核对独立赛程的主客队、球队 ID 和别名审核时间。各采集器仍负责自己的赛事等级、盘口格式和来源时间。赛前入口 `market_bridge.py` 读取可追溯到预测截止前的报价，研究重放在通道和单场快照上均带标记。

`runtime/v5/paired_review.py` 按冻结比赛身份、模型版本和预测项目结算。欧国联的定时复盘校验原始赛果和预测后，用同一份完成记录响应相同输入。赛果证据或复盘实现有更新时写入新版本，原冻结仍可追溯。

## 前向验证工件

`runtime/automation/prospective_bundle.py` 负责国内联赛和欧冠工件的导出、校验、路径恢复和冻结回执读取。`prospective_cycle.py` 保留原 CLI 和 Python 入口，负责注册、捕获、结果轮询和进程中断后的任务恢复。每次运行在对应状态目录的锁内完整校验一次工件。云端把这两个文件一同加载到协议锁定的推理版本上，运行回执记录两份文件的哈希。

治理入口：[模块状态、有限候选与跨仓接口](docs/model-governance.md)。
