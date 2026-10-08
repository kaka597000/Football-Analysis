HELLO BOSS

## 2026-10-07：云端候选0.3研究入口更新

按用户本轮授权，后续新增研究预测使用runtime/v3/cli.py的上下文审计流程。先核实双方实力、战意、全部赛事赛程、官方阵容、战术和场地/旅行/天气；完整证据与独立验证的上下文参数齐备后才启用数值修正。缺失则OBSERVE并拒绝研究冻结。比分70%、胜平负/大小球80%是目标，未实现。

实现与命令见runtime/v3/README.md，真实六场验证见reports/model-upgrade-v3/real-audit-results.json。当前合格上下文训练样本0，数值修正禁用；原基线矩阵与预测全部保留。该更新是独立候选执行链优化，不升级Champion、不改永久λ、不替换Selector或Shadow、不重注册500+200，正式Primary KPI新增0。

以下为保留的原始交接正文与历次状态。

---

HELLO BOSS

# 本云端任务有效入口

来源：已读取分享正文及下载的11份原始资料。以下仅覆盖分享正文中的旧API暂停条款，其余模型约束不变。原始资料在 sources 中保留。

- 时间：UTC+8。唯一KPI：未来冻结Primary精确比分Top1。
- Champion V21.5不变；Selector4.1-RP1为Challenger；Regime-MT-KL仅Shadow。
- 永久λ不改；500+200未重新预注册。
- 没有实际矩阵不得补造概率或追认正式冻结。
- API最新启用授权覆盖旧PAUSED；缺凭据不代表用户未授权。
- 本文所载数据库哈希、计数和旧实验成绩仍是历史登记，本轮未实测或重跑。
- 10月6日12场逐场预测未包含在分享正文中；只读取了其状态说明。

---

HELLO BOSS

已整理成下面这份**《WCPP_统一主文档_2026-10-06.md》最新版正文**，合并9月29日核心资料、10月4日交接增量及10月6日复盘修正。

**文件写入环境目前报错，未能生成下载文件或写回项目来源。** 以下正文可以完整复制保存；数据库仍沿用9月29日Canonical版本，本次没有修改数据库或升级Champion。

---

# WCPP 统一主文档｜2026-10-06

**整理截止：2026-10-06 21:37，Asia/Taipei（UTC+8）**  
**性质：规范、研究状态、复盘增量与资源索引的统一整理**  
**版本更新不等于模型升级。**

## 1. 唯一最高目标

提高未来真实冻结样本的 **Primary精确比分Top1命中率**。

1X2、亚洲让球、大小球、BTTS、半全场、Pair@2、Top3/5、Distance用于特征、风险校验和模型诊断，不能掩盖Primary失败。

必须区分三种记录：

| 记录类型 | 判定条件 |
|---|---|
| 完整数值预测 | 实际运行模型，保存完整矩阵、参数版本、输入来源与冻结记录 |
| 聊天前瞻研究 | 赛前给出预测，但缺少完整矩阵或运行记录 |
| 赛后研究诊断 | 已接触赛果，用于发现问题，不计作新规则前瞻验证 |

## 2. 当前模型身份

| 模块 | 当前状态 |
|---|---|
| Champion | **WCPP V21.5 Adaptive Fusion，未升级** |
| 正式运行框架 | **WCPP V21.5-R2＋TFA Core 3.2** |
| 强制统计基线 | **Simple Poisson Strong Baseline** |
| 比分选择层 | **Score Selector 4.1／4.1-RP1，Challenger** |
| 过程层 | **MPF 0.1** |
| 冷门层 | **URE 0.2＋ISP0.3-GATE** |
| Competition Router | **WCPP_COMPETITION_ROUTER_0.1** |
| Context Audit | **WCPP_PREMATCH_CONTEXT_MOTIVATION_INTEGRITY_AUDIT_0.1** |
| LIVE | **Live Game-State 3.2，独立执行** |
| Regime-MT-KL | **SHADOW_ONLY／DO_NOT_PROMOTE** |
| 永久Football λ | **未修改** |
| 全局NB离散度 | **未升级** |
| Formal 500+200 | **尚未重新预注册** |
| 正式验证样本 | **登记状态FORMAL_VALIDATION＝0** |
| 5DollarFootballAPI | **用户已重新授权启用；当前缺少凭据，接入验证待完成** |

