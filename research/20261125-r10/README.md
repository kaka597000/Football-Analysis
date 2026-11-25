# R10 射正、转化率和精选三场

每项赛事按日期滚动，仅使用此前日期的双边射正。进球数超过射正数的一侧保留射正，剔除该侧转化率观测。按实际预算将两队进球强度与射正协方差作用于 R7 比分矩阵。赛前射正历史缺失的比赛计入原分母。参数、日历和三个独立输出的选择规则见本目录 protocol.json。

## 从私有数据版本复算

在仓库根目录使用 Python 3.12，设置 PYTHONPATH=runtime 及 OPENBLAS_NUM_THREADS=1。将数据目录中的 inputs/independent.tar.gz 解至 independent，inputs/elite.tar.gz 解至 elite-data，inputs/supplement.tar.gz 解至 supplement。R5、R6、R7、R9 分别是 20261123-all-scope-kpi、20261123-score-gap-repair、20261123-prematch-literature、20261125-r9-team-score 的版本目录。各重放命令要求新的输出目录。

```sh
python research/20261125-r10/prepare_elite.py --r5 "$R5" --data-repo "$DATA_REPO" --intake elite-data/intake --additional elite-data/additional --out elite-checked
python -m research_program.r10_study --mode main --r5 "$R5" --r6 "$R6" --r7 "$R7" --r9 "$R9" --process supplement/process.jsonl --process elite-checked/process.jsonl --out replay-main
python -m research_program.r10_study --mode elite --r5 "$R5" --r6 "$R6" --input elite-checked --process elite-checked/process.jsonl --out replay-elite
python -m research_program.r10_study --mode independent --input independent --out replay-independent
```

归档复算读取数据目录 verify.py。前向发行入口为 research_program.r10_forward，只能在真实比赛开球前签发。research_program.ranked_score_study lock 从已经冻结的同一批次、同一赛事全部场次中按首选概率挑选最多三场，同概率按开球和比赛 ID 排序。review 使用已认证的常规时间原始证据计算每项指标、精选逐席和三场全中。回放、研究发行和综合展示各自拥有独立回执。

四个阶段沿用项目主配置中的 research_roadmap_20261124。每批三个首选全中是额外指标，少于三场、未完赛或缺少首次展示依据的样本保留覆盖状态。研究概率排序的前向结果不自动认定综合推荐达标。
