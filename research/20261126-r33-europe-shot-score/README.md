# R33 欧联与欧协联射正比分配对

使用 R10 已归档的逐日射正和原 R7 权重重建比分矩阵，与同场原 R13 逐项核对。每种胜平负结果内重新分配比分概率，该结果在 R13 矩阵中的总概率沿用原值。各混合方案使用原 R13 的独立方向和原真实盘线选择。任一方无此前日期射正历史时沿用原 R13。[回放前登记](protocol.json)

2025/26 前半段提名权重，后半段核验。欧联 0.25 权重在后半段首选由 10/72 降到 9/72。欧协联全权重首选由 8/54 降到 6/54，三选由 23/54 降到 22/54。两项选择回到原 R13。2026/27 归档日期的各权重逐场成绩仍可复算。

```bash
PYTHONHASHSEED=0 OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 \
  NPY_DISABLE_CPU_FEATURES=AVX512F OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  PYTHONPATH=runtime python -B -m research_program.europe_shot_score \
  --data /path/to/kaka597000-Football-Analysis-Data --out /path/to/new/replay
```

输出保留三个完整日期窗口、射正历史覆盖、所有预先指定权重、原市场和配对净得失。两个留出窗口为项目已研究的历史比赛。R33 归档为开发结果，第一阶段按既有真实赛前登记进行。[欧战前向登记](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/77)