Router与Context的“ACTIVE”指执行治理规则已生效。专题文件将其标记为`ACTIVE_GOVERNANCE_SHADOW`，**不代表15个赛事族都已完成独立参数训练与验证**。

## 3. 当前正式赛前执行链

```text
Data Integrity／Match Identity
→ Competition Router
→ PreMatch Context／Motivation／Integrity Audit
→ Cohort-specific Strength Prior
→ Home Advantage／TFA／Matchup／SSR／Availability／MPF
→ Dynamic Football λ
→ Four-State／Poisson／Dixon–Coles／Negative Binomial
→ 完整比分矩阵：0-0至7-7，共64格＋8+尾部
→ Tail／Zero／URE审计
→ Market／MFI bounded rerank＋相关证据去重
→ Score Selector 4.1-RP1
→ Primary／Alternative
→ Regime-MT-KL Shadow
→ 保存完整Payload＋matrix_hash_sha256
→ 合法赛前Freeze
```

基础矩阵与最终输出均须保留版本。开赛后不得修改原赛前预测。

LIVE必须先确认分钟、当前比分和比赛状态，再分析剩余λ；不能与PreMatch成绩混算。

## 4. Competition Router

固定一级赛事族：

```text
MEN_TOP5_LEAGUE
MEN_OTHER_TOP_FLIGHT
MEN_LOWER_DIVISION
MEN_DOMESTIC_CUP
MEN_CONTINENTAL_CLUB
MEN_NATIONAL_COMPETITIVE
MEN_NATIONAL_FRIENDLY
MEN_ASIAN_GAMES
WOMEN_ASIAN_GAMES
MEN_YOUTH_U23
MEN_YOUTH_U20_U17
WOMEN_NATIONAL_COMPETITIVE
WOMEN_NATIONAL_FRIENDLY
WOMEN_CLUB_LEAGUE
WOMEN_YOUTH
```

以下参数至少按赛事族独立估计或层级收缩：

- 实力先验与时间衰减；
- 主场优势；
- 离散度；
- Total Regime；
- Margin分布；
- Score-Rank校准；
- 阵容与轮换敏感度；
- 市场可靠度；
- 杯赛阶段、回合和总比分；
- 友谊赛实验性。

样本不足采用partial pooling，不硬复制五大联赛参数。

青年、亚运赛事必须读取当届年龄与超龄规则。无法可靠分类的比赛进入`UNCLASSIFIED_QUARANTINE`，不得正式冻结。

## 5. 每场Context Audit

正式分析前自行查询并结构化：

| 领域 | 必查内容 |
|---|---|
| 状态 | 合适窗口内近期表现、主客差异、对手强度 |
| 过程 | 可得的xG、射门、机会质量 |
| 战意 | 积分、晋级、争冠、保级、种子位的数学状态 |
| 赛制 | 单回合／双回合、总比分、中立场、90分钟口径 |
| 阵容 | 首发、替补、伤停、停赛、实验阵容 |
| 体能 | 休息、连续客场、赛程拥挤、旅行与时区 |
| 公开信息 | 教练、球员、足协、俱乐部表态 |
| 路径 | 控节奏、追分、双向得分、开放级联风险 |
| 异常 | 市场异常与公开可核证诚信信息 |

战意标签包括`MUST_WIN／DRAW_ACCEPTABLE／DEAD_RUBBER`等，不能用媒体“战意强烈”替代数学状态。

