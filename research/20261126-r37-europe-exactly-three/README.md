# R37 欧联与欧协联恰好三球的比分校准

在每种球队取胜结果的三球以上比分内重分恰好三球和四球以上的概率。沿用原 R13 的胜平负概率、三球以上概率和独立盘口选择。两项赛事各自读取原日期窗，验证先看首选和三选，保留原市场同场对照。[运行前登记](protocol.json)

欧联全权重在验证段首选 10→13、三选 30→31。在日期留出，首选由 4/72 降到 3/72，三选由 21/72 降到 20/72。伯恩茅斯对格拉茨风暴的 3–0 改对。水晶宫对霍芬海姆、皇家社会对里昂都把真实 1–1 从首选移至第二，改推 2–1。欧协联验证全持平，按登记选回 R13。两项研究均结束在历史开发。[原欧洲前向登记](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/77)

```bash
PYTHONHASHSEED=0 OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 \
  NPY_DISABLE_CPU_FEATURES=AVX512F OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  PYTHONPATH=runtime python -B -m research_program.europe_exactly_three \
  --data /path/to/kaka597000-Football-Analysis-Data --out /path/to/new/replay
```

R37 复用 R36 的赔率和比分评估。R36 历史入口经本次代码复算逐字节匹配已归档结果。
