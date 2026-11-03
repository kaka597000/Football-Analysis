# 单场分析代码分层

`runtime/research/match_analysis.py` 保留 `build`、`render`、`aware`、`evidence_block` 和 `MODULES` 的原有导入接口。调用方无需迁移。

- `match_evidence.py`：校验赛事身份、来源时间、敏感字段，复制合格证据。
- `match_research.py`：汇总研究执行状态，检查并执行显式请求的分钟模拟。
- `match_report.py`：将已生成的报告渲染为 Markdown，无模型执行。
- `match_analysis.py`：组装模块、模型训练覆盖、阵容情景及最终报告。

本次是职责拆分，输出 schema、缺项处理、异常拒绝规则和预测参数保持原语义。没有训练或晋级模型。
验证包含原版与新版四种场景（缺证据、有效证据、无效证据、非法模拟请求）的 JSON/Markdown 等价检查，以及输入不变和输出副本隔离检查。