合法控节奏、总比分管理、轮换属于竞技策略。赔率异动或传闻不等于比赛操纵。

## 6. 数据源与来源记录

竞彩身份优先雷速体育、新球体育竞足板块；失败立即切换可靠第三方，不死守单一网站。

禁止访问中国体育彩票官方网站及镜像。

| 等级 | 来源 |
|---|---|
| L1 | 联赛、赛事、足协、俱乐部官方 |
| L2 | 高可信赛事数据平台 |
| L3 | 媒体前瞻、预计阵容、二手聚合 |

每个关键字段保存：

```text
source_url
source_tier
query_time
source_published_at／update_time
known_at
supports_field
evidence_scope
```

缺失保留`NULL／NOT_AVAILABLE`。预计阵容不得冒充官方首发，赛后信息不得回填赛前输入。

**所有比赛盘口由助手自行查询**，尽量获取1X2、亚洲让球、大小球、BTTS；正确比分及交易数据可得时追加。

盘口记录必须区分亚洲盘、三项让球、替代盘口和半场盘口。没有原始时间序列，不宣称升盘、降盘或资金流向。

## 7. Football分布与Primary纪律

必须保存：

```text
raw_mode
raw_mode_probability
structural_primary_candidate
structural_candidate_probability
primary
alternative
same_as_mode
primary_selection_basis
override_reason
```

执行规则：

1. 没有足够独立Football证据，不Override Raw Mode。
2. 市场单独不得改变Primary。
3. 未校准阵容叙事不得手工给λ加减固定值。
4. Top2 Diversity只影响Alternative。
5. 修改Primary、Alternative或Top3，必须记录新证据、修改前后及原路径落选原因。
6. 替补核心仍属于90分钟可用资源，不因未首发就视为整场缺席。
7. 没有实际运行矩阵，不报虚构概率、排名、量化SSI或矩阵哈希。

既有机制继续沿用：Tail Expansion、Zero-Score Reentry、Draw-State Promotion、Tail Symmetry、Confirmed Lineup Source Gate、Poisson Anchor Protection及URE相关检查。具体阈值以对应实现为准，不从模块名称猜测。

## 8. 当前主要瓶颈

已读取资料的主要诊断是：

**合理概率分布到单一Primary的排序损失，尤其CENTER_COMPRESSION／TWO_TAIL_MISS。**

9月29日文档中的30条诊断样本：

| 指标 | 文档登记值 |
|---|---:|
| Raw矩阵期望总球 | 约2.633 |
| 实际总球 | 约2.533 |
| Raw Mode总球 | 约1.600 |
| Primary总球 | 约1.767 |
| Raw Top1 | 10.00% |
| Raw Top3 | 33.33% |
| Raw Top5 | 50.00% |
| Raw Top10 | 80.00% |

这些属于Development／Shadow诊断，部分历史`known_at`存在缺口，不能当正式防泄漏验证。

**Top10覆盖80%不等于Primary命中率80%。**

也不能把所有失败都归为Selector：若方向、Margin和Total同时失真，应检查分布及输入层。

## 9. 重点研究方向

| 方向 | 研究内容 |
|---|---|
| 友谊赛独立离散度 | 轮换、实验阵容、换人、防守连续性、首球级联、落后进攻 |
| 杯赛OPEN_CASCADE | 将有证据的开放路径传递到Regime与比分Rank |
| 极端实力差Margin Tail | 改善大胜幅分布，不直接抬总λ |
| ZERO_SCORE竞争 | 0-0与1-0／0-1的比较，复用已有机制 |
| 邻域与Rank Head | 总球、胜幅、比分邻域共同参与终选 |
| 90分钟阵容 | 首发与替补攻击资源、不同比分状态下可用组合 |
| 比分稳定性 | 阵容与场景扰动下的候选竞争 |

淘汰赛不能机械套LOW或FAVORITE_LEAD_CONTROL；比赛双方仍可能形成双向高尾。

