# R49 日职联赛季 xG 与比分分配

用 Football LAB 的赛季均场 xG 和失球预期修正主客比分分配，保留原 R7 的各总球概率。两种进球对照分别使用网页排除乌龙球的进球率和已核实 90 分钟全进球率。周末预测使用 11 月 26 日更新的球队表。[执行前登记](protocol.json)

用原始采集早于计划开球的 11 月 13 日球队表回放 11 月 20–21 日十场。三组选分首选均为 0/10，xG 三选 3/10，两种进球对照 2/10，原 R7 为 1/10。用 2025 年表开发回放 140 场时，xG 首选及三选为 13/140 和 32/140，原 R7 为 13/140 和 34/140。

北京时间 11 月 27 日 11:27:44 完成周末十场独立赛前冻结，回执 `31ea7f132acf6e2f-v000001`。神户胜利船对千叶市原的 xG 第一比分为 1–1，两种进球对照为 1–0。按开球及常规赛果审核时使用同一批场次。

```bash
PYTHONPATH=runtime python -B -m research_program.j1_season_chance_rates \
  --data /path/to/Football-Analysis-Data --replay /tmp/j1-r49-replay
PYTHONPATH=runtime python -B -m research_program.j1_season_chance_rates \
  --data /path/to/Football-Analysis-Data \
  --verify /path/to/Football-Analysis-Data/workspace-updates/20261127-r49-j1-season-chance-rates/issued
```
