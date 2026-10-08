# WCPP 新主模型 1.0

独立可运行的 `WCPP-MAIN-1.0`，不依赖无法恢复的 Champion 数值参数。用于当前研究执行；原 Champion V21.5 登记、永久 λ 和正式验证口径保留。当前正式 KPI 新增为零，未重新注册 500+200。

## 算法与启用规则

- 七个固定候选：180 天衰减收缩基线、365/180 天对手调整攻防、Dixon–Coles、双变量泊松、学习近期状态的 180 天攻防模型、基线与短期攻防的等权混合。
- 近期状态使用最近六场同赛事进球、失球、积分及比赛日期间隔，以四场先验收缩。日期间隔不冒充跨赛事休息时间，未包括的杯赛不视为不存在。
- 近期状态系数通过五个扩展时间折生成的真实比赛目标学习；每个目标的实力模型和特征均来自之前日期，排除同日赛果。修正最多 ±0.35 对数进球率，不使用手填战意权重。
- 历史按日期分为初始 70%、选择 10%、测试 20%；滚动月度重训。只在选择段选择候选，测试段仅用于接受或拒绝该候选，不重新挑选其他候选。
- 选择门槛要求比分命中提升且比分 LogLoss 不明显恶化。接受门槛要求比分提升的日期聚类 bootstrap 95% 区间下界大于零、比分 LogLoss 和胜平负 Brier 不劣于基线。未通过时执行基线。
- 同一比分矩阵产生第一比分、Top3、胜平负与实际盘口结算概率。第一比分是足球统计模型的条件众数，不由市场最高概率比分直接决定。
- 概率未证明经过未来校准，所有输出 `OBSERVE`，不声称目标准确率已经达到。

## 综合因素

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

未来比赛至少提供 `match_id/cohort/home/away/phase/cutoff_at/kickoff_at/sources`，研究冻结另要求 `identity_verified=true`。详细 `context_audit` 合同见 0.3。手工 `context_features/total_line/market_quote/date` 禁止覆盖。

大小球只接收 `total_quote`：`verified=true`、匹配的 `match_id`、机构 `bookmaker`、HTTPS `source_url`、`query_time`、真实 `line` 和明确 `market=asian_total|standard_total`。可附 `quoted_at`；缺少报价时刻标记 UNKNOWN，不声称实时或亚洲主盘。没有报价则 `total=null`，不得回退到 2.5。当前没有市场数值重排，方向比较是等赔率结算余额，不是投注收益计算。

## 留档与复盘

研究冻结允许保存 OBSERVE 预测，以完整记录误差；不等于正式 Primary 冻结，也不等于下注推荐。留档保存输入、模型与源码 hash、完整矩阵、实际报价来源、综合审计、状态及时间，不可覆盖，并重新执行核对所有实质字段。

复盘只接收有来源的 90 分钟比分；排除加时、点球，报告完整赛事人口、留档覆盖率、待结算、比分和胜平负 Wilson 区间，以及大小球全赢/半赢/走盘/半输/全输。不能把半赢计成完整命中，也不能把精选覆盖率隐去。

## 2026-10-08 的真实诊断

使用已经研究过的 6,748 场公开历史，属于开发诊断，不是新独立测试或未来验证。四个分组均未通过候选接受门槛，当前执行模型均为基线。真实参数摘要在 `models/main-v1/`，完整历史与可运行训练产物保存在云端 `reports/main-v1-release/`，不提交数据库或原始附件。

英超选择出的候选在滚动测试命中 55/474，基线 57/474；改进未获证明。详情见 `docs/main-v1-validation.md`。
