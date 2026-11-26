# R31 欧联胜平负校准与比分选分

复用 R30 的成年男子欧联联赛阶段 216 场原档，先重建并核对 R13 的比分矩阵及胜平负独立概率。新配方用三个系数校准胜平负概率，再把系数带来的相对概率变化应用于相应比分。每种赛果内部的比分排序和相对概率保持原值。训练、选型、留出各 72 场。[拟合前登记](protocol.json)

2025/26 选型中，半权重首选从 10 增到 11，三选从 30 降到 29。全权重首选 12、三选 26，三轨胜平负均为 50/72。按预登记的三项门槛，选型保留 R13。于 2026/27 留出，半权重选分首选 3/72、三选 22/72，与原 R13 的 4/72、21/72 形成一场得失。R31 留在历史研究阶段。

使用 Python 3.12.14、项目 `requirements.txt`、LightGBM 4.6.0、pandas 3.1.0 复算。`DATA_REPO` 指向数据仓库，`OUT` 使用新的输出目录。

```bash
export PYTHONPATH=runtime PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
export OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 NPY_DISABLE_CPU_FEATURES=AVX512F
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
python -B -m research_program.uel_outcome_calibration --data "$DATA_REPO" --out "$OUT"
```
