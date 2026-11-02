# 每场赛事完整模块分析

2026-11-02用户要求：截图中的八类模块全部进入每场分析。默认predict_active.run/CLI输出match_analysis，CLI可用--report生成八模块Markdown；原JSON和冻结仍包含完整输出。报告是证据和执行状态汇总，不自动生成综合推荐或改变已训练权重。

```bash
python runtime/predict_active.py /absolute/fixture.json --registry /absolute/registry.json --out /absolute/NEW-pair.json --report /absolute/NEW-analysis.md
```

输出与报告文件均不能已存在。CLI仍按原规则保存current/test配对冻结；综合Primary、方向、真实盘口大小球和TOP3仍需基于报告、候选比较及反证独立冻结。阵容未公布不自动拒绝，水位变化重点核查；不得把statistical baseline当作综合第一比分。

## 每场必须覆盖的八类证据

1. 历史进失球、球队攻防、主客场：保留两模型身份/实际训练截止及单队均值、0球/2+/3+概率和区间，检查最新完赛是否纳入。
2. 近期状态与比赛间隔：近况与对手强度分开；同联赛间隔不等于完整体能。
3. 射门、射正、控球：实际过程事实与工件训练覆盖分别列，不把新采集数据冒充已经训练。
4. xG/xGA：同提供方、明确窗口/主客/对手/时点；缺失保持未知。工件feature_xg_match_count=0与最新现场有xG证据可以同时成立。
5. 阵容、伤停、停赛：确认、预计、未公布分开；人员不全时做轮换情景，不给阵容强行设权重。
6. 战意、旅行、全赛事负荷：数学战意、前14天/后7天全赛事、休息/旅行逐项记录；采集覆盖不完整必须注明。
7. 盘口、水位：优先核查同机构同线真实时点的连续变化，升降盘另列；缺quoted_at不能用query_time替代。
8. 研究旁路：展示实际五角色资料、独立冷门分类器及候选、比分选择实验、分钟模拟的执行或拒绝状态；不把资料包说成五个LLM已运行，也不自动融合概率。

## 本场证据输入

采集按现有API优先流程执行；源数据经过主代理核查后填写fixture.analysis_modules。允许的key为history/form/shots/xg/lineup/context/market/research。每个块的契约：

```json
{
  "match_id": "SYNTHETIC_ONLY",
  "status": "partial",
  "as_of": "2026-01-01T12:00:00Z",
  "summary": "可核实的事实与缺项说明",
  "facts": {"provider": "EXAMPLE", "window": "explicit competition and dates"},
  "sources": [{"source_url": "https://example.test/source", "known_at": "2026-01-01T10:00:00Z", "query_time": "2026-01-01T11:00:00Z"}],
  "missing": ["explicit unresolved field"]
}
```

status为available/partial/scenario/unknown/conflict之一；前三者必须有facts与来源。scenario还需scenarios及uncertainty。来源known_at≤query_time≤块as_of≤预测cutoff；比赛ID必须一致。unknown不能伪装取得数据，错误来源或凭据字段只显示invalid且不回显。available仅指该块提交的证据可核查，不证明所有字段齐全或模型已数值使用。默认不自动发网络请求，不把空模板称为完成八模块采集。

## 分钟模拟调用

可提供fixture.minute_simulation_request，含artifact/state/regulation_minutes及可选state_artifact。调用现有minute_simulator.simulate，固定20000次、seed=0，与同场cohort及cutoff绑定；赛前只接受minute=0。artifact必须符合WCPP-MINUTE-RATES-1且引用真实来源文件/hash。状态修正工件需通过原有训练覆盖和时间验证；红牌交互未训练仍拒绝。不提供输入则NOT_CONFIGURED。基础Poisson模拟只作研究诊断，不证明分钟状态模型已训练。

本次完成统一输出、证据输入校验、研究引擎入口与报告。并未为所有联赛补齐真实数据、训练全部缺失模块、接通五个外部LLM或证明命中率提高。

验收：358项测试，无失败，1组私有历史集成检查因缺文件跳过；真实活动英超工件加载、八模块报告和20000次基础分钟模拟已执行。current/test矩阵与改动前同输入结果逐元素一致。分钟状态修正仍NOT_TRAINED，测试输出单独留档不作为新赛事推荐。新增真实工件代码哈希回归测试，防止独立规则变动阻断旧工件。
