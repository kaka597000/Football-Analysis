# WCPP 新主模型 1.2

独立可运行的 `WCPP-MAIN-1.2`，不依赖无法恢复的 Champion 数值参数。用于当前研究执行；原 Champion V21.5 登记、永久 λ 和正式验证口径保留。当前正式 KPI 新增为零，未重新注册 500+200。

## 分析前的赛事分类

先执行 `competition_router.py`，再估计实力和比分矩阵。路由键同时包含赛事、男女、年龄、级别、俱乐部／国家队、赛事类型和阶段；同一赛事标签内部混入女足、青年、低级别或附加赛也会拒绝训练。已登记的32个明确赛事代码映射到成年男足；未知代码不默认男足，须提供有官方来源和时间戳的 `competition_classification`。女足、U17、U20、U23等分别训练与验证，不能直接复用成年男足参数。分类支持不代表已有这些类别的合格模型。

英超、西甲等联赛不绑定“对攻／传控”的固定倍率。每次分析保存同路由、赛前截止时间可见、此前730天的进球、双方进球和平局统计；不足60场标记样本不足。这些仅为描述性背景，赛果数据不能推出控球风格或xG。数值仍须通过原有选择／接受门槛，球队实际战术证据优先于赛事印象。

非普通联赛阶段增加官方赛制审计：阶段、单／两回合、第二回合前按本场主客顺序的总比分、客场进球规则、晋级局势、轮换、加时与点球规则，预测目标始终是90分钟。友谊赛另核换人和试阵；青年赛事另核该届年龄／超龄规则。缺少规则仍可保存标为OBSERVE的同路由研究矩阵，但禁止启用综合因素数值修正。未知赛事身份或中立场状态则停止预测。“剧本”不构成可用证据；只分析有来源的激励、规则和阵容变化。

```bash
python runtime/v4/cli.py classify fixture.json --out outputs/classification.json
```

`competition_rules` 字段及示例见 [分类升级说明](../../docs/competition-router-20261010.md)。旧1.0产物不能直接执行1.1；须从原历史重新训练并保留旧留档，不能仅修改版本或源码hash。

## 算法与启用规则

- 七个固定候选：180 天衰减收缩基线、365/180 天对手调整攻防、Dixon–Coles、双变量泊松、学习近期状态的 180 天攻防模型、基线与短期攻防的等权混合。
- 近期状态使用最近六场同赛事进球、失球、积分及比赛日期间隔，以四场先验收缩。日期间隔不冒充跨赛事休息时间，未包括的杯赛不视为不存在。
- 近期状态系数通过五个扩展时间折生成的真实比赛目标学习；每个目标的实力模型和特征均来自之前日期，排除同日赛果。修正最多 ±0.35 对数进球率，不使用手填战意权重。
- 历史按日期分为初始 70%、选择 10%、测试 20%；滚动月度重训。只在选择段选择候选，测试段仅用于接受或拒绝该候选，不重新挑选其他候选。
- 选择门槛要求比分命中提升且比分 LogLoss 不明显恶化。接受门槛要求比分提升的日期聚类 bootstrap 95% 区间下界大于零、比分 LogLoss 和胜平负 Brier 不劣于基线。未通过时执行基线。
- 同一比分矩阵产生第一比分、Top3、胜平负与实际盘口结算概率。第一比分是足球统计模型的条件众数，不由市场最高概率比分直接决定。
- 概率未证明经过未来校准，所有输出 `OBSERVE`，不声称目标准确率已经达到。

## 综合因素

第一推荐的业务定义及九类决策核查见 [第一比分综合决策规范](../../docs/primary-score-decision-contract.md)。`primary` 是为研究与复盘保留的矩阵首选格，并不自动代表综合推荐；输出增加 `primary_basis`、`primary_label` 与 `comprehensive_primary_ready`。目前后者固定为 false，防止把基线或六块因素修正冒充已完成九类综合决策。实际Top1命中率目标是70%，不是要求单场概率达到70%。

复用 0.3 的六块证据审计：实力、战意、跨赛事赛程、阵容可用性、战术和环境完整性。缺少数据保持未知，不能填零或直接改 λ。

`train-context` 从真实新主模型赛前研究冻结及其 90 分钟赛果提取特征。要求完整 L1 证据、未调整的兼容泊松基线、唯一比赛、有效时间戳；至少 200 个真实样本，按日期先训练后验证，只有通过既有比分、胜平负及大小球验证门槛的系数才能启用。该样本门槛不等于正式 500+200 预注册。战术和环境文本目前作为审计项，不宣称已有数值系数。

目前合格综合因素样本为零，数值融合未启用。接口完整不代表真实数据已具备。

## 运行

环境使用根目录 `requirements.txt` 的 NumPy/SciPy，无新增依赖。

