---
name: context-wcpp-review-walk-forward
description: 在WCPP项目结算已冻结足球预测、复盘失误或设计滚动时间验证时使用。
---

适用于本仓库及WCPP运行资产的维护者和分析代理。处理已存在的冻结记录及独立研究验证，不重建不存在的历史赛前预测。

1. 读取 `AGENTS.md`、[用户核心需求](../../../docs/user-primary-score-requirements.md)、[冻结规则](../../../docs/analysis-freeze-policy.md)、[自主复盘调整](../../../docs/autonomous-model-review-policy.md)和[项目目标](../../../docs/project-goals.md)。指标、首次版本选择和结算规则以这些文档及现有实现为准，不在电子表格重定义。
2. 先锁定本批完整赛事名单、每场各轨首次赛前冻结ID、版本及哈希，再读取赛果。安排 gpt-6-luna 核查来源和90分钟/加时/点球，主代理检查球队、比赛身份及状态。延期、未完赛、待定和失败不得从覆盖分母消失。
3. 复用 `runtime/v5/paired_review.py` 与 `runtime/research/review_events.py`；先检查其实际函数/命令契约再调用，不猜参数。当前、test、人工综合和独立候选各自统计，test不同版本分列；原TOP3赛事名单单列且不替代全量。不要把每场三个比分的Top3当成推荐三场赛事。
4. 按权威规则分别报告比分、胜平负、实际盘口大小球及覆盖率；实际线缺失不自设2.5。大小球保留全赢/半赢/走盘/半输/全输。原先待定不得事后挑候选计命中；乌龙受益队与球员所属队分清。
5. 诊断机会生成、转化/扑救、人员和比赛状态；赛后xG只能用于复盘，不回填赛前输入。先区别程序错误、数据问题与统计误差，再记录保持、补数据、修复或独立实验的决定。
6. 滚动验证先登记样本、日期切分、配方、基线、评估指标和停止/验收条件，再执行。拟合、调参、校准、最终留出分离；特征必须在目标赛前可知，同场不同轨使用相同来源和盘口。没有历史first-known只能称结果日期隔离的开发诊断，不能认证as-of回测；已用于调参的样本不再算未见验证。
7. OOD联赛遵守[独立研究边界](../../../docs/asia-leagues-ood-research-tracks.md)，欧国联遵守[分级验收协议](../../../docs/nations-league-model.md)。同场净增命中、覆盖率和不确定性共同报告；达到样本数不自动晋级，也不以概率指标替代Primary Top1。
8. 使用 `runtime/research/analysis_freeze.py` 的 `--stage review` 追加保存复盘原文、证据和调整决定。按[版本管理](../../../docs/version-management.md)同步。Python产出指标，Sheets只展示带冻结ID、版本、分母和数据截止的副本。
