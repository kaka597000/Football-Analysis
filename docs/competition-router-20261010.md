# WCPP-MAIN-1.1 赛事分类与赛制升级

更新日期：2026-10-10，北京时间（UTC+8）。这是新主模型的研究升级，继承9月29日 Competition Router 的隔离纪律；不是原 Champion 的晋级或重新预注册。

赛前顺序：比赛身份与来源 → 赛事分类 → 同类参数与历史 → 实力、战意、赛程、阵容、战术、场地审计 → 赛制审计 → 比分矩阵 → 实际盘口检验 → 留档与分组复盘。

| 分类维度 | 处理 |
|---|---|
| 男女 | 男足、女足独立训练及上下文验证 |
| 年龄 | 成年、U23、U21、U20、U19、U18、U17、U16、U15独立；未知年龄拒绝 |
| 级别 | 顶级、第二级、第三级、更低级、混合级别、国际级；杯赛还需审核双方实力 |
| 参赛主体 | 俱乐部与国家队分开 |
| 类型 | 联赛、国内杯、洲际杯、国家队正式赛、友谊赛、过渡赛事 |
| 阶段 | 常规赛、分组、淘汰、附加赛等保留独立路由；阶段不明不能训练 |

32个已登记的明确赛事代码可恢复成年男足身份，来源是现有明确代码注册表。不能根据队名、赔率或“英超风格”等猜男女和年龄；新赛事必须有官方分类证据。支持新类别的输入不代表已经有女足或青年模型。

英超“对攻”、西甲“传控”只作为待验证的问题。当前赛果库可以提供同路由此前730天、截至分析时已可见的进球、双方进球和平局统计，至少60场才标记为有足够描述样本；不能从赛果推断控球、xG或攻守意图。球队实际阵型、人员和对手关系仍通过战术证据审计。没有通过独立验证的系数就不改数值。

友谊赛默认阶段为friendly；MLS全明星单列友谊类型。巴甲、日职、挪超、瑞典超的明确年度赛季slug归为regular；附加赛不参与这种归并。存在季后赛或分组赛制的赛事，缺少阶段不默认常规赛。笼统的cup标签保持阶段未知，避免日本联赛杯分组与淘汰样本混合。

## 杯赛与非普通联赛阶段的输入

以下是字段结构示例，不代表已经核实的真实比赛或规则。证据必须支持该场、该阶段及当届规则。

```json
{
  "competition": "uefa.champions",
  "stage": "quarterfinals",
  "neutral": false,
  "competition_rules": {
    "competition": "uefa.champions",
    "stage": "quarterfinals",
    "format": "two_leg",
    "leg_number": 2,
    "aggregate_before_match": [0, 1],
    "away_goals_rule": false,
    "neutral": false,
    "extra_time_possible": true,
    "penalties_possible": true,
    "score_basis": "regulation_90",
    "qualification_state": "按本场主客顺序记录晋级局势",
    "rotation_summary": "有来源的阵容及轮换情况",
    "evidence": {
      "source_url": "https://example.org/official-edition-rules",
      "source_tier": "L1",
      "supports_field": ["competition_classification"],
      "query_time": "2026-10-10T09:00:00+08:00"
    }
  }
}
```

总比分必须按本场主队、客队顺序；首回合前必须[0,0]。晋级概率和90分钟胜平负不能混用。友谊赛补充`substitution_rules/experimentation_summary`；青年赛事补充`age_eligibility_rule`。

所谓杯赛“剧本”不作为模型特征。实际可审计的因素包括两回合激励、轮换、联赛优先级、实力跨度、中立场、平局是否可接受和加时可能性。规则缺失会使综合因素数值修正关闭，矩阵仅作OBSERVE研究；身份未分类、路由不匹配或中立场未知则停止预测。

## 实施与验证边界

分类接入训练、预测、上下文训练合同、留档重算及数据库导出。导出保留原`source_stage`，增加`competition_route/classification_training_eligible`；该布尔值仅说明分类完整，不证明中立场、时间可见性、参数验收或正式训练资格。当前统一投影未存中立场证据，导出`neutral=null`，训练会拒绝这些记录，须补充可核验场地状态后才能使用；不能把缺失中立场默认为false。原有已明确中立场状态的英超历史可用于本次研究重训。

1.0产物不能直接执行1.1。英超原2,280场真实历史已重新训练；开发诊断选择strength180，但测试段55/474低于基线57/474，接受门槛未通过，继续执行baseline。这个复用历史的诊断不是新增独立验证，固定2.5诊断也不是实际盘口准确率。

其他赛事需要按完整分类重训；缺少阶段的旧国家队正式赛不直接迁移。原留档保留，不升级旧预测版本、不追认冻结。Champion V21.5、Selector4.1-RP1、Regime-MT-KL和永久λ定位不变，正式KPI新增0。70%／80%目标尚未获证明。

可执行实现与输入命令见[runtime/v4/README.md](../runtime/v4/README.md)。全部历史数据及完整训练产物仅保存在云端WCPP目录，Git仅保存源码和说明。
