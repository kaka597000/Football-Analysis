# R24 欧冠联赛阶段零球分配

11 月 26 日凌晨欧冠九场中，原综合首选全部预测双方进球，实际有五场一方零球。原三选覆盖五场，五个正确比分均排第三。R24 检验能否通过重新分配每个总进球数下的比分概率，提高唯一第一推荐。九场复盘只用于提出问题。[原赛前首版复盘](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/65)

在 2024/25 欧冠联赛阶段 144 场拟合两个零球系数，以 2025/26 前五轮 90 场选权重。在后段 54 场及 2026/27 原面板 72 场评价。R24 保持 R7 的每档总进球概率，复用已归档的 R7 胜平负和盘口选向。选权、比较、前向条件见[训练前登记](protocol.json)。

| 126 场日期留出 | 第一比分 | 每场三选 | 胜平负 | 归档盘口全赢 |
| --- | ---: | ---: | ---: | ---: |
| R7 / R13 | 8/126 | 28/126 | 82/126 | 70/115 |
| 市场基准 | 9/126 | 26/126 | 82/126 | 64/115 |
| R24 | 7/126 | 31/126 | 82/126 | 70/115 |

R24 相对 R7 改动 27 个首选，改对 0 场、改错 1 场。它把皇马对国米的 2–1 降为备选，实际为 2–1。三选改对 5 场、改错 2 场。预登记要求首选净增，故本配方结束在历史研究阶段。两段比赛此前已用于项目研究，盘口归档缺少机构报价时点。

## 复算

使用 Python 3.12.14、项目 `requirements.txt`、LightGBM 4.6.0 和 pandas 3.1.0。`DATA_REPO` 指向含预登记中三个归档的检出目录。脚本验证各归档 SHA-256 及同场对照身份，输出到新目录。

```bash
PYTHONPATH=runtime PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 \
OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 NPY_DISABLE_CPU_FEATURES=AVX512F \
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
python -B -m research_program.ucl_zero_goals --data "$DATA_REPO" --out r24-ucl-replay
```

`fit.json` 保存拟合系数、验证比分与选权。`metrics.json` 保存分窗指标、同场得失和日期聚类区间。两个 JSONL 归档保留验证与留出的逐场预测。