上述研究方向不等于已训练完成，也不构成Champion升级。

## 10. Regime-MT-KL影子分支

```text
Frozen Base Matrix
→ Total Regime：LOW 0–1／CENTER 2–3／HIGH 4+
→ Signed Margin：D0／H1／A1／H2／A2／H3+／A3+
→ KL／IPF最小改动投影
→ 64格重排
→ Shadow Primary
```

已读专题登记：1751条五大联赛历史Replay；训练1025条，时间隔离测试726条。

| 分支 | Top1 | Top3 | Top10 | Score LogLoss |
|---|---:|---:|---:|---:|
| Raw Poisson | 13.09% | 32.09% | 78.79% | 2.8838 |
| Total＋Margin | 13.22% | 31.54% | 77.82% | 2.8936 |

结论仍是**DO_NOT_PROMOTE**。原文报告提升约0.14个百分点，显示百分比有四舍五入。

全局mean-preserving离散度实验最大状态权重99.9053%，近乎退化为单状态。后续只研究competition／cohort-specific参数。

本次整理没有重跑上述实验。

## 11. 每批Top3精确比分

固定流程：

```text
Market-Blind Score Probability
→ Structural Adjustment
→ Score Stability
→ Market Sanity Check
```

稳定性考虑：

- 单格概率；
- 同方向邻近比分质量；
- Margin与Regime稳定性；
- 零封／平局竞争；
- 比分熵；
- 场景冲突；
- 阵容不确定性。

没有完整概率与扰动评估时，明确称**定性Top3**，不得以强队胜率代替精确比分稳定性。

不足3场时按实际场数输出，不补造。

## 12. 复盘增量及边界

下表分批记录，不合并成新的累计胜率。

| 批次 | 已登记Primary成绩 | 来源与边界 |
|---|---:|---|
| 9月28日正式竞彩8场 | 0/8 | 已读9月29日统一台账 |
| 9月29日下午7场 | 3/7 | 已读晚间增量；Pair@2＝5/7 |
| 9月29日晚欧国联7场 | 0/7 | 用户10月4日交接；本次未重新逐场核证 |
| 10月1日3场 | 0/3 | 用户交接记录 |
| 10月2日3场 | 2/3 | 用户交接记录 |
| 10月3日2场 | 1/2 | 用户交接记录 |
| 10月5日欧国联7场最终版 | 0/7 | 已读10月6日独立复盘；Pair@2＝0/7 |

10月5日批次没有完整冻结矩阵，只能归为聊天前瞻研究。初版Pair@2为1/7，最终版为0/7，两版不得择优拼接。

该批最终Primary总球16，实际14，不能解释为整体低估总球。

其执行修正是：

1. 评估首发与替补的90分钟可用资源。
2. Alternative替换同样需要新增独立Football证据。
3. 同时审计零比分与高尾，但不机械升级候选。
4. 没有Raw矩阵，不断言某场属于已确认的Zero-score Rank Failure。
5. 保留版本、来源时间和修改理由。

这些比赛是修正发现集，不是修正后的验证成功样本。

### 尚未完成评价的记录

10月4日西乙三场仍保留原预测：

| 比赛 | Primary | Alternative |
|---|---:|---:|
| 希洪竞技—塞尔塔B | 1-0 | 1-1 |
| 拉斯帕尔马斯—巴拉多利德 | 1-1 | 1-0 |
| 卡斯特利翁—休达 | 2-0 | 3-0 |

其在交接时标为待赛。本次没有查询后续赛果，统一标记**结果待核验**，不继续称其当前未开赛。

10月6日12场聊天预测保留为独立的**首发前定性版本**，不补造矩阵、不追认正式Freeze。

## 13. Canonical数据库

仍为：

`WCPP_赛事数据库_统一合并版_2026-09-29.sqlite`

登记SHA256：

