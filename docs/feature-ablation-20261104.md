# 真实历史 xG 月度滚动消融：2026-11-04

北京时间 2026-11-04 11:08 完成独立 CPU 实验。已真实拟合 **506 个 Poisson GLM**（253 个按月双模型），保存 **8,685 场成对逐场预测**。新增 xG 的统计 Top1 净增 **13 场**，差异不确定区间包含零，不能声称已证明提高第一比分准确率。Current/Test/Champion 和旧冻结均未修改，未部署、未晋级。

这里的统计 Top1 是独立 Poisson 联合分布众数，是 `modelPrimary` 类型实验指标；不是综合多证据人工 Primary，也不是活动 Current/Test 历史部署业绩。本批历史已参与开发，**不是未来验证**。盘口门槛实际执行为 **BLOCKED**：认证历史赛前报价 0/30,180，盘口训练与预测均为 0；没有伪造盘口消融。

## 输入与固定实验协议

- 输入：`/workspace/WCPP/research/all-scope-20261104/features/records.json`，30,180 个真实 90 分钟 fixture；其中 Understat 同场 xG 10,273。源数据库 SHA-256、完整输入 SHA-256 与来源计数存于输出 `protocol.json`；源事实链接说明见同目录 `features/README.md`。
- 研究范围固定为欧洲五大联赛，10,294 场。严格按 `(cohort, competition, stage)` 分隔；意甲 `regular` 与 `regular-season` 不合并。没有引入亚洲/OOD 或杯赛。
- baseline 特征：canonical 主客角色、本队和对手各自过去 10 场进球/失球均值。
- +xG 特征：在相同 baseline 上增加本队和对手各自过去 10 场 **Understat** xG/xGA 均值。xGA 必须等于同一提供方对手 xG；不混提供方。双方都至少有 3 场既往赛果与 3 场既往 xG 才有资格进入成对实验。缺失从未补 0；真实观测的 0 保留。
- 目标当场 xG 完全不进入目标特征；赛后 xG 仅在整日期块处理完成后加入未来日期历史。同日期所有比赛使用同一日期之前的历史。逐场依赖保留 match_id、日期、provider_match_id、source hash、source path、observed 和 first-known 字段。
- 每个阶段/联赛单独学习一个有主客特征的共享 Poisson 计数回归：双方各提供一条球队进球训练行。两个模型使用**完全相同的比赛、目标和 fit 窗口**。特征只按训练样本标准化，L2 alpha 固定 0.1，max_iter=500、tol=1e-9，不手写 xG 影响权重。
- 每月 1 日之前的已结束日期用于 expanding fit，至少 200 个历史 fixture；系数在当月固定。月内目标特征可使用目标日期之前的新历史，但不会月内重拟合。当月/后段标签不选超参、不反选版本。
- GLM 未使用 `neutral` 变量。输入五大 10,294 场 neutral 字段全部缺失/未知，只使用 canonical 主客角色；**未知没有当成 False，也未认证真实场地**。此样本不得与要求 explicit neutral 的 walk-forward 样本合并。
- first-known 全缺：五大赛果 10,294/10,294、Understat xG 10,273/10,273。来源抓取时间发生于赛后，不能证明历史发布时点。全部只支持历史先前日期重建，历史 as-of 认证预测 **0**。

## 完整覆盖分母与结果

| 层级 | fixture 数 |
|---|---:|
| 全输入 | 30,180 |
| 五大范围 | 10,294 |
| 既往赛果/xG 双边合格 | 9,975 |
| 初期历史不足拒绝 | 319 |
| 月初 fit 历史不足拒绝 | 1,290 |
| 完整成对预测 | 8,685（五大范围的 84.37%） |

成对预测日期为 2021-03-01 至 2026-10-09（源仅日期精度，不虚构具体开球时刻）。

| 指标（同 8,685 场） | 仅赛果 baseline | 加先前 xG |
|---|---:|---:|
| 统计比分 Top1 | 1,061 / 8,685 = 12.216% | 1,074 / 8,685 = 12.366% |
| 胜平负 | 4,393 / 8,685 = 50.581% | 4,496 / 8,685 = 51.767% |
| 精确比分 log-loss（低更好） | 2.961349 | 2.945314 |
| 胜平负 log-loss | 1.015273 | 1.002596 |
| 胜平负 Brier（三项之和） | 0.607600 | 0.598412 |
| 胜平负 RPS（两项平均） | 0.209716 | 0.205379 |
| 总球 ≥4 Brier | 0.212227 | 0.211099 |
| 双方零球 Brier 均值 | 0.183663 | 0.182615 |
| 总球预测均值 | 2.787901 | 2.833867 |
| 预测总球 ≥4 平均概率 | 30.576% | 31.583% |

实际总球均值 2.818998，实际 ≥4 球频率 31.491%。概率由无限支撑 Poisson/Skellam 计算，不截断尾部或把低众数总球当总球预测。胜平负概率取三类概率最大值，与比分众数方向独立。

