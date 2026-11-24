# 近六场上半场表现能否改善首选比分

**按事前门槛淘汰这版配方。** 在拟合截止之后的 1,764 场中，首选改对 11 场、改错 5 场，从 12.19% 升至 12.53%。按比赛日重抽的 95% 差值区间为 −0.12 至 +0.81 个百分点。原赛前盘口和时钟可核的 31 场，两个版本的第一比分逐场相同。正式展示首选、实际展示三比分及精选三场的配对分母均为 0。[规则](protocol.json) · [逐赛事覆盖](coverage.csv) · [完整计分](results.json)

| 同场窗口 | 有效场数 | 矩阵首选 | 矩阵前三 | 胜平负 | 实盘大小球全赢／非走盘 | 比分对数损失 |
|---|---:|---:|---:|---:|---:|---:|
| 后段历史，2023–2025 各年 2–4 月 | 1,764 | 215 → 221 | 602 → 604 | 956 → 958 | 1,028 → 1,028／1,764 | 2.88575 → 2.88541 |
| 11 月 20–21 日按原决策截点重建 | 31／33 | 2 → 2 | 6 → 6 | 17 → 17 | 15 → 15／29 | 3.00867 → 3.00349 |

表中为基线 → 候选。后段共有 63 场改变首选，47 场两边都未命中。矩阵前三改对 14、改错 12，胜平负改对 8、改错 6。后段具备完整六场输入的 1,736 场单列为首选 214 → 220；另 28 场按锁定政策回退，仍保留在全量分母。首选按赛事的净变化为英超 −1、西甲 −1、法甲 +3、德甲 +2、意甲 +3。历史列保存具名双边盘口，但逐笔报价和赛前抓取时钟缺失，只能支持日期隔离的开发检验。

带时钟的 33 个原档目标中，韩 K1 两场 Final A 超出现行常规赛阶段，有报价但被排除。另外 31 场均有一致的原综合首冻、同一机构的原始 1X2 与总球响应、赛前报价及取回时钟、可核的常规时间赛果。其中 15 场有双方完整近六场输入（英超 6、德甲 6、意甲 2、法甲 1），其余 16 场复制同一市场基线（13 场缺此赛事的训练来源，3 场西甲的球队身份未能双边绑定）。15 场子集的首选均为 1／15，矩阵前三均为 4／15。31 场大小球各有 2 走盘和 2 半输。对固定的每个总进球数，候选只重新分配主客双方份额，因此所有大小球线的概率均保持一致。两组的预测是赛后离线重建；此批部分比赛已用于项目其他研究。

## 单一信号与训练

