# WCPP PreMatch Context / Motivation / Integrity Audit 0.1

**版本日期：** 2026-09-29  
**ID：** `WCPP_PREMATCH_CONTEXT_MOTIVATION_INTEGRITY_AUDIT_0.1`  
**状态：** `ACTIVE_GOVERNANCE_SHADOW`  
**位置：** Competition Router之后、Strength/TFA之前  
**Champion：** WCPP V21.5（不变）  
**永久 Football λ：** 不做全局修改  
**新增预测Gate：** 0

## 1. 目标

每场比赛在进入正式WCPP建模前，必须先完成赛事族识别和赛前情境审计。

核心不是“猜剧本”，而是把以下信息结构化并保留证据链：

- 近期状态；
- 积分、排名、晋级、保级、争冠、种子位等真实竞技目标；
- 杯赛首回合/次回合与总比分；
- 轮换与实验阵容；
- 赛程拥挤、休息天数、旅行与时区；
- 教练/球员/足协公开表态；
- 阵容可用性与首发不确定性；
- 市场异常；
- 公开诚信/比赛操纵风险信息；
- 可能的比赛状态路径（低事件控制、开放级联、追分、保平等）。

## 2. 新固定流程

```text
Data Integrity / Identity
        ↓
Competition Router
        ↓
PreMatch Context / Motivation / Integrity Audit
        ↓
Cohort-specific Strength / Home Advantage / Dispersion
        ↓
TFA / Matchup / Availability
        ↓
Football λ + Four-State
        ↓
Regime / Margin / Score Matrix
        ↓
Score Selector
```

## 3. 战意不使用主观“强/弱”单标签

战意必须拆成可核证状态：

- MUST_WIN：只有胜利才能显著改善目标；
- DRAW_ACCEPTABLE：平局即可满足主要目标；
- DEAD_RUBBER：结果不能实质改变当前竞争目标；
- TITLE / PROMOTION / RELEGATION / QUALIFICATION / SEEDING 等具体数学状态；
- AGGREGATE_GAME_STATE：两回合总比分约束；
- ROTATION_EXPECTED；
- EXPERIMENTAL_SETUP；
- FIXTURE_CONGESTION。

数学状态优先使用联赛/赛事官方积分榜、规则和赛程自行计算，不用媒体“战意强烈”替代。

## 4. 近期状态

不同赛事族采用不同窗口，不统一套一个模板：

- 五大联赛：近期5–10场 + 主客场拆分 + xG/射门/机会质量；
- 低级别联赛：更强调阵容稳定、场地、连续客场与赛程密度；
- 杯赛：联赛状态只作背景，必须叠加轮换、总比分、晋级目标；
- 国家队正式赛：近期国家队窗口、可用名单、旅行和小组排列；
- 国家队友谊赛：实验性、换人、球员管理权重更高；
- 女足/青年/亚运：按同cohort历史和本届年龄/名单规则，不借用成年男子参数。

## 5. “控分”与“卖分”严格分离

### 可以建模的合法竞技策略
例如：

- 平局即可晋级；
- 两回合领先后主动降节奏；
- 已锁定名次后轮换；
- 为避免伤病而管理主力分钟；
- 小组末轮不同结果对应不同种子/对手。

这些只能作为`SPORTING_INCENTIVE / SCENARIO`变量，不称为比赛操纵。

### 非法比赛操纵
只有以下级别证据才允许升级Integrity状态：

- FIFA / UEFA /洲足联/足协官方；
- 执法机构或法院；
- 官方诚信部门；
- Sportradar/IBIA等专业诚信监测机构的公开告警、调查或已确认案件。

赔率异动、成交异常、媒体传闻、社媒爆料只能记录为：

- `MARKET_ANOMALY`
- `INTEGRITY_PUBLIC_RUMOR`

不能直接写“卖分”“假球”“操纵比赛”。

## 6. 市场异常规则

异常价格/成交只触发调查，不直接翻Primary。

必须至少记录：

- 1X2 / AH / O-U / BTTS时间序列；
- Correct Score可得时的back/lay/LTP；
- 市场总成交与runner成交；
- 来源、查询时间、更新时间；
- 是否存在跨市场一致性异常。

并保持：

```text
market anomaly != match fixing
matched volume != bettors count
odds movement != insider evidence
```

## 7. 数据库新增

```text
competition_context_requirements_v1
prematch_context_audits_v1
prematch_context_sources_v1
integrity_risk_registry_v1
context_scenario_taxonomy_v1
```

15个当前Competition Router赛事族均已写入各自的情境审计需求。

## 8. 固定情境标签

当前注册：

- MUST_WIN
- DRAW_ACCEPTABLE
- DEAD_RUBBER
- ROTATION_EXPECTED
- EXPERIMENTAL_SETUP
- FIXTURE_CONGESTION
- AGGREGATE_GAME_STATE
- MARKET_ANOMALY
- INTEGRITY_PUBLIC_RUMOR
- INTEGRITY_OFFICIAL_ALERT
- INTEGRITY_SANCTION_HISTORY
- LOW_INTENSITY_CONTROL
- OPEN_CASCADE_RISK

其中`MARKET_ANOMALY`和`INTEGRITY_PUBLIC_RUMOR`禁止单独作为比赛操纵结论。

## 9. 数据纪律

每个关键情境字段必须尽可能保存：

```text
source_url
source_tier
source_query_time
source_published_at
known_at
evidence_scope
evidence_text_hash
supports_field
```

若信息在预测cutoff之后出现，不得回填到赛前冻结。

## 10. 模型使用边界

- Context Audit可以改变候选排序、风险路径和不确定度；
- 不允许因为一条新闻手工给Football λ加减固定值；
- 只有经过cohort校准的变量才能进入数值参数；
- Integrity Alert本身也不是“必然反向下注”信号；
- 任何涉嫌比赛操纵的结论必须保持来源归属和不确定性。

## 11. 研究依据

- FIFA Match Manipulation / Betting Fraud Detection System；
- UEFA Anti-Match-Fixing / BFDS；
- Sportradar Integrity / UFDS；
- 赛事无关紧要时的激励变化研究；
- 赛程拥挤对体能、轮换和战术同步影响的系统综述。

## 12. 当前状态

```text
Competition Router     ACTIVE
Context Audit          ACTIVE_GOVERNANCE_SHADOW
Champion               unchanged
Football λ             unchanged
New prediction Gate    0
Formal 500+200         not restarted
```
