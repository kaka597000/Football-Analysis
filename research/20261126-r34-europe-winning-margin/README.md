# R34 欧协联与欧联净胜球校准

根据潜在胜方的 R13 概率，分别校准一球小胜与两球以上取胜的比率。拟合用已知的历史胜方，赛前对两队分别计算。每个胜平负档及每个平局比分的概率固定，实际盘线方向沿用原 R13。样本、赛程分段、权重选择及前向条件在运行前登记。[历史开发登记](protocol.json)

欧协联以半权重通过历史筛选，日期留出首选 14/54 持平，三选从 25/54 到 26/54。欧联的备选均在验证段损失三选命中，按登记保留 R13。未来的候选对照使用独立赛前冻结和真实 90 分钟结果。原比赛、首版及精选按原协议评价。[原赛程与预测](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/77)

```bash
PYTHONHASHSEED=0 OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 \
  NPY_DISABLE_CPU_FEATURES=AVX512F OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  PYTHONPATH=runtime python -B -m research_program.europe_winning_margin \
  --data /path/to/kaka597000-Football-Analysis-Data --out /path/to/new/replay
```

每窗输出完整逐场记录，同时保留比分原概率、市场对照、同场改对改错和按比赛日聚类的区间。
