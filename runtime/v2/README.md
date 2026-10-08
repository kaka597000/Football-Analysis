# WCPP 云端候选0.2

这是独立Challenger，既不是恢复后的Champion V21.5，也不替换Selector4.1-RP1。Regime-MT-KL仍仅Shadow。永久λ、原预测、Canonical数据库和500+200登记均不修改。

## 已实现

- 公开历史采集、原始快照SHA256、去重、90分钟口径筛选；额外时间/点球完场状态排除。
- 联合攻防强度、对手强度校正、主场优势、中立场、365天时间衰减与岭收缩；未知球队在历史重建中使用并记录先验，未来请求默认拒绝，须显式接受冷启动先验。
- 独立Poisson、Dixon–Coles低比分修正、共享进球双变量Poisson。后两者的依赖参数在攻防拟合后估计，不宣称完整联合极大似然。
- 同一比分分布输出唯一Primary、胜平负和指定整数/半球/四分之一大小球的完整结算概率。
- 仅校准段拟合比分分布温度；仅选择段选择候选，最终测试段不参与选择。温度变换保持单比分排序，主要调整概率，不能单独提高Top1。
- 四段按日期拆分及固定配置的逐月滚动重建。指标包含Top1、比分log loss、胜平负准确率/Brier、固定2.5大小球准确率/Brier、校准分箱与冷启动数量。
- 阵容特征残差回归、官方来源/赛前时间校验、独立时间段验收；目前没有真实可用的历史特征，数值调整禁用。验证器当前仅支持Poisson温度1，其他分布不会误用系数。
- 去水市场概率、有界矩阵重排（alpha≤0.2且KL≤0.02），必须通过独立验证并有报价源时间。英超历史实验未通过重排收益门槛，其他赛事报价不足，未来全部禁用。
- 未来证据快照采集、未来赛前预测、不可覆盖的研究冻结；冻结需比赛身份和首发/可用人员/动机/完整性证据，不追认历史冻结，不计正式KPI。

## 环境与运行

Python3.12，依赖见requirements.txt。本环境已具备对应版本，无新增服务或环境变量要求。

```bash
cd runtime/v2
python -m unittest discover -s . -p 'test_*.py' -v
python collect.py
OPENBLAS_NUM_THREADS=1 python benchmark.py data/v2/EPL-history.json
OPENBLAS_NUM_THREADS=1 python walk_forward.py data/v2/EPL-history.json
```

collect.py刷新原始历史快照，重跑后须重建报告，不能把后来查询的资料当作旧赛前输入。每次需要保留独立赛前证据，snapshot使用新的目录：

```bash
python snapshot.py usa.1 EVENT_ID --directory data/v2/prospective/UNIQUE_SNAPSHOT_ID
python cli.py predict reports/model-upgrade-v2/usa.1-artifact.json data/v2/prospective/UNIQUE_SNAPSHOT_ID/fixture.json --out data/v2/prospective/UNIQUE_SNAPSHOT_ID/draft.json
```

无机构大小球报价时可显式传--line，但会标记研究指定盘口，不冒充市场盘口。英超ESPN全名与CSV简称需要有据可查的显式别名映射，默认不猜测或自动匹配。

有真实历史特征后：

```bash
python cli.py fit-context SAMPLES.json --features FEATURE_NAMES --out UNVALIDATED_CONTEXT.json
python cli.py validate-context SAMPLES.json --features FEATURE_NAMES --out CONTEXT_VALIDATION.json
```

样本必须包含cohort、kickoff_at、cutoff_at、baseline_observed_at、result_observed_at、baseline_home/away、home_goals/away_goals、features和官方evidence。训练/验证按时间分离，训练结果观察时间必须先于验证比赛cutoff；真实样本不足时不能使用合成样本代替。

预测草稿有完整真实核验后才可研究冻结：

```bash
python cli.py freeze ARTIFACT.json DRAFT.json --directory data/v2/research-freezes
```

## 验证解释

全部历史验证均是现在查询的历史重建，不是历史冻结回放。英超6个赛季每季380场；ESPN年度返回覆盖未获独立完整性认证。首次获知时间缺失，不能伪造。静态留出与逐月滚动对照结果均公开；滚动检查在查看静态结果后新增，不能称为全新未看过的测试。当前没有稳定Top1改善证据，所有候选DO_NOT_PROMOTE。
