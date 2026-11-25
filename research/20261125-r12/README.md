# R12 精选三场首选全中研究

每项赛事独立拟合首选比分的命中概率。使用 R10 原首选的概率为起点，再考虑三个比分中首选占的概率、比分形态、胜平负吻合程度、与独立市场首选是否一致及赛前射正覆盖。先前比赛的比分仅用于训练标签。球队的三个比分和另外两个方向均沿用原始发行。

历史按同赛事同比赛日分批。日期隔离后，主窗在 2,686 场、416 个完整三场批次上比较；原排序和新排序均无整批三中三，含不足三场的所有席位中首选命中由 203 增至 208。独立 2019 后半季的 184 个完整批次、亚冠新增窗口的 7 个完整批次也均无整批全中。历史市场只有报价档，没有逐笔时间。前向批次的所有比赛在同一次登记时已经拥有可核验预测。

```sh
export PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1
python -m research_program.selected_sweep study --data "$R10" --protocol research/20261125-r12/protocol.json --out new-study
python -m research_program.selected_sweep issue --source issued-r10 --original-rank ranked-r10 --weights new-study/forward-weights.json --protocol research/20261125-r12/protocol.json --out new-future
python -m research_program.selected_sweep review --source issued-r10 --original-rank ranked-r10 --out new-future --report new-review.json --results results.json --evidence evidence
```

study 与 review 可离线运行。issue 只适用于真实开球前的原研究名单。未完赛和不足三场均保留原席位。研究排序与正式综合精选有其各自的回执。
