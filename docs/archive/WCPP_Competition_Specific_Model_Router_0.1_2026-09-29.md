# WCPP Competition-Specific Model Router 0.1

**版本日期：** 2026-09-29  
**模型/治理ID：** `WCPP_COMPETITION_ROUTER_0.1`  
**状态：** `ACTIVE_GOVERNANCE_SHADOW`  
**核心纪律：** 禁止把单一赛事模板/参数组跨性别、年龄层、级别、赛制、国家队/俱乐部直接复用。

## 1. 路由位置

```text
Data Integrity / Identity
        ↓
Competition Router
        ↓
Cohort-specific Strength / Home Advantage / Dispersion / Regime / Margin / Rank
        ↓
WCPP Football Matrix
        ↓
Score Selector / Shadow Branches
```

比赛在进入TFA/Strength Prior前必须先完成赛事族识别。

## 2. 当前一级赛事族

- MEN_TOP5_LEAGUE：五大联赛
- MEN_OTHER_TOP_FLIGHT：其他男子顶级联赛（J1/K1/A-League等）
- MEN_LOWER_DIVISION：男子低级别联赛（J2/K2等）
- MEN_DOMESTIC_CUP：国内杯赛/联赛杯
- MEN_CONTINENTAL_CLUB：UCL/ACLE等洲际俱乐部赛事
- MEN_NATIONAL_COMPETITIVE：成人男子国家队正式赛
- MEN_NATIONAL_FRIENDLY：成人男子国家队友谊赛
- MEN_ASIAN_GAMES：亚运男足，按届次年龄/超龄规则读取元数据
- WOMEN_ASIAN_GAMES：亚运女足独立族
- MEN_YOUTH_U23：男子U23
- MEN_YOUTH_U20_U17：男子U20/U17（样本充足后继续拆分）
- WOMEN_NATIONAL_COMPETITIVE：成年女足国家队正式赛
- WOMEN_NATIONAL_FRIENDLY：成年女足国家队友谊赛
- WOMEN_CLUB_LEAGUE：女足俱乐部联赛
- WOMEN_YOUTH：女足青年赛事

## 3. 各赛事族不得共享的核心参数

以下参数至少按cohort独立估计或层级收缩：

1. Strength prior/time decay
2. Home advantage
3. Goal dispersion / NB or other variance terms
4. LOW/CENTER/HIGH Total Regime
5. Margin distribution
6. Score-Rank calibration
7. Lineup/rotation sensitivity
8. Market calibration reliability
9. Cup stage / first-leg / second-leg / aggregate-state
10. Friendly experimentation/rotation state

禁止直接把五大联赛参数复制到女足、青年、低级别、杯赛或国家队。

## 4. Cup / Tournament特殊状态

杯赛必须额外保存：

- single_leg / two_leg
- leg_number
- aggregate_score_before_match
- neutral_venue
- extra_time_possible
- rotation_risk
- qualification_state

友谊赛必须额外保存：

- friendly=true
- expected_rotation
- substitution_regime
- experimentation_state
- travel/timezone

青年/亚运赛事必须保存该届实际年龄资格规则，不从名称猜测。

## 5. 当前数据库映射

09-29 Canonical历史8922条已经完成exact-competition路由：

- MEN_TOP5_LEAGUE：5161
- MEN_OTHER_TOP_FLIGHT：1317
- MEN_LOWER_DIVISION：1190
- MEN_CONTINENTAL_CLUB：808
- MEN_NATIONAL_COMPETITIVE：232
- MEN_DOMESTIC_CUP：214

当前所有8922条历史均已有cohort assignment；没有修改历史原始行。

## 6. 参数治理

- 参数不足：使用层级贝叶斯/partial pooling向相邻cohort收缩。
- 样本充分：独立估计。
- 禁止：数据不足时直接套用五大联赛固定参数。
- 新赛事：先路由，再分析；不能识别则进入`UNCLASSIFIED_QUARANTINE`，不得正式冻结Primary。
- Champion和永久Football λ不因本Router自动改变。

## 7. 研究依据

不同赛事环境存在可测差异，支持赛事族分层：

- 男女欧洲联赛主场优势存在系统差异：
  https://www.tandfonline.com/doi/abs/10.1080/17461391.2011.651490
- U17/U20/Senior国家队技术战术表现随年龄层变化：
  https://pmc.ncbi.nlm.nih.gov/articles/PMC12289234/
- 英格兰四级联赛主场优势存在分级差异：
  https://pmc.ncbi.nlm.nih.gov/articles/PMC8152196/
- 两回合淘汰赛存在次回合主场/赛制状态效应：
  https://academic.oup.com/jrsssa/article/181/4/1009/7072053
- 全球不同地区/国家主场优势存在显著地域差异：
  https://www.tandfonline.com/doi/abs/10.1080/02640410500141836

## 8. 与Regime-MT-KL关系

`WCPP_REGIME_CONDITIONAL_MARGIN_TOTAL_0.1_SHADOW`今后不再训练一个全局Regime。

正确路径：

```text
Competition Router
   ↓
Cohort-specific Regime / Margin
   ↓
KL/IPF projection
   ↓
Exact Score Rank
```

现有五大联赛Development结果仅代表`MEN_TOP5_LEAGUE`研究，不得外推到其他cohort。