成对 Top1：新增命中 **204**、损失命中 **191**、两边均中 **870**，净 **+13 / 8,685 = +0.150 个百分点**。按 cohort/stage/月块、固定 seed 20261104 的 2,000 次 bootstrap，差值 95% 区间 **[-0.299,+0.590] 个百分点**；配对精确 McNemar p=0.546。区间只是历史开发数据的描述性不确定性，不能代替未见未来验证。

概率约束分别保留：总体 score log-loss、WDL log-loss/Brier/RPS、4+ 与零球 Brier 均较 baseline 小，但没有据此设事后晋级容忍度。Top1 仍为最高指标；没有达到 70%/80% 目标，不能以概率改善代替 Top1 认证。

| 独立阶段组 | 成对场数 | baseline Top1 | +xG Top1 | 新增/损失 | 净增 |
|---|---:|---:|---:|---:|---:|
| 英超 regular | 2,044 | 218 | 217 | 46 / 47 | -1 |
| 西甲 regular | 1,691 | 234 | 234 | 45 / 45 | 0 |
| 法甲 regular | 1,808 | 214 | 211 | 31 / 34 | -3 |
| 德甲 regular | 1,306 | 148 | 150 | 34 / 32 | +2 |
| 意甲 regular | 1,836 | 247 | 262 | 48 / 33 | +15 |
| 意甲 regular-season | 0 | — | — | — | — |

意甲 regular-season 有 200 场双边历史合格，但无月初达到 200 个既往合格 fixture 的目标月，因此没有强行训练。总体净增主要来自意甲，其他组未见一致 Top1 改善；未据此选择意甲或删除负收益组。按年完整结果在 `summary.json`。

## 严格盘口证据门槛

原始私有 market 只有机构、capture、开球、盘线、两侧赔率，`quote_time` 缺失。capture 不等于报价时间，closing 名称也不证明真实赛前时点。输入未合并任何合格市场行，30,180 场均 `NO_CERTIFIED_MARKET_JOIN`。

`market-gate.json` 是实际运行的拒绝结果：`BLOCKED`，认证赛前报价 0，training 0，ablation predictions 0。没有使用 closing/capture 代替 quote_time，没有制造历史盘口收益；本轮无法报告实际盘线大小球命中率。4+ 尾部概率仅为无盘口分布诊断，不能叫实盘大小球准确率。

## 审计、局限与交付

9 项定向测试通过：目标/同日期过程与标签变动不影响当日特征；缺失不补 0；phase 和球队 ID 隔离；重复 fixture 拒绝；来源/日期不符拒绝；真实拟合与概率支持；month-start cutoff/完整双模型分母；capture 缺 quote_time 拒绝；净增损失核对。实际 9,975 特征、8,685 成对预测及 253 fit 另做全量审计：同日/未来依赖、目标入历史/fit、跨提供方、来源 hash 缺失、训练截止越界、丢失成对模型、概率质量越界均为 **0**；全部 GLM 收敛。

历史窗口为最近 10 个**已观测** xG，而非保证近期完整窗口。19,950 个球队特征行中，171 个最近 xG 距目标超过 90 天，最大 1,549 天；依赖日期如实保留，未用目标表现重选鲜度阈值。这说明需要未来单独预登记鲜度/身份连续性验证，本轮不能称已修复所有长期缺口，也不能凭小净增切换模型。

持久工件目录：`/workspace/WCPP/research/all-scope-20261104/ablation/`。

- `protocol.json`：输入 hash、固定协议、创建时间。
- `features.jsonl`：9,975 场特征、数值、每条历史来源和时间依赖。
- `fits.jsonl`：253 个双模型月 fit，含训练 IDs、cutoff、标准化参数及真实学得系数。
- `predictions.jsonl`：8,685 场 baseline/+xG 的 λ、唯一统计 Top1、胜平负概率、尾部和完整目标/误差。
- `excluded.jsonl`：所有初期历史不足/训练窗口不足拒绝项。
- `summary.json`：总体、逐阶段和逐年完整分母、净增损失及概率约束。
- `market-gate.json`：盘口 BLOCKED 的真实分母和拒绝原因。
- `execution-audit.json`：全量时间/来源/venue/数值审计；`manifest.json` 与追加 `audit-manifest.json` 保存工件哈希。

复现实验（新目录；已有 protocol 的目录拒绝覆盖）：

```bash
cd /workspace/Football-Analysis
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m unittest runtime.research_program.test_feature_ablation -v
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m runtime.research_program.feature_ablation \
  --records /workspace/WCPP/research/all-scope-20261104/features/records.json \
  --out /workspace/WCPP/research/all-scope-20261104/ablation-reproduction-NEW
```

决策：保留独立 inactive 研究；不改活动参数，不追认旧预测，不将结果称前瞻验证。后续若评估鲜度限制/模型结构，需另存固定协议和完整新分母，并在真实未来冻结验证中确认。
