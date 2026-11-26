# R32 欧联与欧协联 1–1 首选复盘

用 R30 锁定的完整联赛阶段回放比分矩阵，分别分析欧洲两项赛事。2025/26 前半拟合、后半选权重，2026/27 已归档轮次用于日期留出。登记在首次运行前提交。[拟合前登记](protocol.json)

在原首选为 1–1、独立胜平负判断为主胜或客胜、重建排序与原三选一致的场次，单独校准 1–1 比分概率。其余比分的相对概率沿用原矩阵，胜平负和实际盘线方向沿用各自的原预测。

```bash
PYTHONHASHSEED=0 OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 \
  NPY_DISABLE_CPU_FEATURES=AVX512F OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  PYTHONPATH=runtime python -B -m research_program.europe_one_all_calibration \
  --data /path/to/kaka597000-Football-Analysis-Data --out /path/to/new/replay
```

归档包括三个窗口的逐场比赛、原三选是否包含实际赛果方向、候选与实际比分的最小进球数误差、所有固定权重及市场对照。两项赛事的校准均未达到独立前向测试条件。欧联验证段半权重增加 2 个首选命中，日期留出段改对 1 场、改错 2 场，三选命中持平。欧协联日期留出段两个比分指标均持平。已有登记以各自赛前首版继续结算。[欧战首版](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/77)