```text
8b83d0adb71cf2f4b26eab0acec29a235b84b4b24a5eed4c4475293bb736bcc0
```

登记大小：56,631,296字节。

| 表 | 9月29日审计登记数 |
|---|---:|
| matches | 3034 |
| results | 3032 |
| forecasts | 226 |
| score_matrices | 115 |
| score_matrices_v2 | 55 |
| evaluations | 224 |
| historical_matches_v1 | 8922 |
| recent_forecast_payloads_v1 | 55 |
| score_rank_evaluations_v1 | 30 |
| ingest_quarantine_v1 | 28 |
| national_team_regime_training_v1 | 197 |
| regime_shadow_replay_v1 | 726 |

历史赛事族映射8922/8922；15个赛事族已注册profile和context requirements。

审计登记的逐场Context表及未来Regime Shadow预测／评价表当时为0。注册表存在不能证明逐场运行已完成。

文档记载：

```text
PRAGMA integrity_check = ok
PRAGMA foreign_key_check = 0
```

**本次没有打开SQLite重新计算哈希或查询计数。以上均为历史审计登记，不是本轮实测。**

不得创建空库或旧库并命名成10月6日“最新合并库”。9月29日以后聊天增量未确认入库的，保持待合并状态。

## 14. 数据与代码范围

- WCPP主范围为已核实竞彩足球；用户点名的额外比赛单列Extended样本。
- 北单、其他独立模型、WorldLiveBall仿制项目不得自动并入WCPP成绩。
- 10月4日核查所见本地Poisson／DC代码，只能确认基线实现，不能据此宣称完整WCPP已经实现。
- 国家队公开数据若含加时，先进入Raw staging，规范化90分钟口径后才可训练。
- 行政判罚结果、口径不明结果保持隔离，不补造比分。
- 5DollarFootballAPI按用户最新授权重新启用；仅从环境变量 `WCPP_5DOLLAR_API_KEY` 读取密钥，禁止写入文件或日志。先验证 `/status`，竞彩请求固定 `types=jingcailottery`，权限未验证。

## 15. 日常流程与固定交付

| 时间／节点 | 工作 |
|---|---|
| 18:00 UTC+8 | 核实竞彩清单，全量赛前分析 |
| 21:00 UTC+8 | 更新剩余未开赛比赛，记录版本差异 |
| 官方首发出现 | Lineup Re-evaluation |
| 次日 | 核实90分钟赛果，按最后合法赛前版本复盘 |

以上是目标流程；**当前无人值守任务是否配置和运行成功，未在本次核验。**

每场交付至少包括：

身份、cohort、来源等级、近期状态、战意、阵容、体能、Matchup、λ、1X2、亚洲让球、大小球、BTTS、半全场、URE路径、Raw Mode、Primary、Alternative、最大失败路径、市场校验、数据缺口、矩阵与尾部、哈希、Shadow输出、Freeze状态。

不能取得或未运行的字段明确标缺失，不用文字叙事冒充数值结果。

## 16. 验证与晋级

研究修正可以继续执行；Champion升级必须满足：

1. Candidate与参数固定；
2. 未来赛果出现前预注册；
3. 输入`known_at`不晚于预测截止；
4. 时间隔离与未见样本；
5. 同场Raw基线对照；
6. Primary Top1稳定正Lift；
7. LogLoss／RPS不系统恶化；
8. 分赛事族评估；
9. 完整冻结记录。

正式500+200具体设计以重新预注册方案为准，不在本文件中补造。

## 17. 资料读取与版本优先级

后续按以下顺序使用资料：

1. 本次10月6日统一整理正文；
2. 9月29日Canonical数据库；
3. 9月29日统一复盘台账；
4. 9月29日晚间更新总结；
5. 10月4日项目核查与执行修正；
6. 10月6日欧国联独立复盘；
7. Router、Context、Regime专题说明；
8. 更早主文档、日志和原始预测作为历史审计。

