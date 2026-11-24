# 亚冠精英 League Stage 兼容验收

本轮只更新原草稿PR #51。既有正式cohort `afc.champions:ELITE:league_phase`、阶段 `league_phase` 保持；ESPN原生league-stage/League Stage与原有league-phase/League Phase为该现代路由的明确别名。不任意截断前缀，不扩展资格赛、旧制或淘汰赛。

共享现代阶段规范化复用欧冠分支：严格匹配cohort、年份整数且>=2024、每个已提供的slug/name；同场scoreboard/summary两侧均须符合阶段，year/type如同时存在必须一致，summary提供的league.slug不得冲突。type用于同场一致性，不硬编码12868；13662后季以合成测试覆盖。

## 真实留存配对

数据提交 `69821e4cb6db23ac51cc81159ed4a1a0058ca39e`，目录 `workspace-updates/20261104-data-supplement`。读取manifest、summary-manifest原捕获时间和SHA；原响应不改。scoreboard文件 `afc.champions-2025.json` Git blob为 `730f8e914b33a9f3a89a06cdddcb14191bf085a1`，SHA256为 `ff80b99a972aaa42ed9c79d44040fa452f1749811941a69c7348ece1b35bec07`。

| 实证样本 | 原阶段 | 旧pin及修复前草稿 | 修复后 |
|---|---|---|---|
| 719137 Al Ain–Al Rayyan | year2024/type12868/league-stage；summary League Stage无slug | phase or era not compatible | 原路由league_phase，90分钟1–2 |
| 733182 | year2024/type12867/round-of-16 | 阶段拒绝 | 仍阶段拒绝 |

summary SHA分别为 `aee9abd9a88663d51bb2b92935d78f9e58e37412652ea915f2b6c5dbb40353e7`、`2a552084efb9d59cb4ad65a3579c933894068adafe5cac5752b5bc855cfb7711`。733182前两节合计2–0、含加时最终3–0；本次不纳入允许阶段，不结算或训练该场。

旧固定源码 `6639867d502a2dfe53a9a235c8761f8ee6b91533` 与修复前草稿 `6ef6e417fef6a8d3ccdcf37d7f8d3713cc22f8b4` 均实际重放确认拒绝。修复后的719137通过regulation_row及certify_retained；临时副本只放现有scopes、单条新规范化行和原响应，trainer._load接受1条/隔离0/排除0，没有调用train或fit。一个合格输入不代表达到训练样本门槛。

欧冠真实401915311再次通过：保持NEW_FORMAT:league_phase，90分钟2–0，共享认证及训练只读加载1条均通过。

## 复现及限制

本地脚本 `/tmp/afc-phase-validation/replay.py RUNTIME_DIRECTORY`；pin-before.log、draft-before.log、after.log分别保存前后输出。脚本读取真实manifest及响应，不请求赛事API，仅临时目录输出标准化行。欧冠复现 `/tmp/ucl-preview.py`。

针对回归：`python -m unittest discover -s runtime/automation -p test_collector.py -v`，16项，其中新增2项，覆盖多season type、来源/时代冲突、旧制、资格赛、淘汰赛与未知路由。真实淘汰赛反例之外的冲突及13662后季属于合成测试，不能冒充留存实证。完整资产测试命令 `python scripts/run_tests.py --layer integration`；精确HEAD结果与远端CI另在PR追加。

没有新采集、训练任务、部署、数据主分支或定时pin修改。历史冻结、正式权重、英超RSI不变；不代表全部亚冠赛事或全20项已完成生产验收。
