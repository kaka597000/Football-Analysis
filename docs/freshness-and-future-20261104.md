# xG鲜度消融与未来总体复核｜2026-11-04

## 实际执行

在原历史重建实验之外新增固定90日xG依赖门槛。每个目标只使用目标日期前、距目标1至90天内的最近最多10条同提供方xG；双方至少各3条，先判断资格再构造特征。赛果历史仍用原先最多10场窗口。baseline与+xG在同一8,057场、同一月初训练窗口拟合，不把缺失或被排除的xG填零。目标及同日数据继续禁止进入特征。`--max-xg-age-days`不传时原实验行为保持。

协议与输入hash在拟合前写入独立输出目录：`/workspace/WCPP/research/next-stage-20261104/xg-fresh90/`。没有覆盖原8,685场实验。两批样本和拟合不同，不能将+13与+17相减作为“鲜度带来4场增益”。本批既有历史参与过开发，仍不是未知未来验证。

- 五大10,294场中，9,347场具备合格双方先前特征，8,057场进入成对预测；947场特征不足，1,290场月初训练不足。
- 234个月度双模型fit，即468次真实GLM拟合。
- 统计Top1：baseline990/8057；+xG1007/8057；新增192、损失175，净+17（+0.211个百分点）。McNemar p=.404，月块bootstrap95%区间[-0.247,+0.681]个百分点，仍未证明Top1增益。
- 独立胜平负：4080→4181；比分log-loss2.955907→2.943189；WDL RPS .209654→.206089。
- 逐条检查9,347份特征，xG依赖超出1至90日的记录0。市场训练仍0，历史first-known认证仍0，中立场未知这一原始限制保留。

这些指标为独立统计Poisson基线及+xG模型，不是活动Current/Test或综合人工Primary，不晋级。

复现需新目录：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m runtime.research_program.feature_ablation \
  --records /workspace/WCPP/research/all-scope-20261104/features/records.json \
  --out /absolute/new-directory --max-xg-age-days 90
```

## 真实未来配对复核

本轮实际调用既有`future_validation.review`重新验证两场已冻结总体，并使用新增`future_status`遍历全部预注册联赛、按总体分配配对和结果。保留未捕获样本与待赛样本，不根据是否有结果缩小分母。输出：

- `/workspace/WCPP/research/next-stage-20261104/future-pending-review.json`
- `/workspace/WCPP/research/next-stage-20261104/future-population-status.json`

实际结果：注册2、配对2、待赛2、完赛0、缺首次配对0。两场开球为北京时间11月7日04:00、11月8日22:00。当前尚不能结算，也不能达到协议最少30场门槛。没有重新计算或覆盖已有首次冻结，没有新建定时器。后续应向同一入口提供经过原文/哈希/身份/90分钟核实的真实赛果，结果为空时仍保持待赛。

```bash
PYTHONPATH=runtime python -m research_program.future_status \
  --plan-receipts /workspace/WCPP/research/all-scope-20261104/future/plan-receipts-20261104.json \
  --packages /workspace/WCPP/research/all-scope-20261104/future/paired-receipts-20261104.json \
  --results /absolute/verified-results.json --evidence-root /absolute/raw-evidence \
  --out /absolute/new-population-status.json
```

11项定向测试通过，含鲜度先于资格判断、保留赛果历史、禁止无效日数和多联赛总体缺配对保留。旧9项消融测试保持通过。完整集成由主代理统一执行。
