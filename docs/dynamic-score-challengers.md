# 动态实力与比分分布 Challenger

独立代码：runtime/research_program/challengers.py。球队攻防以带收缩的在线Poisson梯度状态更新，考虑对手强度、主场和实际天数；同日比赛全部先预测再更新。新球队回到先验；只有明确season字段才执行预登记换季收缩，缺失season/换帅不补造。它是轻量动态泊松实验，不冒充完整状态空间贝叶斯后验。

比较独立Poisson、双变量Poisson、负二项和共享节奏混合。各分布保持主客均值，不通过人工加球制造尾部。有限支持保留截断质量审计。当前DC模型不改。

日期分组的fit/cal/diagnostic为50/25/25，族/参数仅由cal选择，Top1、LogLoss、比分Brier与WDL Brier同时约束；diagnostic模型仅拟合fit+cal，不含测试标签。评估沿用V2 Top1/WDL/尾部/校准统计。实际Current的同场未来对照仍须另采；不能把dynamic-Poisson对照叫作实际Current。

```bash
python runtime/research_program/train_challengers.py --registry /workspace/WCPP/runtime/active-model.json --out NEW_DIRECTORY --cohorts eng.1 esp.1 ger.1 ita.1 fra.1 swe.1 nor.1
```

入口只从实测SHA匹配的旧工件读取历史，输出到新目录，不改Current/Test。历史capture/first-known缺口未解，结果均为开发诊断，不是认证as-of或真正未来未见成绩。不推定候选比现有模型好；不自动晋级。

尚未完成完整状态空间/分层贝叶斯比较、现代赛季数据补齐、xG特征消融和实际未来同场验证。七队列训练若完成也不能替代这些步骤。

前向重拟合入口 refresh_challengers.py 重新核验新summary，按身份/比分/场地去重，保留旧特征与旧工件；同队同日附近冲突阻断，不静默覆盖。实际273场中269条净新增、4条跨来源重复合并，总历史2734行。另存forward-candidate，7项训练截止更新至11月1/2日，但该重拟合包含已经检查过的诊断标签；诊断成绩属于另一个fit+cal模型，不能当作forward重拟合的独立验证。全部inactive。
