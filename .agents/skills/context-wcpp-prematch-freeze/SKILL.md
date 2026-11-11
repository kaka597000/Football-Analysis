---
name: context-wcpp-prematch-freeze
description: 在WCPP项目进行足球赛前分析、独立双模型调用、综合判断和发布前冻结时使用。
---

适用于本仓库及其WCPP运行资产的维护者和分析代理。只处理赛前分析；已开场比赛转滚球或复盘流程。权威定义和验收门槛继续由项目文档维护，本技能负责执行顺序。

1. 先读仓库 `AGENTS.md`、运行目录 `AGENTS.md`、[用户核心需求](../../../docs/user-primary-score-requirements.md)、[冻结规则](../../../docs/analysis-freeze-policy.md)和[比分审计](../../../docs/score-distribution-adjustment.md)。使用[项目目标](../../../docs/project-goals.md)中的正式指标，不另行定义Top1或TOP3。
2. 按[API优先采集](../../../docs/api-first-collection.md)安排 gpt-6-luna 收集资料；给出赛事/日期/所需字段、共享请求预算和输出目录。主代理复核赛事代码、成年男子、年代/赛季、阶段/等级/回合、双方ID、场地、开球与90分钟口径。保留原响应、URL、真实查询/报价时点、哈希及缺项。中国体彩及镜像禁访，密钥只读环境。
3. 读取实际活动注册表及工件，验证哈希、训练截止、球队ID命名空间和适用范围。旧工件、研究候选和活动路由分别说明；日职联、日联杯及百年构想联赛不得互换。欧国联按等级和阶段隔离。没有适用模型则记录明确拒绝，禁止借其他模型或虚构矩阵。
4. 使用同场同输入运行 `python runtime/predict_active.py FIXTURE.json --out NEW_PAIR.json`。此CLI会写冻结；仅限已经核验的真实未来比赛，软件合成测试不得使用此发布入口。调用失败时保存错误和双模型不可用状态，按冻结规则保留覆盖分母。
5. 分别审查双方进球路径、近期状态/过程、人员、赛制、全赛事负荷及反证，最后核实市场。遵守[市场与阵容规则](../../../docs/market-and-lineup-policy.md)：没有正式首发不自动阻断；重大未解决人员风险保留待定。没有矩阵不编概率，单场射门不改称xG，不能仅把矩阵最大格子改名为综合第一比分。
6. 综合输入封装为 `fixture` 和 `payload`，引用真实双模型冻结回执，保存原文、候选比较、反证、来源、版本、截止和缺项。运行 `python runtime/research/analysis_freeze.py NEW_ANALYSIS.json --stage prematch --track comprehensive`。TOP3使用同模块 `--track top3-selection`，只纳入实际合格记录，不足不凑；名单可以为空。
7. 发布前读回回执并验证哈希，显示冻结ID和北京时间。后续修订追加，不覆盖首次记录。按[版本管理](../../../docs/version-management.md)推送和核验远端；按[存储分工](../../../docs/football-storage-and-skills.md)准备报告副本。冻结不意味着模型晋级。