用户之后的明确指令覆盖对应旧条款。旧预测记录、已知赛果与研究参数不得混成一个版本。

### 本次实际核对的关键资料

- `WCPP_统一主文档_2026-09-29.md`
- `WCPP_资源库核心索引_2026-09-29.md`
- `WCPP_模型变更日志_2026-09-29.md`
- `WCPP_复盘分析_统一台账_2026-09-29.md`
- `WCPP_数据库统一审计_2026-09-29.md`
- `WCPP_项目最新更新总结_2026-09-29.md`
- `WCPP_Competition_Specific_Model_Router_0.1_2026-09-29.md`
- `WCPP_PreMatch_Context_Motivation_Integrity_Audit_0.1_2026-09-29.md`
- `WCPP_Regime_Conditional_Margin_Total_Calibration_0.1_SHADOW_2026-09-29.md`
- `WCPP_项目核查与执行修正_2026-10-04.md`
- `WCPP_欧国联复盘_2026-10-06.md`
- 用户在本对话提供的10月4日交接及后续明确规则。

本次统一了规则入口与状态说明；没有逐张重读全部历史截图、逐条重验全部历史赛事，也没有修改原始记录。

## 18. 待完成事项

| 工作 | 当前状态 |
|---|---|
| 最新规则与复盘增量梳理 | 本正文已完成 |
| 可下载文件及项目来源写回 | 文件写入失败，未完成 |
| Canonical数据库实测审计 | 未完成 |
| 9月29日后增量入库 | 未确认完成 |
| 完整模型代码与参数对应核查 | 未完成 |
| 分赛事族训练与配对回测 | 本次未执行 |
| 未来完整矩阵Shadow积累 | 按后续实际冻结记录统计 |
| Champion晋级 | 未发生 |

**当前工作重点：恢复可审计的数据和模型运行链，再研究赛事族专属Regime、Margin、离散度与最终比分排序。**

## 当前聊天补充：数据源登记

当前数据源清单见同目录“数据源登记.json”，来自用户登记，不是本轮连接测试结果。除5DollarFootballAPI外，多数为公开网页查询，没有已配置API密钥。雷速与新球用于竞彩核实；赛事/足协/联赛/俱乐部官方为L1；FotMob/Sofascore为L2交叉核验。媒体为背景证据，不冒充官方首发。AgentBets报价需标注二手来源；PokerStars三项让球不能当亚洲盘。martj42国家队数据仍为Raw staging，核实90分钟口径后才能训练。Betfair交易所成交量、back/lay和正确比分交易数据尚无已验证连接或密钥，不得称已接入。市场仅用于最后校验和有界重排；体彩官网及镜像禁止访问。


## 本轮核查状态更新

以reports/模型恢复核查摘要.md为当前恢复能力清单。Canonical已实测审计；模型登记及部分研究参数已提取，但Champion原始数值产物缺失，未恢复完整推理。API密钥已生效，账户community，竞彩接口403 insufficient_plan；不得沿用旧密钥缺失状态。


## 云端Challenger 0.1已实现

用户确认历史主要以聊天方式执行，并授权本任务实现源码和配置。本轮新增runtime/wcpp.py、runtime/shadow.py、11项通过的测试及README运行说明。实现Poisson基线、同赛事族收缩拟合、输入校验、大小球/胜平负输出、研究冻结/复盘和Shadow核查；原V21.5 Champion身份保持不变，不宣称完整复原聊天模型。1751条Raw Mode与历史记录一致；726条历史测试95次Top1，LogLoss2.883828。Shadow重排723/726首选与旧记录一致，3场差异保留；未晋级。尚无真实比赛新冻结，500+200未重新预注册。以runtime/README.md及reports/cloud-runtime-delivery.json为实现边界，旧“无源码”的记录指历史产物缺失，不再表示本云端没有新实现。