```bash
# 输出目录必须为新的目录，避免覆盖之前的训练结果
OPENBLAS_NUM_THREADS=1 python runtime/v4/cli.py train data/EPL-history.json --directory outputs/main-v1/EPL
python runtime/v4/cli.py predict outputs/main-v1/EPL/artifact.json fixture.json --out outputs/draft.json
python runtime/v4/cli.py freeze outputs/main-v1/EPL/artifact.json outputs/draft.json --directory research-freezes/main-v1
python runtime/v4/cli.py review freezes.json results.json --population 20 --out outputs/review.json
python runtime/v4/cli.py train-context outputs/main-v1/EPL/artifact.json real-context-samples.json --out outputs/context-artifact.json
python scripts/run_tests.py
```

历史输入沿用 0.2 的历史 JSON 合同，包括比赛身份、赛事分组、ISO 日期、90 分钟比分、来源 URL 与实际采集时间。未来预测需要模型构建和数据观察时间不晚于预测截止时间，历史日期早于开赛 UTC 日期，球队必须在模型中存在。源码 hash 不一致须重新训练。

未来比赛至少提供 `match_id/cohort/home/away/phase/cutoff_at/kickoff_at/sources/neutral`，其中 `neutral` 必须为明确布尔值；未知赛事另附正式分类证据，非普通联赛阶段附 `stage/competition_rules`。研究冻结另要求 `identity_verified=true`。详细 `context_audit` 合同见 0.3。手工 `context_features/total_line/market_quote/date` 禁止覆盖。

大小球只接收 `total_quote`：`verified=true`、匹配的 `match_id`、机构 `bookmaker`、HTTPS `source_url`、`query_time`、真实 `line` 和明确 `market=asian_total|standard_total`。可附 `quoted_at`；缺少报价时刻标记 UNKNOWN，不声称实时或亚洲主盘。没有报价则 `total=null`，不得回退到 2.5。当前没有市场数值重排，方向比较是等赔率结算余额，不是投注收益计算。

## 留档与复盘

研究冻结允许保存 OBSERVE 预测，以完整记录误差；不等于正式 Primary 冻结，也不等于下注推荐。留档保存输入、模型与源码 hash、完整矩阵、实际报价来源、综合审计、状态及时间，不可覆盖，并重新执行核对所有实质字段。

复盘只接收有来源的 90 分钟比分；排除加时、点球，报告完整赛事人口、留档覆盖率、待结算、比分和胜平负 Wilson 区间，以及大小球全赢/半赢/走盘/半输/全输。不能把半赢计成完整命中，也不能把精选覆盖率隐去。

## 2026-10-08 的真实诊断

使用已经研究过的 6,748 场公开历史，属于开发诊断，不是新独立测试或未来验证。四个分组均未通过候选接受门槛，当前执行模型均为基线。真实参数摘要在 `models/main-v1/`，完整历史与可运行训练产物保存在云端 `reports/main-v1-release/`，不提交数据库或原始附件。

英超选择出的候选在滚动测试命中 55/474，基线 57/474；改进未获证明。详情见 `docs/main-v1-validation.md`。

## 1.2：研究包方法落实

本次增加九类证据记录审计、5Dollar赛事调用审计及读取入口、历史概率与残差诊断、方法知识索引。不会由提示词或平台摘要直接改变比分矩阵。详细合同与启用边界见 [1.2 升级说明](../../docs/main-v1-2-upgrade.md)。

```bash
python runtime/v4/cli.py audit-decision fixture.json --out outputs/decision-audit.json
python runtime/v4/cli.py fetch-feed --league-id VERIFIED_PROVIDER_LEAGUE_ID --start-time START_UNIX --end-time END_UNIX --out outputs/feed.json
python runtime/v4/cli.py diagnose saved-prematch-forecasts.json sourced-results.json --population 20 --out outputs/diagnostics.json
python runtime/v4/cli.py lookup-research --theme T09 --out outputs/research-reference.json
```

读取器先验证 `/status`，再使用既有已验证的 `/leagues/{id}/fixtures` 协议，逐页保存无密钥响应。联赛ID必须从真实供应商登记核实；每页 `has_more=true` 必须继续请求下一页，不能称覆盖完整。读取不自动证明比赛匹配，也不自动入库或进入模型。上例占位值须替换为整数。

`decision_assessment` 是证据元数据和覆盖审计，不能证明叙述正确；即使18块文档齐全，`comprehensive_primary_ready` 仍为 false。`source_calls` 记录取得、用于分析、进入模型三个阶段，不能把 `/status` 当赛事数据。缺调用或缺证据会列入 OBSERVE 原因；允许研究留档记录缺口，不计正式KPI。

1.0/1.1产物不能直接运行1.2。按新源码重新训练，禁止改旧产物的版本/hash冒充重建；旧预测、矩阵、复盘与源码快照保留。
