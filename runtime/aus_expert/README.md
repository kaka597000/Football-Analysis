# 澳超独立研究专家

单一实现保存在本Git仓库，`/workspace/WCPP/runtime/aus_expert`仅为符号链接。复用既有optimization的函数与schema，不修改其源码或current/test登记。只支持澳超男子成年常规赛；finals、年龄/性别/赛事不匹配、新球队拒绝。输出为统计研究矩阵，不是综合第一推荐比分。

```bash
MPLCONFIGDIR=/tmp/aus-mpl python runtime/aus_expert/runner.py import --input /absolute/immutable/matches.jsonl
MPLCONFIGDIR=/tmp/aus-mpl python runtime/aus_expert/runner.py export --out /absolute/new-dataset
MPLCONFIGDIR=/tmp/aus-mpl python runtime/aus_expert/runner.py train --dataset /absolute/new-dataset --out /absolute/new-model-directory
MPLCONFIGDIR=/tmp/aus-mpl python runtime/aus_expert/runner.py predict --artifact /absolute/model/artifact.json --fixture /absolute/fixture.json --out /absolute/new-prediction.json
MPLCONFIGDIR=/tmp/aus-mpl python -m unittest discover -s runtime/aus_expert -p test_expert.py -v
```

在`/workspace/Football-Analysis`执行。训练/导出/预测输出均须使用新目录或新文件，禁止覆盖。入库审计文件存在时拒绝模糊重复运行。入库通过单事务，只新增源证据/球队ID/赛事与quarantine，旧matches逐字段保持；冲突只隔离。绝不调用会重建全库的Store.project。

fixture须含`match_id,cohort=aus.1,competition=aus.1,gender=male,age=senior,stage=regular,season,date(UTC),home,away,neutral=null,cutoff_at,kickoff_at`。home/away为已核实Canonical ESPN球队ID。UTC日期必须与kickoff_at相符，cutoff不能未来，必须真实尚未开球且工件早于cutoff。禁止目标赛果/xG等赛后字段。predict会在独立`aus_expert_research`轨追加冻结，包括ABSTAIN，不修改current/test也不自动晋级。

研究数据无历史first_known_at认证，始终RETROSPECTIVE_PRIOR_ONLY。xG缺失，不填造；ESPN过程原始证据归档但不冒充共享History所识别的football-data.co.uk，过程/伤停/战意/完整体能数值模块未训练。neutral未知保持None。旧队更名沿稳定providerID；已解散球队保持独立。

开发fit截止2021-07-01；选择仅用2021-07至2023-07。选型后，审计模型只重拟合2023-07前历史，固定该起点回放2023-07至2026-07。这是本轮操作留出回溯压力测试，长期状态冻结；不是未见未来业绩。全历史工件另外refit用于未来研究，其本身没有未见历史准确率。矩阵argmax不冒称用户综合Primary，70%/80%目标未证明。

四候选：常数Poisson、球队Poisson365/ridge20、相同core+独立train-only DC rho、相同core+固定OOF LightGBM残差。DC rho限制[-.03,.015]确保在率[.08,8]全域tau为正；PMF尾部按1e-13自适应格子（最少16）归一化，DC修正后再次归一化；并列argmax采用NumPy行优先第一个。区间是离散中央80%模型内区间，保存实际概率质量，不是保证80%实测覆盖。总球CRPS由矩阵反对角线求真实总球分布。

回滚仅在确认库没有其它新写入时使用`reports/aus-expert-20261016/canonical-before.sqlite`在线备份恢复；有并行后续写入时须按本轮import-audit逐新增ID撤回，不能整库覆盖新工作。不要自动执行回滚。备份、旧逐场快照、活动文件hash及导入增量审计均供人工核查。

最新可调用工件（固定配置数据修复，仅研究）：

```bash
MPLCONFIGDIR=/tmp/aus-mpl python runtime/aus_expert/runner.py predict \
  --artifact /workspace/WCPP/models/aus-expert-20261016-boundary-refit-v1/artifact.json \
  --fixture /absolute/new-fixture.json --out /absolute/new-prediction.json
```

权威描述：`/workspace/WCPP/reports/aus-expert-20261016/aus-expert-registry.json`。V1的2,806场与487场操作留出保留；跨年事件650536补库后最新工件仅固定Poisson配置以2,807条全历史refit，未重选或重算audit。


赛前综合分析还须遵守[澳超开放性分析要求](/workspace/Football-Analysis/docs/aus-league-context.md)。这是逐场证据审查，统计矩阵及现有工件不因联赛风格标签自动加球。
