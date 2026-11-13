# 欧国联独立双模型与验收

2026-11-10按用户要求实际训练。2026-11-13起未来训练、研究预测与冻结统一使用安全入口 `python -m runtime.nations_league.safe_cli`；旧 `runtime.nations_league.model` 保留为原工件的历史核心，源码哈希与既有工件保持不变。
4个等级各自训练两个独立统计候选，阶段仅限 `league-phase`。
当前候选学习Poisson截距与主场参数；test候选学习正则化球队进攻及对手防守参数。
配方、730天半衰期和ridge=8在运行前锁定，未根据本轮留出成绩重选。

## 已执行结果

数据来自独立库502条经原文复核的90分钟历史。每级用2026年以前记录拟合评估工件，2026年记录仅用于开发留出重构；各阶段、等级和俱乐部均不混训。
最后再用全部合格历史生成8个面向未来的工件；评估工件和全历史工件分开保存，全历史工件不冒称验证过其已含标签。

|等级|评估拟合量|留出量|当前候选第一比分命中|test候选第一比分命中|结论|
|---|---:|---:|---:|---:|---|
|A|77|32|2|2|未取得严格增益|
|B|107|32|3|5|比分净增2，概率质量退步|
|C|142|32|4|3|比分与方向退步|
|D|72|8|2|2|留出样本少于30|

这是历史结果日期隔离的开发诊断，原文first-known仍未知，不是实时as-of回测或未来成绩。没有真实历史盘口，因此不报告实际盘口大小球命中率，也不以固定2.5代替。新加入某等级的球队在历史诊断中可使用该模型已拟合的总体先验，逐场列明未见球队，不虚构球队历史。

四级均未满足本轮历史质量门槛，也没有完成未来冻结样本、来源及实盘审计。**活动预测注册表保持不变，不能把已训练称为已通过验收。** 本任务不改原模型、原Champion或旧冻结。

## 调用与冻结

```bash
python -m runtime.nations_league.safe_cli train \
  --db /workspace/WCPP/data/nations-league/uefa-nations-20261110.sqlite \
  --out /absolute/NEW-model-directory

python -m runtime.nations_league.safe_cli freeze-pairs \
  --index /absolute/NEW-model-directory/candidates.json \
  --fixtures /absolute/verified-future-fixtures.json \
  --out /absolute/NEW-freeze-output
```

目录和输出拒绝覆盖。`fixtures`为JSON数组，每场必须有真实summary原文的路径、SHA、URL、查询时间；等级缺失时允许已经严格核对的同场scoreboard补证。球队、开球时间、阶段等级、中立场和未来状态均重新验证。未来fixture禁止目标比分标签；工件创建时间须在实际预测截止之前，训练标签须在目标日期之前。数据、协议与执行源码哈希须和训练登记一致。

安全入口额外要求 `match_id` 与原文 `source_event_id` 完全对应，拒绝 `home_90`、`home_total`、`home_score`、`score`、`actual` 等目标赛果别名，并核对索引内current/test角色和实际执行的核心源码。全批两边候选均校验、计算成功后才开始冻结，业务错误不会发布半批；多文件写入遇磁盘故障的事务回滚不属于这一保证。新计划与预测保存校验层版本、校验层/旧核心/共享数值源SHA，训练另保存 `execution-guard.json`。原矩阵算法直接复用，历史工件不改哈希、不重训、不晋级。

单场只计算研究输出时使用 `python -m runtime.nations_league.safe_cli predict --artifact /absolute/current-artifact.json --fixture /absolute/fixture.json --out /absolute/NEW-output.json`；该命令本身不构成冻结。

首次执行前冻结完整未来名单及两边工件SHA，再冻结各场完整矩阵和统计参考Primary。本轮由Luna取得52场未来summary，实际HTTP200、原文明示场地；52场均保存为 `nations-paired-candidates` 轨。它独立于活动current/test和综合人工分析，统计矩阵最大格子不代表综合第一推荐。具体冻结ID、时间、工件和原文均在数据交付快照中。

未来验收以分级完整首次冻结分母为准，计划每级至少30场，来源/时点和实际盘口另审，禁止赛后从两边择优。当前已知未来赛程A/B/C各16场、D4场，单次这批仍不足每级30场，需要后续预登记批次继续积累；目前不安排新定时任务。本模块不会自动激活候选，也不会将单纯样本累计视为已通过概率质量或效果验收。

本轮52场仅有赛程身份原文，未采集合格实盘报价；冻结主要用于统计比分/方向的未来观察，不能覆盖实盘大小球验收。后续取得报价须另记真实时点与修订，不能补填成这批首次冻结时已有的证据，也不能将全部52场自动认定为完整验收样本。

研究统计模型已训练的通道只有历史90分钟进球、主场以及test球队攻防；xG、伤停、首发、数学战意、完整体能及市场系数仍未训练。要进行正式赛事综合分析，仍须按用户需求实际采集近期过程、伤停与盘口证据。