每支球队用最近六场同一联赛完赛记录计算“上半场净胜球 − 全场净胜球的一半”的均值，衡量净胜球优势较多出现在上半场还是下半场。取主队均值减客队均值作为唯一新增输入。历史窗口只用目标日期之前的完赛记录，同日全部预测结束后才记入当天结果；在后段滚动时，已结束的早期比赛可以成为之后比赛的历史。材料稀疏、最早一场距目标超过 90 天或身份不清时，两臂沿用原基线。2026 年重建使用 11 月 18 日已存证的五个赛季文件，500 场前序比赛均有半场及全场比分；逐球队时间、近六场条数和身份另行检查。[已存证数据](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/a5ecf0d0f1148a7acdb5675d45c347c0d5fb9d12/workspace-updates/20261118-transformer/transfer/raw) · [数据源字段说明](https://football-data.co.uk/notes.txt)

两臂共享现有的单机构市场基线及选分函数。候选用这一项球队差值改变同总球数下的主客进球分配，零格保留。唯一系数在 2019/20 至 2021/22 年 5,377 场中的 4,239 场上拟合，得到 −0.03692；1,132 场缺合格近期输入，另 6 场缺完整报价。最后拟合日期为 2022 年 5 月 22 日，后段计分始于 2023 年 2 月 1 日。项目现有的进球时段研究学习比赛时段和领先／落后状态，本实验使用过去比赛中每支球队的半场表现，目标场的真实半场比分没有进入预测。[拟合记录](fit.json) · [已有时段模型](../../runtime/research_program/goal_state_process.py)

规则先在 `16c48c1b0115d18fd8e7094b64259903b7b0ed0b` 提交。两组 1,795 个配对预测的矩阵摘要、开盘研究的独立时间界限和模型输入在计分之前确定；预测摘要在 `2ebdce774bd06723131eafadcdf3496f17b8b420` 提交。后段首选置信下界未过零，故停止本配方。没有新增前瞻队列。R7 的 192 个既有比赛冻结仅用于排重。[预测摘要](forecast-lock.json)

## 开盘路径的证据边界

同一机构行情变化已有专门对照。五大联赛 2,218 场保留 Bet365 早期采样列与收盘列，缺逐笔报价时钟、独立抓取时钟及原综合决策截点绑定。原巴甲 6 场可用 Pinnacle 的多次完整报价核对 1X2 和大小球，5 场有 7 次、一场有 6 次完整的决策前抓取。这能复原最早已留存价至决策前末价的两点变化，首次挂牌时点仍缺供应商凭据。[既有盘口对照](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329/workspace-updates/20261118-market-signals/movement)

11 月 18 日北京时间 13:11:09 是六场原综合目标的截止。符合此截止的最后完整报价为 13:07:09，取回于 13:07:16。原本独立的盘口变化研究使用 14:19:42 的较晚截点，可以使用 14:17 的采样。把它当成 13:11 已知会造成泄漏。下次正常已授权采集如产生带供应商首报标志、原始价格与双时钟的同场双市场证据，才可验证真正的开盘增量；可继续使用现有市场路径研究入口。[决策前末次采集](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329/market/runs/44241910588-1-bra1/source-metadata.json) · [原锁定协议](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329/workspace-updates/20261118-market-signals/movement/protocol.json)

## 离线复算

在本分支源码根目录执行。使用 Python 3.12.14、NumPy 2.3.5、SciPy 1.17.0 和仓库依赖。`DATA_REPO` 为已有固定 Git 对象的数据仓；`OUT` 为新私有输出目录。`AUDIT` 为源码提交 `54ec6d70fdff7145c1889ffe6071b2188663bef2` 的干净检出，用来复用已核实的赛事身份、原始首冻和 90 分钟证据读取器。旧研究的模型系数和成绩不参与计算。

```sh
set -eu
export PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1
P=research/20261124-halftime-balance
mkdir -p "$OUT"
git -C "$DATA_REPO" show a6d45024cf6cf622f05e4b378ab53cfe2d6ae329:workspace-updates/20261123-kpi-optimization/source-assets.tar.gz > "$OUT/source-assets.tar.gz"
AUDIT_PATH="$AUDIT/runtime:$AUDIT/research/20261124-prior-dismissals"
PYTHONPATH=runtime python "$P/study.py" fit --archive "$OUT/source-assets.tar.gz" --out "$OUT"
cmp "$P/fit.json" "$OUT/fit.json"
PYTHONPATH="$AUDIT_PATH" python "$P/clocked_evidence.py" inputs --data-repo "$DATA_REPO" --out "$OUT/clocked-inputs.jsonl"
PYTHONPATH=runtime python "$P/study.py" prepare --archive "$OUT/source-assets.tar.gz" --clocked-inputs "$OUT/clocked-inputs.jsonl" --fit "$OUT/fit.json" --out "$OUT"
cmp "$P/forecast-lock.json" "$OUT/forecast-lock.json"
PYTHONPATH="$AUDIT_PATH" python "$P/clocked_evidence.py" labels --data-repo "$DATA_REPO" --out "$OUT/labels.jsonl"
PYTHONPATH=runtime python "$P/study.py" grade --archive "$OUT/source-assets.tar.gz" --labels "$OUT/labels.jsonl" --run "$OUT" --fit "$OUT/fit.json" --lock "$P/forecast-lock.json" --out "$OUT"
cmp "$P/results.json" "$OUT/results.json"
cmp "$P/coverage.csv" "$OUT/coverage.csv"
PYTHONPATH=runtime python -m unittest discover -s "$P" -p test_study.py -v
python scripts/check_repository.py
python scripts/run_tests.py --layer integration
```

只有单独执行研究命令才会调用本配方。回滚可以删除研究分支，或还原该研究目录和对应两个独立模块。原始市场、历史预测、私人交付证据和生产工作流均未写入本次变更。
