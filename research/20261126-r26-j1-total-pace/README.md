# R26 日职联总球分布试验

用同赛季、严格早于目标比赛日的球队及联赛近期进球残差调整 R7 的总球概率。每个总球数下的比分相对概率保持。训练 2,291 场，验证 218 场，日期留出 312 场。按首选和三选均至少持平的预登记规则，验证段选出权重 0。[训练前登记](protocol.json)

| 218 场验证 | 第一比分 | 每场三选 |
| --- | ---: | ---: |
| R7 | 23 | 71 |
| R26 半权重 | 23 | 70 |
| R26 全权重 | 22 | 71 |

留出段 R7 和选定方案都是首选 45/312、三选 106/312。预登记全权重对照为首选 45/312、三选 108/312。2026/27 的 150 场中，全权重方案将场均预期总球从 2.70 降到 2.62，实际为 2.87。这批日期已用于历史开发，R26 的数值配方止于历史检验。

## 复算

使用 Python 3.12.14、项目 `requirements.txt`、LightGBM 4.6.0、pandas 3.1.0。`DATA_REPO` 指向含 R25 归档的数据仓库目录。

```bash
PYTHONPATH=runtime PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 \
OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 NPY_DISABLE_CPU_FEATURES=AVX512F \
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
python -B -m research_program.j1_total_calibration --data "$DATA_REPO" --out r26-j1-replay
```
