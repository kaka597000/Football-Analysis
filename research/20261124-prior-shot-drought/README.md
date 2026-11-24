# 过去六场很少射正，能否纠正第一比分中的零进球判断

**结束本配方。** 三个时间外五月共 688 场，加入射正信号后第一比分少中 1 场，实际大小球全赢少 2 场，比分对数损失增加。11 月有原赛前时钟可还原的 15 场中，首选和三选都没有改变。正式模型没有使用此配方。

| 比较窗口 | 有预测及赛果 / 原名册 | 矩阵第一比分 | 矩阵前三 | 胜平负 | 实盘大小球全赢 / 非走盘 |
|---|---:|---:|---:|---:|---:|
| 拟合期，用于失误诊断 | 2,070 / 2,206 | 255 → 257 | 647 → 647 | 1,055 → 1,053 | 1,150 → 1,154 / 2,069 |
| 2023、2024、2025 年各年整月五月 | 688 / 688 | 85 → 84 | 212 → 213 | 369 → 369 | 399 → 397 / 688 |
| 已见的 11 月 20–21 日原赛前批次 | 15 / 33 | 0 → 0 | 5 → 5 | 10 → 10 | 10 → 10 / 14 |

表中从左到右依次为共同基线和增加一项输入的候选。两臂均未向用户交付，实际展示首选、展示三个比分和预先精选三场全中的配对分母均为零。原 R7 的 192 个身份及冻结哈希均已校验。

## 错在什么地方

旧项目 20 项赛事、5,233 场的诊断中，3,491 场首选是 1–1，真实发生 580 场；1,598 场有四球以上，首选给出四球及以上共 36 场。这两组边际数量反映了首选过于集中的现象，无法说明单场错于何因。[原项目逐赛事诊断](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329/workspace-updates/20261123-all-scope-kpi/results/miss-diagnostics.csv)

本轮开发段的共同基线在 2,070 场中精确命中 255 场。1,083 场错在至少一队究竟进球没有，占首选失误的 59.7%；其余 732 场判断对了哪些队会进球，但猜错正进球数。按球队一侧计，有 818 次预报进球、实际为零，另有 436 次预报零球、实际进球。同场两队都猜错时，两项可同时增加。[开发段诊断](results/training.json)

历史后段基线的 603 个错误中，341 个涉及一方是否进球，262 个仅错在正进球数量。新增信号使前者增至 342，后者仍是 262。它找到了常见错误的类型，没有在新时间块中纠正该错误。

## 本次只改一个输入

基线沿用项目已有的同联赛、分主客、对手调整进球实力。它还根据本队最近六场射正均值、对手最近六场允许射正均值校准该队零进球的概率。候选在同一个零球概率上加一项。对每组六个计数，先算其中只有 0–1 次射正的比例，再扣除相同均值下泊松计数的对应概率。对本队进攻和对手防守的两组偏差取平均。因此，两队场均射正相同，比赛之间出现射正短缺的频率仍可不同。

只用较早赛季的 2,043 场完整射正记录拟合单一增量系数，结果为 +0.114774。零球以外各比分的相对概率保持原值。基线与候选使用相同的选分和大小球结算函数。已有 R7 用总球和球权分配研究比分，三臂校准用赛事级零球系数，旧冷门分类器曾使用平均射正与其他技术统计。此处研究的是球队逐场射正的低端频率在均值之外是否有增量。赔率、休息、红牌和半场比分均未进入本模型。[登记协议](protocol.json) · [拟合参数](fit.json)

## 时间隔离与计分结果

北京时间 11 月 24 日 20:36 登记模型、窗口、指标、样本要求和淘汰条件。以 2019/20 至 2021/22 赛季每年 11、1、3、5 月为训练块。每块使用同一月初截点，只取早于月初前一日的完赛记录，源日期未知的最后整天也舍弃。两臂在三个后段五月均只用当季 4 月 29 日及以前的历史。评级与射正特征整月不更新。后段 18 份赛季文件均符合 18 或 20 队双循环的完整有序主客组合。历史赛果取归档国内联赛的全场比分及相符的 FTR 胜平负。历史赛程、技术统计和盘口没有当年逐场的首次公布时钟，五月面板属于日期隔离开发回放。

688 场的两臂矩阵与预测摘要在读取五月赛果前推上远端。它们横跨 63 个比赛日，五月窗口与 #69、#70、#72 的定向评分月份分开。项目此前在其他研究中接触过这些赛季。Bet365 的 2.5 球方向在两臂中均预先计算，赛后以该场真实的大小两边报价列核实这条线。报价时钟未知，不能据该项推出月初已能按此线执行。[预登记提交](https://github.com/kaka597000/Football-Analysis/commit/73498618b7361a39c08be9f8bf0cb5805cf8398c) · [五月预测锁](https://github.com/kaka597000/Football-Analysis/commit/a9640c04df036e5c07462f715f89e1336c4e7307)

| 五月后段指标 | 改对 | 改错 | 选择发生变化 |
|---|---:|---:|---:|
| 矩阵第一比分 | 0 | 1 | 3 |
| 矩阵前三 | 2 | 1 | 24 个有序列表 |
| 胜平负 | 0 | 0 | 0 |
| 2.5 球大小全赢 | 0 | 2 | 2 |

首选净差为 −0.145 个百分点，按整比赛日重抽 2,000 次的 95% 区间为 −0.498 至 0.000。比分对数损失 3.024183 → 3.024317，球队零球对数损失 0.531462 → 0.531528，均是越小越好。英超首选 12 → 11，另外五个联赛的首选数不变。此次结论按预登记的共同分母做出，未分联赛重新选参或新增长期候选。[机器可读历史结果](results/historical.json) · [20 项赛事的覆盖表](results/coverage.csv)

