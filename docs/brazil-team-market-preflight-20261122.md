# 巴甲球队进球盘口与 BTTS 请求预检

Data 的 `unattended.yml` 在提交 `bd0cba9e22dd1191c0524f12fff4cec7a83fb94b` 使用市场研究源码 `7c4b833c94426347941667cff540e6d78405485e`。巴甲步骤先执行 `data_sources.team_market_quotes` 的注册赛程采集，再把新归档路径传给 `team_market_candidate issue`。两处相关源码与本草稿基于的 `d5645f13de63658af79289bfd118d530e2b4f0f2` 相同。通用赛果采集和基础训练分别固定在 `fbed25e9…` 与 `6639867d…`。

已核对的消费者中，巴甲候选回放读取首冻引用的初始报价归档，赛果复盘读取首冻和认证赛果。Data 的复验脚本固定检查原始巴甲及英超试点归档。英超每日发现与采集另有输出目录，其他全场市场与赛果步骤也有独立输入。预检仅适用于本批巴甲候选的球队盘口。普通采集、滚动发现、全场市场保留原入口和行为。

## 原回执与离线重放

Data 提交 `a5ecf0d0f1148a7acdb5675d45c347c0d5fb9d12` 中的 `44391584574-1-bra1` 运行于北京时间 11 月 19 日 04:30:29 开始采集。留存原始报价重放确认 6 次请求、回执 6 credits、24 组完整 BTTS、0 组单队盘口。下一步 6 场均 `PREVIOUSLY_ISSUED`，新增候选 0。[报价覆盖回执](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/a5ecf0d0f1148a7acdb5675d45c347c0d5fb9d12/workspace-updates/20261118-market-signals/team-markets/runs/44391584574-1-bra1/coverage.json) · [候选回执](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/a5ecf0d0f1148a7acdb5675d45c347c0d5fb9d12/workspace-updates/20261118-market-signals/team-candidate/runs/44391584574-1-bra1.json)

- `coverage.json` SHA-256 `6da4f39cbb70eb7e7649981d2d012d83b30b879a2de468ab1c03535408f17935`
- 候选回执 SHA-256 `569538775ebcc26634a9596d75153c249f3763b2637b9c2463c4b11a9a6f8a41`

原时点六场都尚未开球。用相同首冻和登记输入重放新开关，模拟供应商调用 0 次，覆盖仍为 6 场。空报价归档可以走原 `issue`，结果为六条 `PREVIOUSLY_ISSUED`。结合 Data 提交 `69821e4cb6db23ac51cc81159ed4a1a0058ca39e` 的留存赛果，隔离 `review` 结算 6/6，原报价仍可重放 12 个冻结矩阵。44 份输入字节保持一致。这组额度数字属于该轮回执与离线重放，不能据此外推月度费用。

## 使用与回归

`team_market_candidate preflight` 只读输出每场分类和通过核验的原冻结与首次候选哈希。它复用原登记、配方、父计划和冻结校验。首冻的球队、比赛、阶段、开球及市场标识须与原登记完全一致。候选的独立数据截点可晚于基线。身份冲突、重复首冻与坏哈希明确拒绝，拒绝的批次不发送请求。

下游 `collect()` 的 `--brazil-first-issue-preflight` 为显式开关，只适用于原登记方案和球队盘口市场。已首发场次保留在覆盖中且不查询。未首发场次仍在请求前重检是否已开球。复验按归档请求开始时间判断当时已存在的首冻，后续首发不改变旧归档。现网 workflow 和源码 pin 尚未接线。

```sh
PYTHONPATH=runtime python -m research_program.team_market_candidate preflight \
  --state-dir /path/to/read-only-snapshot --as-of 2026-11-19T04:30:29.751598+08:00
PYTHONPATH=runtime python -m unittest discover -s runtime/research_program -p 'test_team_market*.py' -v
PYTHONPATH=runtime python -m unittest discover -s runtime/data_sources -p 'test_team_market*.py' -v
python scripts/run_tests.py --layer integration
```

新增模拟测试覆盖全部首发、混合登记、开球时刻、冲突/坏哈希、归档复验和默认采集兼容。真实回放仅用留存字节，模拟发送器一旦被调用就会报错。回滚可在本草稿撤销此提交。
