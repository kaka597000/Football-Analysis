# R38 欧战国内对手强度

从 R13 的国内联赛归档重算 21,481 场有常规时间标记的完赛记录。每场国内比赛的主、客进球期望按对手此前的攻防调整，再将球队最近八场的调整汇总为欧战比分输入。此前场次的局部缺失保留原联赛主客期望。各输入只用严格早于该比赛日的历史。欧联和欧协联各自重建已签发的历史 R13 比分网格，分别拟合两个系数。[运行前登记](protocol.json)

欧联 72 场选型，半权重首选和三选均与原版相同，分别为 10 场和 30 场，比分对数损失略低。随后 72 场历史留出，原版和半权重首选均为 4 场，三选由 21 场到 22 场，增加的是奈梅亨对索菲亚列夫斯基的 2–2。此场也由 R33 的射正比分试验补入。欧协联原方案在选型段胜出，历史留出首选 14/54、三选 25/54。选型及留出日期先前已用于项目历史研究。

欧联半权重经筛选进入 11 月 26–27 日欧联全轮独立赛前对照。18 场按原 R13 国内数据和原价格构造分布，保存逐场首版与有序比分。第一阶段沿用此前实际展示和原协议。 [数据归档](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/data/r38-europe-domestic-opponents-20261126/workspace-updates/20261126-r38-europe-domestic-opponents)

使用 Python 3.12.14、源码依赖及 LightGBM 4.6.0。`OUT` 使用新的目录。

```bash
PYTHONPATH=runtime python -B -m research_program.europe_domestic_opponents --data "$DATA_REPO" --out "$OUT"
```
