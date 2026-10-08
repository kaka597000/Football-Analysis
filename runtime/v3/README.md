# WCPP云端候选0.3：有来源的赛前情境

这是独立研究候选。Champion V21.5、Selector4.1-RP1、Regime-MT-KL、永久λ及正式500+200不改。0.3复用0.2的攻防模型，在其外围接入可训练、可审计的上下文流程；没有宣称比分70%、方向80%已经实现。

## 已可运行

- 双方实力：积分/场、当前主客场进失球/场。原攻防模型保留对手强度校正。
- 战意：有证据的争冠/资格/保级/杯赛/中游目标、目标分差与剩余场数、平局接受条件；缺失时不根据赔率或排名猜标签。
- 赛程：至少覆盖前14天/后7天全部赛事，过去与未来场次、前次开球间隔（上限336小时）。这里只计算开球间隔，不冒充实际终场后的休息时间。
- 阵容：L1官方首发、缺失常规主力及关键攻防人员。未查到伤停不等于无人缺阵。
- 战术/场地/旅途/天气：带来源的审计；尚无可验证数值特征的项目不设任意乘数。
- 特征来源及查询/已知/发布时间校验；未来已公布赛程可使用，未来赛果不可使用。
- 只有完整特征契约、L1证据、兼容并独立验证的上下文系数才修正数值。否则输出真实基线矩阵及OBSERVE，不零填缺失值、不人工改Primary。
- `RESEARCH_ELIGIBLE`表示研究冻结条件齐全，不表示概率高、正式推荐或目标准确率达标。
- 不可覆盖的研究冻结、内容重现及矩阵篡改拒绝。
- 90分钟验收，精确比分/胜平负、大小球全赢/半赢/走盘/半输/全输分列；分母、未结算、覆盖率及Wilson95区间明确。分组相关性与新独立样本仍须另做验证，区间不证明晋级。

## 运行

环境已具备Python、numpy和scipy，依赖沿用../v2/requirements.txt，无新增服务或密钥要求。

```bash
OPENBLAS_NUM_THREADS=1 python -m unittest discover -s runtime/v3 -p 'test_*.py' -v
python runtime/v3/cli.py audit FIXTURE.json --out NEW_AUDIT.json
python runtime/v3/cli.py predict MODEL.json FIXTURE.json --out NEW_DRAFT.json
python runtime/v3/cli.py fit-context REAL_SAMPLES.json --out UNVALIDATED_CONTEXT.json
python runtime/v3/cli.py validate-context REAL_SAMPLES.json --out CONTEXT_VALIDATION.json
python runtime/v3/cli.py freeze MODEL.json NEW_DRAFT.json --directory RESEARCH_FREEZES
python runtime/v3/cli.py evaluate RESEARCH_FREEZES/*.json --results RESULTS.json --eligible-count ELIGIBLE_COUNT --out REVIEW.json
```

每次使用新输出路径，不覆盖旧截止时间。审计记录原输入，新预测保存派生输入和完整矩阵。0.3不接受手填context_features；必须从context_audit派生。

模型使用0.2的独立赛事族产物（selected_family、temperature等）；经过验收后可将上下文产物置于context_coefficients。没有真实样本时训练/验证会拒绝，不以测试用合成数据生成交付参数。

## Fixture字段

原字段：match_id、cohort、home、away、phase=PreMatch、cutoff_at、kickoff_at、total_line、sources、identity_verified。

新增context_audit.home及context_audit.away，各有以下六块。每块evidence包括https来源、source_tier=L1/L2、supports_field对应块名称、query_time，可另登记known_at/source_published_at，均不得晚于该查询。

| 块 | 必要字段 |
|---|---|
| strength | competition=cohort, games, points, venue_games, venue_goals_for, venue_goals_against |
| motivation | objective=title/qualification/relegation/cup/midtable, points_gap_to_target（目标阈值减当前积分，可为负）, remaining_matches, draw_acceptable布尔值 |
| schedule | coverage.from/to/all_competitions_verified，matches列表；每条有match_id/kickoff_at/competition/status/evidence |
| availability | lineup_confirmed、missing_regular_starters、missing_key_attackers、missing_key_defenders，L1 evidence和supports_field=lineup的L1 lineup_evidence |
| tactics | verified=true、matchup_summary |
| integrity | verified=true、venue、travel_summary、weather_summary |

motivation对杯赛也不能凭标签硬填联赛目标分差：无法构造有意义且可验证的该字段时保持缺失，等待杯赛特征契约；不跨赛事族硬套。

schedule中的finished仅能用于截止前已结束比赛；scheduled用于未来已公布比赛。历史资料只覆盖联赛时all_competitions_verified必须为false。暂停/改期/未结束的中间场次需要核实，不能默认已有充分休息。

market_conflict=true会阻止研究冻结。该旗标须由有来源的检查设置；不是自动行情扫描器或已验证市场收益策略。当前市场数值重排依然遵循0.2的独立验收规则。

## 训练样本

REAL_SAMPLES每条必须包含fixture（当时完整赛前证据）、cohort/kickoff_at/cutoff_at、baseline_observed_at、baseline_home/away、home_goals/away_goals、score_basis=regulation_90、result_observed_at。结果不能早于90分钟或晚于当前时刻。重复赛事、不完整审计、未来来源及不一致时间拒绝。

训练至少100条真实样本；验证至少200条并按日期分段，且训练时实际知道的结果必须早于验证截止。沿用0.2的Top1/log loss/方向非退化联合门槛，样本量门槛不是目标准确率证明，也不是正式500+200重新注册。

## 当前真实资料验证

2026-10-07六场竞彩：五场有6个可观察实力特征，芬超缺完整场地分层资料；所有场次首发、完整赛程、战意证据或独立身份核验不足，因此全部OBSERVE，研究冻结拒绝。基线矩阵均与原稿相同，原预测保留。真实合格上下文训练样本0，数值修正尚未启用。

synthetic测试只验证程序行为，不用于训练实际模型或宣称预测准确率。
