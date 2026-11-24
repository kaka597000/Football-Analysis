# 同机构大小球变价能否改善第一比分

**结论：结束本配方，不申请新的长期前瞻。** DraftKings 可认证的 95 场同线比赛，矩阵第一比分多中 1 场，实际盘线大小球全赢少 2 场，比分对数损失从 2.96617 升到 2.97399。首选净差按 10 个北京时间比赛日整群重抽，95% 区间为 0 至 +3.03 个百分点。预登记需要至少 100 场、区间下限大于零、大小球与概率质量都不退步。

| 时间外历史回放 | 配对分母 | 矩阵第一比分 | 矩阵前三 | 90 分钟胜平负 | 实盘大小球全赢 | 比分对数损失 |
|---|---:|---:|---:|---:|---:|---:|
| DraftKings，同线主比较 | 95 | 10 → 11 | 30 → 31 | 56 → 56 | 49 → 47 | 2.96617 → 2.97399 |
| DraftKings，含移盘时的原样回退 | 119 | 13 → 14 | 37 → 38 | 70 → 70 | 59 → 57 | 2.97600 → 2.98224 |
| Bet365，六联赛历史收盘列 | 2,234 | 299 → 298 | 752 → 758 | 1,244 → 1,244 | 1,372 → 1,369 | 2.89309 → 2.89285 |

每格按基线 → 候选列命中场数，末列越低越好。95 场中首选只变了 2 场，改对 1、改错 0，另 1 场两臂都错；矩阵前三改对 2、改错 1；大小球改对 1、改错 3。Bet365 首选改对 4、改错 5；矩阵前三改对 11、改错 5；大小球改对 17、改错 20。两家机构分别拟合，系数方向相反。剔除项目看过的整批 11 月 22 日预测后，另 71 场同线结果为首选 6 → 7、矩阵前三 24 → 25、胜平负 44 → 44、大小球 40 → 37。该子表仅用于显示旧批次贡献，主分母始终保留。

所有后段大小球可结算对均为全赢或全输，走盘和半赢为零。胜平负边际是模型的固定约束。原名册 171 场中，51 场没有可比较的当次预测；其余 120 场有 119 场核准赛果。一场挪威比赛的首冻开球为 11 月 22 日 16:00 UTC，赛果证据为 18:00 UTC，留作未结。每项赛事的名册、可预测、同线、可结算及命中/改对改错见 [覆盖 CSV](results/competition-coverage.csv)。CSV 中没有入样的赛事保留覆盖数，命中指标留空。[DraftKings 指标及逐日汇总](results/native-metrics.json) · [Bet365 指标](results/proxy-metrics.json)

本研究比较同一场、同一截点的两套比分矩阵。基线只用当时同一家机构的胜平负和实际大小球两边价格。候选仅增加同一条盘线从开盘字段到本次抓取时的价格变化，对胜平负边际施加相同约束。缺开盘或移盘时，两臂使用同一基线矩阵。缺当次完整市场时两臂共同弃权。

