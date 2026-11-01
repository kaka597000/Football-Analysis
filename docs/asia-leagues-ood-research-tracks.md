# 亚洲三联赛：域外研究轨

登记日期：2026-11-01，北京时间（UTC+8）。

本文件只登记研究边界。不重新训练，不修改 `runtime/active-model.json`、`runtime/predict_active.py` 或任何实盘引擎。

## 范围

以下三条成年男子联赛纳入私有数据仓库的采集范围，相对当前欧洲训练的实盘模型视为域外（out-of-domain，OOD）研究轨：

| 赛事 | 代码 | 研究标签 |
|---|---|---|
| A-League Men（澳超） | `aus.1` | EXPERIMENTAL / OOD |
| J1 League（日职联） | `jpn.1` | EXPERIMENTAL / OOD |
| K League 1（韩K联） | `kor.1` | EXPERIMENTAL / OOD |

采集范围依据：

- 公开采集器 `runtime/automation/collector.py` 的常规赛 ESPN 集合包含 `aus.1`、`jpn.1`、`kor.1`。
- 活动双模型自 `WCPP-DUAL-4` 起把这三条列为严格队列；当前登记为 `WCPP-DUAL-5` 的 21 队列之一。种子由 `runtime/automation/export_seed.py` 按活动注册表写入私有 `config/scopes.json`。
- 无人值守采集与候选训练只写入私有仓库 `kaka597000/kaka597000-Football-Analysis-Data`。见 [无人值守说明](unattended-operation.md)。

列入采集范围只表示可以收集并单独研究。它不等于这些样本已经进入欧洲实盘训练域，也不等于候选已经晋级。

## 相对实盘引擎的域外地位

当前 Champion、`runtime/v4`–`v7` 以及双模型 Primary Top1 引擎，主要在欧洲顶级联赛上校准。把澳超、日职联、韩K联相对该主域视为 OOD。

已有的队列工件（例如澳超常数基准与 `AUS-EXPERT` 研究轨、日韩经诊断适配器执行的当前工件及 `SYSTEMATIC-RESEARCH` test）仍是独立研究路由。它们的存在不把这三条联赛并入欧洲训练，也不构成实盘 Champion 晋级。队列接入说明见 [双模型流程](dual-model-workflow.md)。澳超逐场开放性审查仍见 [澳超背景](aus-league-context.md)，该要求不改变本文件的域外标签。

## 禁止事项

1. **不得把这三条联赛的样本混入欧洲训练**，除非另做时间隔离验证，并且单独通过晋级门槛。历史回放、同场开发对照和采集入库都不能代替该门槛。
2. **不得把亚洲专用候选自动晋级为实盘 Champion。** 私有训练任务的策略保持 `promotion: NEVER` / `automatic_promotion: false`。候选可以留在私有研究状态，不能替换当前模型、test 模型、Champion、永久 λ 或活动注册表。
3. **研究预测必须标明 `EXPERIMENTAL / OOD`。** 只针对这三条联赛、且未通过上述独立晋级门槛的预测，一律使用该标签。不得把矩阵最大值、市场热门比分或标签改写说成实盘 Primary Top1。
4. 关键证据不足时明示待定。不把 OOD 研究输出补进欧洲样本的正式 KPI，也不赛后挑选较好版本追认。

## 与正式指标的关系

唯一正式最高指标仍是未来真实冻结样本的 Primary 精确比分 Top1。本登记不声称这三条联赛或欧洲引擎已经达到比分 70%、胜平负或实际盘口大小球 80%。研究轨命中率单独统计，不并入欧洲实盘分母。