## 有原赛前时钟的旧批次

原 33 场各保留身份和冻结。31 场可核原赛前盘口与常规时间结果。两队均能在原截止前匹配到六场真实射正的有 15 场，包括英超 6、德甲 6、意甲 2、法甲 1。其余 18 场包括 12 场不在本研究可得的来源范围、1 场荷甲缺截止前可认证的同季来源、3 场西甲球队映射不足，以及 2 场韩 K1 Final A 超出批准阶段。可用 15 场的 360 个射正数据单元已按当时来源、球队、攻守方向和日期独立复核。

原结果核验通过的 31 场，主客队的两个常规时段均与终场分数相合。15 场配对集中在同一个北京时间比赛日，所以不计算比赛日置信区间。实际大小球两臂各全赢 10、半赢 0、走盘 1、半输 1、全输 3。比分对数损失 2.954433 → 2.952616，四项选择均相同。该批在 #69、#70 已经研究过，仅保留时点复原证据。[原批次预测锁](https://github.com/kaka597000/Football-Analysis/commit/41756ff75fea711cc5a7446da113defc0c1da55b) · [原时点结果](results/clocked.json)

## 复现

从本源码仓根目录运行。使用 Python 3.12 及仓库 requirements-integration.txt 中的依赖。下列 DATA 指向已获取固定对象的 Data 仓，OUT 指向一个新本地目录。供应商原始字节留在原归档，只公布汇总、协议及校验值。

~~~sh
export PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
S=research/20261124-prior-shot-drought
PIN=a6d45024cf6cf622f05e4b378ab53cfe2d6ae329
mkdir -p "$OUT"
git -C "$DATA" show "$PIN:workspace-updates/20261123-kpi-optimization/source-assets.tar.gz" > "$OUT/season.tgz"
git -C "$DATA" show "$PIN:workspace-updates/20261123-prematch-literature/first-issues.tar.gz" > "$OUT/r7.tgz"
python "$S/history.py" train --archive "$OUT/season.tgz" --r7 "$OUT/r7.tgz" --out "$OUT/train.jsonl"
python "$S/study.py" fit --inputs "$OUT/train.jsonl" --out "$OUT/fit.json"
cmp "$OUT/fit.json" "$S/fit.json"
python "$S/history.py" test-inputs --archive "$OUT/season.tgz" --r7 "$OUT/r7.tgz" --out "$OUT/test.jsonl"
python "$S/study.py" seal --inputs "$OUT/test.jsonl" --fit "$OUT/fit.json" --panel historical --out "$OUT/historical"
cmp "$OUT/historical/lock.json" "$S/locks/historical.json"
python "$S/history.py" test-labels --archive "$OUT/season.tgz" --r7 "$OUT/r7.tgz" --lock "$S/locks/historical.json" --forecasts "$OUT/historical/forecasts.jsonl" --out "$OUT/labels.jsonl"
python "$S/grade.py" score --panel historical --forecasts "$OUT/historical/forecasts.jsonl" --labels "$OUT/labels.jsonl" --lock "$S/locks/historical.json" --out "$OUT/historical.json"
python "$S/grade.py" training-diagnosis --train "$OUT/train.jsonl" --fit "$OUT/fit.json" --out "$OUT/training.json"
cmp "$OUT/historical.json" "$S/results/historical.json"
cmp "$OUT/training.json" "$S/results/training.json"
~~~

原时点核验复用已有两个研究的净签出目录。把 SRC69 和 SRC70 分别设为以上游提交 `54ec6d70fdff7145c1889ffe6071b2188663bef2` 和 `6e3906fb24e487eec736e73ff304975d0ac35e93` 签出的源码目录。Data 尚需含 `a5ecf0d0f1148a7acdb5675d45c347c0d5fb9d12` 及相关被引用的历史 Git 对象。

~~~sh
AUDIT="$SRC69/runtime:$SRC69/research/20261124-prior-dismissals:$SRC70/research/20261124-halftime-balance"
PYTHONPATH="$AUDIT" python "$S/clocked.py" inputs --data "$DATA" --out "$OUT/clocked-inputs.jsonl"
python "$S/study.py" seal --panel clocked --inputs "$OUT/clocked-inputs.jsonl" --fit "$OUT/fit.json" --out "$OUT/clocked"
cmp "$S/locks/clocked.json" "$OUT/clocked/lock.json"
PYTHONPATH="$AUDIT" python "$S/clocked.py" labels --data "$DATA" --sealed-inputs "$OUT/clocked-inputs.jsonl" --forecasts "$OUT/clocked/forecasts.jsonl" --forecast-lock "$S/locks/clocked.json" --out "$OUT/clocked-labels.jsonl"
python "$S/grade.py" score --panel clocked --forecasts "$OUT/clocked/forecasts.jsonl" --labels "$OUT/clocked-labels.jsonl" --lock "$S/locks/clocked.json" --out "$OUT/clocked.json"
cmp "$OUT/clocked.json" "$S/results/clocked.json"
python "$S/competition_table.py" --historical "$OUT/historical.json" --clocked "$OUT/clocked.json" --clocked-inputs "$OUT/clocked-inputs.jsonl" --out "$OUT/coverage.csv"
cmp "$OUT/coverage.csv" "$S/results/coverage.csv"
python -m unittest discover -s "$S" -p 'test_*.py' -v
python scripts/run_tests.py --layer integration
~~~

研究脚本与结果均独立于运行路由。删除本研究分支即可撤回草稿。正式模型、权重、赛前历史、R7 队列和发布流程均未修改。
