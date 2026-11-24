# 历史红牌失衡场次对比分首选的影响

**结论为淘汰本配方。** 后段 1,135 场的模型首选增加 8 次，矩阵前三减少 5 次，比分对数损失从 2.9860 升至 2.9883。后两项未达到事前门槛。两个模型都没有对外发送，实际首选、实际展示三比分和赛前精选三场的配对有效分母均为 0。研究结束于离线比较。

| 同场窗口 | 有效场数 | 矩阵首选 基线→候选 | 矩阵前三 | 胜平负 | 大小球全赢／非走盘 | 比分对数损失 |
|---|---:|---:|---:|---:|---:|---:|
| 2010/11 至 2017/18 开发 | 2,605 | 330→327 | 820→807 | 1,307→1,299 | 缺具名双边价 | 2.9770→2.9937 |
| 2019/20 至 2022/23 后段历史核查 | 1,135 | 140→148 | 366→361 | 552→565 | 633→627／1,129 | 2.9860→2.9883 |
| 2026 年 11 月 20–21 日北京时间，按原截点重建 | 15／33 | 0→1 | 5→4 | 10→11 | 10→11／14 | 2.9384→2.9341 |

后段首选由 12.33% 升至 13.04%，涉及改对 28、改错 20。按比赛日期整体重抽 2,000 次，差值 95% 区间为 −0.33 至 +1.79 个百分点。矩阵前三改对 39、改错 44，胜平负改对 38、改错 25，大小球全赢改对 53、改错 59。后段各联赛首选净变化为英超 +8、西甲 +4、德甲 −3、意甲 +1、法甲 −2、荷甲 0。开发期首选改对 57、改错 60。完整的 20 项赛事分母和缺口见 [coverage.csv](coverage.csv)，逐指标净变化、结算和按日期重抽区间见 [results.json](results.json)。

2026 年共识别 33 个原档目标。33 个综合首冻均可绑定，16 份不同的赛前盘口原始响应覆盖这 33 场。14 场所属赛事没有本研究的赛季来源，荷甲 1 场没有该季赛前来源，西甲 3 场未能用锁定身份规则同时确定两队。可评分 15 场覆盖英超 6、德甲 6、意甲 2、法甲 1。每场的首次综合冻结、同一盘口的两边原始报价和 ESPN 终场字节均经过核验。首选改对 1、改错 0，矩阵前三改对 0、改错 1。大小球 15 个盘口含 1 次走盘，两组计入非走盘的场数均为 14，各有 1 次半输。赛前可得的是这些历史输入，本研究两个版本的预测均在赛后重建。该小样本仅覆盖两个北京时间比赛日。R7 的 192 个原冻结已验哈希，和本次 33 个目标的 ID 及赛事、两队、开球组合交集均为空。

## 唯一变量与时间隔离