此前的市场路径研究使用固定权重调整多项市场信息。本轮只拟合一个大小球系数，可以逐场计算相对当次市场水平的增量。半场配方沿用 [#70 的原研究结论及盘口附录更正](https://github.com/kaka597000/Football-Analysis/pull/70)。

2026 年 11 月 24 日北京时间 19:26 在提交 `2c57739ec4b9ae2d517eaa662c705d51a6c0c5df` 登记纳入范围、时间切分、唯一模型和判定。20:03 将两臂系数、全体 ID 对应的预测与矩阵哈希及所有弃权的摘要提交并推送到 `510524ebd35d75955074897b90542d971a7fe7f4`，随后读取后段标签。原始预登记和逐项锁见 [protocol.json](protocol.json) 与 [locks](locks/)。

## 报价证据和时间窗口

- **DraftKings** 使用 ESPN 原始响应。发行人的综合首冻或原采集回执将响应字节 SHA、比赛、主客和开球绑定到原决策截止前。原响应中的 `open` 和 `close` 分别提供开盘字段和当次可见价。仅能认证响应的抓取时钟，逐笔更新时间和最终收盘时刻均未知。
- 早段从 11 月 5 日开始，到北京时间 11 月 8 日 12:00。91 场名册有 59 场同盘线，其中 43 场的常规时间赛果在切分前已认证，拟合系数为 −0.487854。另 16 场保留在覆盖记录。
- 后段使用切分之后的原首冻及北京时间 11 月 24 日零时前的开球。两段合计 276 个不同比赛身份，91 个早段、171 个后段、14 个在窗口外且匹配 R7。逐项核验了原有 192 场 R7 冻结。后段 120 场可出两臂矩阵，其中 96 场同线、24 场移盘回退，51 场共同缺席。96 场分布于 10 个北京时间比赛日。
- 11 月 7 日原 55 场批次有 48 场留存完整两市报价，其中包括范围外赛事。11 月 8 日的后续五大联赛 22 场均在夜场名册内，按原首冻去重。11 月 18 日预登记路径是工程回放包；另外的六场巴甲三臂研究不属于综合首冻名册。
- 11 月 20–21 日 33 个原首冻中，31 场共引用 39 个不同 ESPN 响应哈希。42 条来源引用均有早于决策的查询时钟，核过的 KPI 包、GAP 输入包和 GAP 原始包没有匹配的响应字节。另两场没有 ESPN 原始引用。这些场次未取用其它机构的开盘价。
- 11 月 22 日的 33 个原首冻均签入了同场 ESPN 记分板的响应哈希和查询时钟，29 场完整且符合范围，其中 25 场同盘、4 场移盘。3 场 MLS 季后赛超出范围，1 场没有完整 DraftKings 报价。11 月 23 日使用原首冻、独立抓取索引和原本地响应字节三方匹配，有一场在后段窗口内且同线。原响应未随本研究再发布。
- **Bet365 历史开发对照** 独立使用六个联赛的开盘采样和收盘列，同列为实际报价的 2.5 线。2019/20 至 2021/22 赛季训练，6,221 场中 6,209 场合格，系数 +0.128225。2023、2024、2025 年各 8–11 月的 2,234 场均可配对。来源缺精确抓取时钟，只支持日期隔离的历史诊断。

项目成员过去已接触部分比赛，尤其是 11 月 22 日批次。先行证据盘点也曾在独立审计过程中读到 11 月 17 日复盘摘要及 11 月 22 日一个赛后字段。早段标签盘点曾解码 18 行晚于训练截止的早段赛果，最终提取在解码前将它们剔除。上述数据均未用于选择配方或调参。两臂是事后按原截点复演，本次没有向用户实际展示两臂的比分或锁定精选三场。真实展示首选、真实展示三选、预选三场均无配对分母。

## 复现

在源码根目录安装 `requirements-integration.txt`，设置 `PYTHONPATH=runtime` 与 `OPENBLAS_NUM_THREADS=1`。需要含所列不可变对象的 Data 仓库；11 月 22 日和 23 日的交付包还需 Data 提交 `87e3ad5ba551714dbf840bae84335d3bf619a50a`、`e958e8e09e062d147e98714a466d69ff6d35ca56`。在命令中将 `DATA` 设为该仓库，`OUT` 设为新的本地目录，`BOARDS23` 设为原交付方保留的 11 月 23 日 `schedule/scoreboards.json`；缺该文件时去掉 `--nov23-raw`，该批会明确留缺，完整预测哈希将不同。

```sh
export PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1
S=research/20261124-same-line-motion
DATA_PIN=a6d45024cf6cf622f05e4b378ab53cfe2d6ae329
mkdir -p "$OUT/native" "$OUT/proxy"
git -C "$DATA" show "$DATA_PIN:workspace-updates/20261123-prematch-literature/first-issues.tar.gz" > "$OUT/r7.tgz"
git -C "$DATA" show "$DATA_PIN:workspace-updates/20261123-kpi-optimization/source-assets.tar.gz" > "$OUT/assets.tgz"
python "$S/early_boards.py" --data "$DATA" --out "$OUT/early.jsonl"
python "$S/export_clean_native.py" --data "$DATA" --source "$PWD" --out "$OUT/slates.jsonl"
python "$S/side_rosters.py" --data "$DATA" --out "$OUT/side.jsonl"
python "$S/verify_early_20261120_21.py" --data "$DATA" --out "$OUT/nov20.jsonl"
python "$S/late_boards.py" --data "$DATA" --nov23-raw "$BOARDS23" --out "$OUT/late.jsonl"
python "$S/assemble_native.py" --r7 "$OUT/r7.tgz" --out "$OUT/native" \
  "$OUT/early.jsonl" "$OUT/slates.jsonl" "$OUT/side.jsonl" "$OUT/nov20.jsonl" "$OUT/late.jsonl"
python "$S/proxy.py" inputs --assets "$OUT/assets.tgz" --out "$OUT/proxy/inputs.jsonl"
python "$S/proxy.py" train-labels --assets "$OUT/assets.tgz" --out "$OUT/proxy/train-labels.jsonl"
```

早段赛果使用 Data `b1d6dbc3b4df4bd098250a5f236382426b0e89a4` 的完整签出目录 `TRAIN_DATA` 以及源码 `783e02ddebd427383dbdd6a9e4b359dc68c13d8b` 的签出目录 `VERIFY_SOURCE`。命令会核对标准化结果 blob、实际两节 90 分钟原始证据及正常采集回执。

```sh
python "$S/build_training_roster.py" --archive-root "$TRAIN_DATA/workspace-updates" --out "$OUT/train-roster.jsonl"
python "$S/extract_labels.py" --data "$TRAIN_DATA" --results-repo "$TRAIN_DATA" \
  --data-commit b1d6dbc3b4df4bd098250a5f236382426b0e89a4 --source-repo "$VERIFY_SOURCE" \
  --source-commit 783e02ddebd427383dbdd6a9e4b359dc68c13d8b --inputs "$OUT/train-roster.jsonl" \
  --train --runs "$TRAIN_DATA/runs" --out "$OUT/native/train-labels.jsonl" --report "$OUT/native/train-labels-summary.json"
for PANEL in native proxy; do
  python "$S/study.py" fit --panel "$PANEL" --inputs "$OUT/$PANEL/inputs.jsonl" \
    --labels "$OUT/$PANEL/train-labels.jsonl" --out "$OUT/$PANEL/fit.json"
  python "$S/study.py" seal --panel "$PANEL" --inputs "$OUT/$PANEL/inputs.jsonl" \
    --fit "$OUT/$PANEL/fit.json" --out "$OUT/$PANEL"
  cmp "$OUT/$PANEL/lock.json" "$S/locks/$PANEL-lock.json"
done
python "$S/proxy.py" test-labels --assets "$OUT/assets.tgz" --out "$OUT/proxy/test-labels.jsonl"
```

后段 119 场来自三个可单独复验的集合。常规采集的最新固定 Data 快照 `076fe6d1a83281cb9393ee4887d1deced995eb0a` 给出 68 场，固定 `a6d45024` 的同 68 场逐项相符。11 月 8 日的 40 场另有 40 个独立抓取的原始摘要，核对均一致。另有 10 个固定归档提供欧国联 48、巴甲 2 场的同场、分阶段、两节终场和逐响应捕获时间。

第 119 场是亚冠精英联赛阶段 `espn:401912612`。在上述 Data 快照的请求日志中，原始记分板与摘要抓取于 11 月 24 日 09:53:04 和 09:53:05 UTC，各自哈希均可还原。两者同年、同阶段、同队、同开球。使用既有 [配对认证入口](https://github.com/kaka597000/Football-Analysis/blob/d905fe4d02d06a7d1a0ed4df3d70b16f020305b8/runtime/research/display_review.py#L39) 并独立核算上下半场，通过的是 90 分钟 2–2。生产快照仍将这场隔离，此次只在影子研究中使用该证据。所有研究赛果的采集均早于预登记提交时刻。

设置 `RESULT_DATA` 为上述 `076fe6d1` 的完整 Data 签出目录，`ADAPTER` 为源码提交 `d905fe4d02d06a7d1a0ed4df3d70b16f020305b8` 的干净签出目录。常规结果核验器在预测封存提交与本草稿中的文件内容相同。

```sh
python "$S/extract_labels.py" --data "$RESULT_DATA" --results-repo "$DATA" \
  --data-commit 076fe6d1a83281cb9393ee4887d1deced995eb0a --source-repo "$PWD" \
  --source-commit 510524ebd35d75955074897b90542d971a7fe7f4 --inputs "$OUT/native/inputs.jsonl" \
  --test --lock "$OUT/native/lock.json" --protocol "$S/protocol.json" --runs "$RESULT_DATA/runs" \
  --out "$OUT/native/normalized.jsonl" --report "$OUT/native/normalized-report.json"
python "$S/rebuild_late_labels.py" --data "$DATA" --inputs "$OUT/native/inputs.jsonl" \
  --lock "$OUT/native/lock.json" --out "$OUT/native/cups.jsonl" --report "$OUT/native/cups-report.json"
python "$S/archived_afc_label.py" --data "$DATA" --adapter "$ADAPTER" --inputs "$OUT/native/inputs.jsonl" \
  --lock "$OUT/native/lock.json" --out "$OUT/native/afc.jsonl"
python "$S/join_labels.py" --inputs "$OUT/native/inputs.jsonl" --lock "$OUT/native/lock.json" \
  --out "$OUT/native/test-labels.jsonl" "$OUT/native/normalized.jsonl" "$OUT/native/cups.jsonl" "$OUT/native/afc.jsonl"
for PANEL in native proxy; do
  python "$S/study.py" grade --panel "$PANEL" --inputs "$OUT/$PANEL/inputs.jsonl" \
    --labels "$OUT/$PANEL/test-labels.jsonl" --lock "$OUT/$PANEL/lock.json" --out "$OUT/$PANEL/results.json"
done
python "$S/export_coverage.py" --inputs "$OUT/native/inputs.jsonl" --native "$OUT/native/results.json" \
  --proxy "$OUT/proxy/results.json" --out "$OUT/public"
diff -r "$OUT/public" "$S/results"
python -m unittest discover -s "$S" -p 'test_*.py'
python scripts/run_tests.py --layer integration
```
