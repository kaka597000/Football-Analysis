# 2026-11-04逐场误差诊断与未来配对入口

本轮是历史重构诊断和后续验证准备，不证明未来命中率提高，不晋级、不修改活动注册表、不创建定时任务。统计矩阵众数为本次独立研究Primary，不能改名为综合人工第一推荐。

## 已完成的真实运行

`runtime/research_program/diagnostics.py` 已对七联赛1741场、四模型6964份留存矩阵运行。读取每个NPZ前核验fold文件SHA256，再核验逐场矩阵SHA256、概率质量、众数一致性和独立矩阵索引。输出在 `/workspace/WCPP/research/all-scope-20261104/diagnostics/`：`report.md`为逐联赛可读表，`summary.json`为汇总，各联赛JSON保存全部逐场诊断。

|模型|Top1命中/1741|独立WDL命中|比分错但独立方向对|总球均值偏差|众数总球−分布均值|
|---|---:|---:|---:|---:|---:|
|current_recipe|184|897|832|−0.105|−0.953|
|dynamic_poisson|210|875|835|−0.072|−0.846|
|negative_binomial_5|175|875|779|−0.072|−1.479|
|tempo_mixture|188|875|805|−0.072|−1.264|

总球均值偏差=预测总球均值−实际总球。负二项及tempo保留动态模型均值，但更多概率进入零球与尾部，众数总球进一步降低：更灵活的分布没有自动解决Top1问题。逐场JSON将均值残差、主客零球、单队与总球3+/4+/5+概率校准、实际比分概率/排名、众数和独立WDL方向同时保留。这些诊断有重叠，不能相加为因果归因。

动态泊松相对Current历史重构同场独有命中65、Current独有39，净+26；WDL改对56、改错78，净−22。负二项净−9、tempo净+4。候选独有命中属于事后描述，不能构造可实现的oracle收益，也不能把历史+26称为未来增益。没有核实的历史实盘报价，不补虚拟2.5线。

## 旧冻结完整性审计

`future_validation.py audit-existing` 已只读审计 `/workspace/WCPP/forecasts/issued-analyses`。最终结果为 `/workspace/WCPP/research/all-scope-20261104/future/existing-first-freezes-v2.json`。早期v1未正确保留缺生成元数据的ABSTAIN，已追加v2修正，v1不可作最终统计。

- 首次赛前Current/Test配对226场，其中174场有双矩阵，52场双方ABSTAIN，均保留覆盖分母。
- 冻结记录及链错误0，已存输入/生成时间与赛前时序冲突0。ABSTAIN缺失的生成元数据单列可用性，不推断其存在。
- 70份保留execution audit的Current工件SHA256；其余不能追认为具备该元数据。
- 9场同时具备真实机构/盘口/双边价格/quoted_at和query_time，且真实报价在截止前一小时内。仅有查询时间不替代真实报价时间。
- Challenger独立配对为0。没有赛后重跑或补造候选冻结。

## 已锁定与待完成状态

已真实执行`lock`，把七份已有`WCPP-DYNAMIC-FORWARD-0.1`动态泊松候选的原始工件SHA256、Current版本/工件SHA256、活动注册表SHA256及推理代码SHA256写入：

`/workspace/WCPP/research/all-scope-20261104/future/protocol-lock.json`

候选来自 `/workspace/scope18-audit-20261104/WCPP/research/challengers-20261103-forward-refit/*/forward-candidate.json`。不是重选历史表现最好的分布。协议固定最少30场、单侧alpha=.05、完整Primary覆盖和零概率质量退化容忍；仍需独立来源as-of及真实线审计。锁定候选并不等于已预注册未来赛事总体；当前状态为`LOCKED_AWAITING_UNSEEN_FIXTURE_UNIVERSE`。

未提供符合身份/中立场核验及未开赛条件的真实目标时，保留待补的未来总体，不伪造赛果或未来配对。不把代码单测包写入真实预测台账。