沿用已有对手调整进球强度模型、120 天衰减、相同主客先验和 31×31 独立泊松。两组每季只训练 7 月 1 日至 11 月 8 日的联赛完赛数据，评估 11 月 20 日至 12 月 31 日。每轮预测均使用这份封闭训练集。候选组剔除早期双方红牌数不同且都有有效计数的比赛。红牌相等或缺失的比赛保留。原始数据未给红牌分钟，实验只能检验整场剔除的代价。两组均未使用目标比赛的红牌或赛果产生预测。[源字段说明](https://www.football-data.co.uk/notes.txt)

三个窗口分别剔除 845／5,224、463／2,592、75／500 场历史比赛。后段有 178 场因候选训练覆盖不足，按原协议复制基线，仍留在分母。早期训练有 752 场红牌数缺失。早期六个联赛及后段六个联赛均为已知结果的档案。历史字段 Bet365 大小 2.5 有双边价可复核，单场原报价时间未知，仅列历史开发成绩。最近一批要求查询时间、报价时间、原始响应及首冻相符。[真实赛前训练来源](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/a5ecf0d0f1148a7acdb5675d45c347c0d5fb9d12/workspace-updates/20261118-transformer/transfer/raw)

[研究规则](protocol.json)于提交 `a9712f86b1ed6089f98b7df4a915e079349e57e4` 锁定。[配对预测摘要](forecast-lock.json)于提交 `63714346a855e83450590110cded28a82721d68e` 记录之后，才执行独立计分。生成预测与计分两步均只读固定数据对象。完整供应商原文及逐场研究预测保留在本地复算目录。

## 精确比分盘与双方进球盘的覆盖

比分盘和双方进球盘能区分胜平负与总球数相同的路径，例如 3–0 与 2–1。上一轮已有直接精确比分排序和 BTTS 的研究实现。要检验增量，仍需在同场、同机构和同一赛前截点取得完整报价并对照常规市场。[精确比分原研究](https://github.com/kaka597000/Football-Analysis/pull/56)

- HKJC 三场各有 28 个明确比分和 5 个其余比分档，共 99 项。三份页面均在原截止前获取，逐价供应商报价时间及其与原冻结快照的绑定缺失，严格配对为 0／3。86 场带时钟的市场基准包也没有完整比分盘。仅有两项比分价格的专项材料无法还原全盘。[比分盘档案](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329/workspace-updates/20261123-team-strength)
- 时间报价摘录包含 9 场 BTTS。扩大到数据仓全部同项目 `team-markets/runs/*/quotes.json`，得到 135 条赛前双边报价、13 个独立比赛 ID（巴甲 6、英超 7）。这些报价都有供应商时间与抓取时间，常规时间口径一致。最初两个具名试点登记巴甲 6 场、英超 2 场；其余报价不能仅凭市场记录计为一次交付。13 个 ID 与 R7 的 192 个 ID 核对无交集。[市场运行归档](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329/workspace-updates/20261118-market-signals/team-markets/runs) · [首冻登记](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329/workspace-updates/20261118-market-signals/team-candidate/first-issue-evidence.json)
- 11 月 24 日北京时间 17:52 正常定时采集成功。巴甲 6 场均已登记，发出 0 次新市场请求；英超发现 1 场、额度预检为 `QUOTA_BUDGET`，取得 0 份报价。该场已在 R7 原队列。经核的市场来源尚不足以证明能按当前权限和额度连续供给这项比较。[正常运行](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/actions/runs/45369336846) · [英超额度回执](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/076fe6d1a83281cb9393ee4887d1deced995eb0a/workspace-updates/20261118-market-signals/team-markets/runs/daily-2026-11-24-eng1/coverage.json)

下一次有正常综合发布、同时已有获授权的完整比分盘时，优先在原负责人交付前核对市场全清单、双时钟、赛事身份、原始响应摘要和展示顺序，并用独立发送回执查回首版。先取得可评分的真实赛前证据，再决定是否值得增加比较。研究未预登记新队列。

## 本地复算

在源码仓根目录运行。本地复算使用 Python 3.12.14、NumPy 2.3.5、SciPy 1.17.0 与单线程 BLAS，`DATA_REPO` 指向拥有两个协议 Git 对象的数据仓克隆，`OUT` 指向新的本地目录。准备阶段只对评估对象读取身份与盘口列，训练结果和卡数仅从训练日期抽取。评分阶段验证预测摘要，读取目标结果并按盘口结算。

```sh
export PYTHONPATH="$PWD/runtime" PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1
STUDY=research/20261124-prior-dismissals
python "$STUDY/replay.py" prepare --data-repo "$DATA_REPO" --out "$OUT/prepared"
cmp "$STUDY/forecast-lock.json" "$OUT/prepared/forecast-lock.json"
python "$STUDY/replay.py" grade --data-repo "$DATA_REPO" --from-run "$OUT/prepared" --lock "$STUDY/forecast-lock.json" --out "$OUT/graded"
cmp "$STUDY/results.json" "$OUT/graded/results.json"
python -m unittest discover -s "$STUDY" -p test_archive.py -v
python -m unittest discover -s runtime/research_program -p test_prior_dismissals.py -v
python scripts/check_repository.py
python scripts/run_tests.py --layer integration
```

本研究的命令由开发者单独运行。源码变更仅含独立研究入口、测试和汇总。回滚时删除这条研究分支或还原本研究目录与两个独立模块即可。
