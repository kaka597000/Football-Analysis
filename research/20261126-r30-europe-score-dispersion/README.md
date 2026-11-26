# R30 欧协联和欧联比分分布校准

两项赛事分别使用成年男子联赛阶段的 R13 历史原档，重建逐场比分矩阵。试验在给定胜平负结果的条件下调整总进球分布的宽窄，校准后每类结果的概率和平均总进球仍与原矩阵一致。三比分全重排和只换第三选各比较半权重及全权重。训练和选型分别用 2025/26 联赛阶段前半和后半，2026/27 赛程作为日期留出。[赛前锁定协议](protocol.json)

欧协联 54 场留出首选 14 场、三选 25 场，验证期保留原方案。欧联 72 场留出，半权重第三选替换了 6 场，首选 4 场、三选 21 场与 R13 持平。两项试验均结束在历史研究阶段。今夜已经冻结的两份首次展示表按登记赛程复盘。

复算使用 Python 3.12.14、源码 `requirements.txt`、LightGBM 4.6.0 和 pandas 3.1.0。`DATA_REPO` 指向数据仓库，`OUT` 使用新的输出目录。

```bash
export PYTHONPATH=runtime PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
export OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 NPY_DISABLE_CPU_FEATURES=AVX512F
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
python -B -m research_program.europe_score_dispersion --data "$DATA_REPO" --out "$OUT"
```
