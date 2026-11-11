# 日联杯数据与独立研究模型

2026-11-11按用户要求新增男子日本联赛杯（J.League Cup / YBC Levain Cup，`jpn.league_cup`），正式范围为20项。可以开展来源核验后的赛事研究；当前尚无通过验收的活动预测模型，不直接调用J1、天皇杯或百年构想联赛工件。

## 数据库

独立SQLite：`/workspace/WCPP/data/jleague-cup/jleague-cup-20261111.sqlite`。从旧库对应的缓存HTML重新复核212场（2023年73、2024年70、2025年69），保存45份来源、球队身份、阶段、回合及90分钟/加时/点球拆分。原统一库只读。

BetExplorer列表中明确普通完赛的数字比分才能直接认作90分钟；带AfE/AfP须从对应比赛页的半场增量、比赛状态和canonical URL重新核验。结果和阶段来自同一比赛链接、日期及主客原文，不沿用旧的training_eligible标记。来源路径、SHA和原观察时间保存；未知原始query_time和first-known保持未知，不使用文件复制时间回填。

2023年小组赛与淘汰赛、2024/25改制后各轮次、首回合与次回合分别保存。为防未知格式混训，目前进一步按edition隔离，没有根据年份自动宣布赛制兼容。来源的`1/64-finals`等轮次标签保留在原文；归一代码`round-of-128`等属于供应商括号命名，不代表该赛季实际有128支球队。

历史中立场均未知，库存保持NULL；研究模型使用显式缺失指示变量，不把未知当非中立场，也不声称已验证该场主场优势。来源清单尚未经过独立官方全量对照，状态为PARTIAL。对2026/27赛事须另外核查官方新赛季赛制、球队、真实开球时间、首/次回合及总比分。

## 研究工件

已实际训练4个统计研究工件：2023小组赛一对、2025供应商`1/64-finals`单场阶段一对。每对当前候选为学习截距的Poisson，test候选为正则化球队攻防Poisson；未知场地采用独立缺失通道。其余24个edition/阶段/回合队列因样本不足未训练，不用跨阶段拼样本达标。

按不同比赛日期切分约前75%拟合、后25%开发诊断，再分别生成全历史面向未来工件。数据不足的日期分组保留整组，不拆同日来凑样本。评估模型与全历史模型分开存储；后者包含留出标签，不能用它重载历史比赛冒称未见验证。历史first-known未知、样本很小、缺真实盘口和场地独立核验，所有工件均为 `TRAINED_RESEARCH_NOT_ACTIVITY_ROUTE`，不修改活动注册表。研究矩阵最大格子仅为统计参考，综合第一推荐仍须独立证据分析。

## 命令

在源码仓库根目录执行：

```bash
python -m runtime.jleague_cup.cli build --manifest /absolute/build-manifest.json --db /absolute/NEW.sqlite
python -m runtime.jleague_cup.cli stats --db /absolute/NEW.sqlite
python -m runtime.jleague_cup.cli train --db /absolute/NEW.sqlite --out /absolute/NEW-models
python -m runtime.jleague_cup.cli predict --artifact /absolute/current-artifact.json --fixture /absolute/fixture.json --out /absolute/NEW-output.json
```

数据库、模型目录和输出拒绝覆盖。manifest中`sources`须为真实缓存原文的raw_path/raw_sha256/source_url/query_time；仅支持核准的BetExplorer日联杯地址。研究fixture必须匹配工件的competition、cohort、era、stage、tie_format和leg，显式成年男子，不能含目标比分；日期必须在训练截止之后。程序不会将这种离线研究诊断称为已核验的赛前发布。

正式赛事分析继续按用户核心需求核查近期过程、伤停、真实盘口、赛制及双回合总比分，按当前/test/综合轨规则独立保存。缺少通过验收的数值工件时如实保留缺口，不能使用其他赛事模型补位。本次不新建定时任务、不发布环境、不宣称准确率提升。