## 随后执行的真实未来样本

本轮随后核验ESPN显式neutralSite及赛事/球队ID，预注册Everton–Coventry、Brighton–Brentford两个未开赛目标，实际调用Current/Test和已锁动态泊松候选，产生两场首次赛前配对。Current/Test与候选分别冻结，留存完整矩阵和输入；两场尚未结束，结算样本0，缺真实盘口，不产生实盘方向业绩。这是矩阵研究验证，不是完整综合赛事推荐；原226场旧冻结不回改。

## 执行后续真实配对

先使用真实未开赛赛事总体JSON数组注册。每项提供`match_id/cohort/stage/kickoff_at/home/away`；命令会将全体ID和双方身份按联赛预注册，不只保存挑选子集。

```bash
python runtime/research_program/future_validation.py register \
  --protocol /workspace/WCPP/research/all-scope-20261104/future/protocol-lock.json \
  --fixtures /absolute/verified-future-universe.json \
  --out /absolute/new-plan-receipts.json
```

随后在真实新的数据截止时间采集同场fixture，`cutoff_at`必须晚于实际预注册冻结、早于当前时钟和开球；来源可用性不能倒填。`neutral`等真实主流程要求照常执行，未知不能强设false。`--plan`指上一步回执`path`所对应的实际预注册冻结文件，不能传回执列表本身。

```bash
python runtime/research_program/future_validation.py capture \
  --protocol /workspace/WCPP/research/all-scope-20261104/future/protocol-lock.json \
  --plan /absolute/registered-plan-freeze.json \
  --fixture /absolute/actual-new-cutoff-fixture.json \
  --out /absolute/new-paired-receipts.json
```

capture调用既有`prediction_service.run`计算真实Current/Test，另算锁定动态泊松候选。两份冻结使用完全相同fixture与cutoff；Current/Test保存在原`current-test-pair`轨，候选保存在`challenger-independent`轨，不覆盖任何轨道。已有首次赛前预测的目标拒绝重建。预先保存实际输出的journal与freeze idempotency key支持`--resume`，不重新推理和择优。默认仍为正式冻结目录；可用`--freeze-root`显式设置研究台账，但不能将测试数据放入真实台账。

复盘输入packages为上述配对回执对象的JSON数组；results为结果JSON数组，无赛果可传空数组。只有明确FT、90分钟、真实观察时间且保留ESPN响应原文/文件SHA256/身份核验/查询时间的结果才结算，复用`replay._finished_espn_label`核对赛果。结果evidence字段为`file/sha256/url/query_time/identity_verified`，file相对evidence-root。未完赛保持pending；没有捕获配对的预注册ID单列missing_first_pairs。

```bash
python runtime/research_program/future_validation.py review \
  --plan /absolute/registered-plan-freeze.json \
  --packages /absolute/paired-receipts-array.json \
  --results /absolute/verified-results-array.json \
  --evidence-root /absolute/retained-source-root \
  --out /absolute/new-review.json
```

比分、独立WDL及真实线大小球分别保留分母。盘口只接受同fixture、同机构/线、双边价格及真实报价时间，五类结算不合并为二元命中。概率门槛复用`promotion_review.assess`，缺总体、样本、来源/实盘独立审计均阻断。任何通过软件门槛的结果仍不自动晋级或变更注册表。

## 检查

运行`PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1 python -m unittest runtime.research_program.test_diagnostics runtime.research_program.test_future_validation -v`：26项通过（17项新增诊断/未来验证测试，另9项为引用的已有promotion review测试）。覆盖矩阵篡改、众数与独立方向分离、待赛/缺配对完整分母、真实报价时间、半输结算、保留响应篡改、缺赛果证据、工件变更、拒绝历史捕获及ABSTAIN保留。以上软件检查不构成未来足球准确率证据。更大范围集成测试由主代理统一执行。
