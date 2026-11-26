# R36 欧联与欧协联三球及以上的比分覆盖

重建两项赛事的原 R13 比分分布。在同一胜平负结果内校准总进球至少三球的概率，并比较半权重、全权重重排和单独替换第三选。第三选可以从原矩阵第 4 至第 12 位里补入同方向的三球以上路径，需符合预登记的概率和排序条件。[运行前登记](protocol.json)

在欧联历史日期留出的 72 场，三球以上实际出现 45 场，原 R13 概率合计为 42.39 场。差异集中于恰好三球，实际 19 场，原概率合计 15.95 场。全权重把三球以上估计降到 39.33，完整重排在选型段同时损失首选和三选。欧协联在历史留出第三选多中 1 场，选型段为持平。两项赛事按预登记选回 R13。[原前向登记](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/77)

```bash
PYTHONHASHSEED=0 OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 \
  NPY_DISABLE_CPU_FEATURES=AVX512F OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  PYTHONPATH=runtime python -B -m research_program.europe_three_goal_coverage \
  --data /path/to/kaka597000-Football-Analysis-Data --out /path/to/new/replay
```

每窗输出总球概率、同场得失、历史盘线及市场对照。第一阶段按原来首次赛前展示及真实常规时间赛果复盘。
