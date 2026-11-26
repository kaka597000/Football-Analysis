# R28 日职联近况过程前向对照

11 月 25 日的原综合首选命中 1/7，第三选命中三场，三选合计命中 4/7。水户–东京绿茵的 1–0 在两份原数值矩阵均排第 1，在原综合排第 3。两场 2–2 中，FC 东京–广岛的实际比分在 Current/Test 排第 8/9，浦和–大阪樱花排第 6/7。新归档的赛后团队 xG 合计分别为 2.07、4.45，可据此区分两个同比分的过程。[原复盘](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/65) · [逐场过程](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/72)

R28 在 11 月 28–29 日的原十场赛程上比较三种统计选分。共同基线用两份原矩阵的归一化几何平均。近况两轨在相同历史场次上分别使用进球和 FotMob xG，按预登记的收缩和上限调整比分格。每方使用 6–10 个去重观测，保持可核对的输入时间与来源。[试验登记](protocol.json)

北京时间 11 月 26 日 14:31 冻结 `d7f7b90b447f4512-v000001`。相对共同基线，xG 轨改变两场首选、五场三选名单。与同样本进球轨逐场首选相同，三选名单差一场。十场待 90 分钟赛果，结果按原场次和真实冻结结算。第一阶段使用另行登记的完整赛前首版窗口。

## 复算

用 Python 3.12.14、项目 `requirements.txt`、LightGBM 4.6.0 和 pandas 3.1.0。用数据仓库路径与原始回执目录作只读重放。

```bash
PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python -B -m research_program.j1_process_forward --data "$DATA_REPO" --verify "$ARCHIVED_ISSUES"
```
