# R25 日职联球队进失球与比分分配

用日职常规阶段每队此前的进失球分布，重新分配 R7 每档总进球数下的比分概率。以 2,291 场训练，在 218 场验证中选中 0.5 权重。后两段共 312 场日期留出中，首选从 45 场降到 44 场，三选均为 106 场。按训练前登记，候选停在历史研究阶段。周末十场继续按原 J1 连续窗口登记结算。[训练前登记](protocol.json)

| 窗口 | R7 首选 | R25 首选 | R7 三选 | R25 三选 | 两轨胜平负 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2025 下半年 | 32/162 | 31/162 | 71/162 | 72/162 | 88/162 |
| 2026/27 面板 | 13/150 | 13/150 | 35/150 | 34/150 | 80/150 |
| 合计 | 45/312 | 44/312 | 106/312 | 106/312 | 168/312 |

R25 在 150 场 2026/27 面板上的首选与 R7 逐场一致。该段 R7 平均预期总球 2.70，实际 2.87。三球或以上实际发生 84 场，R7 概率合计约 75 场。这个差异可作为后续检查总球分布的线索。312 场归档均缺可结算的实际大小球线。数据属历史开发样本，研究轨为 `EXPERIMENTAL / OOD`。

## 复算

使用 Python 3.12.14、`requirements.txt`、LightGBM 4.6.0 和 pandas 3.1.0。`DATA_REPO` 指向数据仓库。入口核对三个归档哈希，匹配 R7、R9 逐场记录和盘口，并写出拟合、验证和留出结果。

```bash
PYTHONPATH=runtime PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 \
OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 NPY_DISABLE_CPU_FEATURES=AVX512F \
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
python -B -m research_program.j1_team_allocation --data "$DATA_REPO" --out r25-j1-replay
```
