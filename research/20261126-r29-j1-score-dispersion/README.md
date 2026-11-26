# R29 日职联总进球离散度实验

沿用北京时间 11 月 26 日 15:12 登记的协议。仅使用日职联成年男子常规赛。用 2025 年前 2,291 场的按日滚动 R7 基础矩阵拟合，以 2025 年前半 218 场选权重。后续 162 场与 150 场是项目已使用的历史留出。[拟合前登记](protocol.json) · [锁定输入及时间窗](../20261126-r25-j1-allocation/protocol.json)

在每种胜平负结果内调整总进球分布的离散度，同时保持该结果的概率和平均进球数。原胜平负判断与大小球选边单独沿用。验证时两个非零权重的首选均为 22/218，原 R7 为 23/218，因此按原条件选原 R7。历史留出中的候选仍全部报告。

```bash
PYTHONHASHSEED=0 OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 \
  NPY_DISABLE_CPU_FEATURES=AVX512F OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  PYTHONPATH=runtime python -B -m research_program.j1_score_dispersion \
  --data /path/to/kaka597000-Football-Analysis-Data --out /path/to/new/replay
```

复算输出含拟合系数、选择、配对结果和 530 场验证及留出逐场比分。首选与三选判定以原选型规则为准，R29 留作历史研究。日职联第一阶段继续按各自已登记的赛前首版和真实赛果核验。[12 月日职联登记](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/69)
