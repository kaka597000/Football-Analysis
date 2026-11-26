# R27 日职联比分校准

这轮试验为 1–1、零封取胜和双方进球的净胜一球分配独立校准系数，再把胜平负和各档总进球概率校准回 R7 的水平。训练使用 2025 年之前的 2,291 场日职联常规赛。218 场验证按首选和三选都至少持平的规则选出半权重。两个日期留出段共 312 场，属于项目已使用的历史开发样本。[训练前登记](protocol.json)

| 样本 | R7 首选 | R27 半权重首选 | R7 三选 | R27 半权重三选 |
|---|---:|---:|---:|---:|
| 2025 上半年验证 | 23/218 | 23/218 | 71/218 | 72/218 |
| 2025 下半年 | 32/162 | 32/162 | 71/162 | 71/162 |
| 2026/27 面板 | 13/150 | 13/150 | 35/150 | 35/150 |
| 留出合计 | 45/312 | 45/312 | 106/312 | 106/312 |

半权重在留出中重排了 12 场的三选，只有 1 场换入新比分，所有首选与 R7 逐场一致。全权重首选 45/312，三选 105/312，三选配对新增 1 中、丢失 2 中，按日期聚类的净增区间为 −1.50 至 +0.67 个百分点。全部比分方案的胜平负均为 168/312，历史严格实盘覆盖 0/312。全权重的最大胜平负概率偏差小于 2e−13，最大总进球概率偏差小于 2e−16。

R27 留在历史开发阶段。12 月两窗按已登记的综合证据协议发布并锁定唯一首选和两个有序备选，等赛后核对实际首版、带时钟实盘和赛果。[12 月登记协议](../20261126-j1-december-stage-windows/protocol.json)

## 复算

使用 Python 3.12.14、项目 `requirements.txt`、LightGBM 4.6.0 和 pandas 3.1.0。`DATA_REPO` 指向包含 R5、R7 原始归档的数据仓库。入口校验归档和逐场身份，输出拟合、验证与日期留出结果。

```bash
PYTHONPATH=runtime PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 \
OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 NPY_DISABLE_CPU_FEATURES=AVX512F \
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
python -B -m research_program.j1_score_calibration --data "$DATA_REPO" --out r27-j1-replay
```
