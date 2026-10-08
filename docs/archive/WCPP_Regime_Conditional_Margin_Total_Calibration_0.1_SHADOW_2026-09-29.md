# WCPP Regime-Conditional Margin–Total Calibration 0.1｜SHADOW

**版本日期：** 2026-09-29  
**模型ID：** `WCPP_REGIME_CONDITIONAL_MARGIN_TOTAL_0.1_SHADOW`  
**状态：** `SHADOW_ONLY`  
**Champion：** WCPP V21.5（不变）  
**正式框架：** WCPP V21.5-R2 + TFA Core 3.2（不变）  
**Football λ：** 不修改  
**新增预测Gate：** 0  

## 1. 目的

解决近期复盘出现的 `CENTER_COMPRESSION / TWO_TAIL_MISS`：

- Primary/Joint MAP过度集中在0-1、1-0、1-1、2-0、2-1；
- 某些比赛真实结果落入0-0/低事件尾；
- 另一些比赛落入4+/5+/6球高事件尾；
- 不能通过整体抬高Football λ解决，因为这会同时损坏低尾。

因此将比分层拆为：

```text
Frozen Base Score Matrix
        ↓
Total Regime
LOW=0–1 / CENTER=2–3 / HIGH=4+
        ↓
Signed Margin
D0 / H1 / A1 / H2 / A2 / H3+ / A3+
        ↓
IPF / KL-style minimum-change projection
        ↓
64-cell exact-score rerank
        ↓
Primary / Alternative
```

## 2. 数学约束

新分支不得直接修改原Football λ。

目标是寻找与原始矩阵 `P_base(h,a)` 尽量接近的新矩阵 `Q(h,a)`，同时使其Total Regime和Margin边际更接近校准模型：

```text
minimize KL(Q || P_base)

subject to:
Q(T-regime) ≈ calibrated Total Regime
Q(Margin bucket) ≈ calibrated Margin
sum Q = 1
```

当前工程实现采用Iterative Proportional Fitting（IPF）执行KL风格的最小改动投影。

## 3. Development Prototype

数据：`top1_historical_replay_v1` 1751条五大联赛历史replay。

时间隔离：

- Train：2025-08-15 ～ 2026-01-31，N=1025
- Test：2026-02-01 ～ 2026-05-24，N=726
- 2026-09-28国家队赛果：**未用于训练或选参**，只作为诊断背景。

输入仅使用冻结replay中的赛前λ及赛事标签：

- lambda_home / lambda_away
- lambda_total
- lambda_diff / abs_lambda_diff
- log(lambda_home/lambda_away)
- 二次项与乘积项
- competition

Total Regime与Margin分别使用带L2正则的多项Logistic模型；`C`仅在训练窗内部时间后置验证选择，最终两者均选择 `C=0.03`。

## 4. 时间隔离测试结果

| Branch | Top1 | Top3 | Top5 | Top10 | Top20 | Score Log Loss |
|---|---:|---:|---:|---:|---:|---:|
| A Raw Poisson | 13.09% | 32.09% | 49.72% | 78.79% | 96.42% | 2.8838 |
| B Total only | 12.53% | 31.96% | 49.31% | 77.82% | 96.42% | 2.8848 |
| C Margin only | 12.26% | 33.06% | 49.17% | 78.51% | 96.69% | 2.8932 |
| D Total + Margin | 13.22% | 31.54% | 49.59% | 77.82% | 96.56% | 2.8936 |

D相对A：

- Primary Top1：`+0.14pp`
- Top3：`-0.55pp`
- Top10：`-0.96pp`
- Score Log Loss：`2.8838 → 2.8936`（恶化）

Regime分类本身：

- Base marginal log loss：1.0378
- 校准模型：1.0388

Margin分类：

- Base marginal log loss：1.7204
- 校准模型：1.7297

所以当前数值Candidate **不具备晋级条件**。

## 5. Mean-preserving Dispersion负面对照

额外拟合了三状态共享乘数混合模型，强制加权均值保持1，从而不修改Football λ总体均值。

训练结果的最大状态权重为 `99.9053%`，模型几乎退化为单状态。

结论：

> 当前1751条俱乐部replay不支持“全局统一扩大dispersion”。

因此下一步只能做 `cohort-specific / competition-specific`，不能使用一个统一NB/Regime倍率打遍所有赛事。

## 6. 国家队数据层

2026-09-29已将2026/27欧国联9月24～28日官方完赛42场追加进入Canonical DB。

用于Regime研究的严格训练表只收：

- 成人男子；
- UEFA Nations League；
- `league_phase`；
- 明确90分钟完赛比分；
- L1/已规范化来源。

当前 `national_team_regime_training_v1`：

- 可训练行：197
- 时间范围：2024-09-05 ～ 2026-09-28
- 2024-11-15 Romania–Kosovo行政结果仍保持不可训练，不补造比分。

2026/27前42场：

- 平均总球：2.429
- 总球方差：2.912
- LOW 0–1：30.95%
- CENTER 2–3：47.62%
- HIGH 4+：21.43%
- 0-0：14.29%

历史可比League Phase：

- LOW：28.39%
- CENTER：42.58%
- HIGH：29.03%
- 0-0：9.68%

这进一步说明：9月28日单批出现多个高比分，不能被解释成整个国家队cohort永久进入高离散状态。

## 7. 数据源策略

### L1直接入训练候选
UEFA官方Nations League赛果：
https://www.uefa.com/uefanationsleague/news/02a2-1fea18abbcbc-456e846509e7-1000--2026-27-uefa-nations-league-all-the-league-phase-fixtures-a/

### L2 Raw staging only
martj42 international_results：
https://github.com/martj42/international_results

该公开数据集覆盖男子成年国家队，但其README明确说明 `home_score/away_score` 可能包含加时，因此不直接写入90分钟训练表。先注册为Raw staging来源，待90分钟口径规范化后再晋级。

## 8. 研究依据

- Bayesian state-space / bivariate negative-binomial：  
  https://academic.oup.com/jrsssc/article/74/3/717/7929974
- Extended Dixon–Coles / Sarmanov / overdispersion：  
  https://academic.oup.com/jrsssc/article/74/1/167/7818323
- First-break / cascade exact-score rerank exploratory paper：  
  https://arxiv.org/abs/2608.05030

第三篇仅作为架构探索依据，不作为“已经证明有效”的证据。

## 9. 当前决定

```text
Champion                     WCPP V21.5 unchanged
Football λ                   unchanged
New Gate                     none
Regime-Margin-Total          SHADOW_ONLY
Current numerical prototype  DO_NOT_PROMOTE
National-team expansion      active
Formal 500+200               not restarted
```

下一轮只允许：

1. 扩充可验证的成人男子国家队90分钟数据；
2. 按Competition/Cohort训练Regime；
3. 使用未来冻结样本生成D-branch Shadow；
4. 在Top1提升且LogLoss/RPS不恶化后固定Candidate；
5. 固定后才重新预注册500+200。
