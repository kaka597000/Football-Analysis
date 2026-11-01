# 长期联赛研究地图：欧洲域内与域外研究轨

登记日期：2026-11-01，北京时间（UTC+8）。文件名仍是 `asia-leagues-ood-research-tracks.md`，避免另写一份互相冲突的来源。本文件只登记研究边界。不重新训练，不修改 `runtime/active-model.json`、`runtime/predict_active.py`、永久 λ 或任何实盘引擎。已发布分析仍按 [冻结规范](analysis-freeze-policy.md) 追加，赛前与滚球分开；本次不是新的比赛预测。

机器可读名单见仓库根目录 `project-goals.json` 的 `league_research_map`。代码护栏见 `runtime/league_tracks.py`。

## 域内：欧洲五大联赛

当前欧洲训练与欧洲校准研究使用下列成年男子顶级联赛。代码以 `runtime/database/registry.py` 为准：

| 赛事 | 代码 | 位置 |
|---|---|---|
| 英超 | `eng.1` | 域内 |
| 西甲 | `esp.1` | 域内 |
| 德甲 | `ger.1` | 域内 |
| 意甲 | `ita.1` | 域内 |
| 法甲 | `fra.1` | 域内 |

`runtime/optimization/dataset.py` 的 Understat 五大联赛键，以及 `runtime/optimization_tools/xg_shadow.py` 的五大联赛 xG 影子范围，都只包含这五个代码。

## 域外：四条独立研究轨

下列四条成年男子联赛可以采集、可以各自研究。相对上述欧洲训练域，每条都是域外（out-of-domain，OOD）研究轨，标签为 `EXPERIMENTAL / OOD`。四条彼此隔离，也不并入欧洲训练。

| 赛事 | 代码 | 研究标签 | 代码依据 |
|---|---|---|---|
| J1 League（日职联） | `jpn.1` | EXPERIMENTAL / OOD | `registry.py`：日职联 |
| K League 1（韩K联） | `kor.1` | EXPERIMENTAL / OOD | `registry.py`：韩K联 |
| A-League Men（澳超） | `aus.1` | EXPERIMENTAL / OOD | `registry.py`：澳超 |
| MLS（美职联） | `usa.1` | EXPERIMENTAL / OOD | `registry.py`：美职联；别名 `MLS` |

`usa.1` 是相对 2026-11-01 最初亚洲三条登记新增的同一类 OOD 轨，不是欧洲域内联赛。采集器常规赛名单里已有这四个代码，只说明可以收集。列入采集范围不等于样本已经进入欧洲训练，也不等于候选已经晋级。

已有队列工件（澳超常数基准与 `AUS-EXPERT`、日韩研究路由、美职联双模型队列）仍是各自的研究路由。它们的存在不构成实盘 Champion 晋级。队列说明见 [双模型流程](dual-model-workflow.md)。澳超逐场开放性审查仍见 [澳超背景](aus-league-context.md)，该要求不改变域外标签。

## 禁止事项

1. **不得把这四条联赛的样本混入欧洲训练**，也不得把一条 OOD 联赛的样本、权重或命中率借给另一条。历史回放、同场开发对照和采集入库都不能代替下面的门槛。
2. **不得把 OOD 候选自动晋级为实盘 Champion。** 私有训练任务的策略保持 `promotion: NEVER` / `automatic_promotion: false`。候选可以留在私有研究状态，不能替换当前模型、test 模型、Champion、永久 λ 或活动注册表。
3. **研究预测必须标明 `EXPERIMENTAL / OOD`。** 未通过该联赛自己的晋级讨论门槛之前，不得把矩阵最大值、市场热门比分或标签改写说成实盘 Primary Top1。
4. 关键证据不足时明示待定。不把 OOD 研究输出补进欧洲样本的正式 KPI，也不赛后挑选较好版本追认。不覆盖已冻结分析。

## 将来若讨论晋级，每条联赛单独要满足的条件

下面是讨论门槛，不是本次已经满足的结论，也不是晋级许可。`jpn.1`、`kor.1`、`aus.1`、`usa.1` 必须各自用本联赛样本验收，不能四条合并，也不能用欧洲五大联赛的成绩代替。

1. 拟合、权重和评估分母只用该联赛自己的比赛。不用欧洲五大联赛或其他 OOD 联赛的参数充数。
2. 赛前冻结、时间隔离。输入已知时点不晚于预测截止，不用赛后信息。赛前与滚球继续分开。
3. 未来未见样本上，预先指定唯一 Primary。比分、胜平负、实际盘口大小球分母独立；待定保留在覆盖分母里。
4. 同场对照该联赛自己的基线。Primary Top1 需要稳定正增益。log-loss 或 RPS 不能替代 Primary Top1。
5. 讨论发生之前，标签保持 `EXPERIMENTAL / OOD`，且不得自动写入 Champion。

这些条件对齐 [项目目标](project-goals.md) 的闭环：采集核验、冻结、复盘、独立实验、时间隔离验证，然后才可能保留、切换或回滚。本次登记不启动该讨论。

## 与正式指标的关系

唯一正式最高指标仍是未来真实冻结样本的 Primary 精确比分 Top1。比分 70%、胜平负或实际盘口大小球 80% 仍未证明达到。研究轨命中率单独统计，不并入欧洲实盘分母。本次没有训练模型，也没有晋级。

## 代码护栏与未关闭的缺口

`runtime/league_tracks.py` 拒绝把 `aus.1`、`jpn.1`、`kor.1`、`usa.1` 放进欧洲训练代码名单，并拒绝把这四条标成可晋级。调用点：

- `runtime/research/egidi_blend/`：混合权重、LambdaMART 和欧洲出折样本。四条 OOD 只在各自联赛上拟合 λ，不进入欧洲训练，评估也不互相合并。
- `runtime/optimization/dataset.py` 的五大联赛 xG 键，以及 `runtime/optimization_tools/xg_shadow.py` 的五大联赛范围。
- `runtime/automation/trainer.py`：OOD 队列若单独写出候选，`automatic_promotion` 必须为 false。

尚未关闭、本次也不新造管道的部分：

- 无人值守训练仍可按队列单独拟合 OOD 候选。这是隔离研究，不是欧洲混合训练集。
- 系统研究导出仍可按注册表队列分 cohort 保留 OOD 比赛。导出按 cohort 分开拟合，不把这四条写进五大联赛 xG 键。
- 本仓库没有把候选写进 Champion 或 `runtime/active-model.json` 的晋级函数。不新增该函数，也不修改活动注册表。
- v4 `validate_training` 已要求单一赛事路由后才能拟合。本次不改该路由的阶段分类。
