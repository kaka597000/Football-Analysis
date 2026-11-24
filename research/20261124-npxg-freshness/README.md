# 剔除点球是否改善近期球队攻防预测

**本轮结论：停止把最近六场的总 xG/xGA 直接替换成非点球 xG/xGA 的配方。** 在资料较新的 49 场历史同场回放中，矩阵首选和前三均无净增，全比分对数损失从 3.2042 升至 3.2127。11 月更严格的 25 场中，没有一场能从同一来源证明双方最近六场齐全。此次交付的可复用部分是赛前资料时效检查。R7 原登记 192 场继续独立运行。

## 同场回放

仅改变最近六场主客攻防的 xG 输入。以 2020/21–2024/25 五季共 8,982 场按联赛拟合一次泊松回归，系数正则化固定为 0.01。基线复用上一轮已核验的「进球＋总 xG」系数。候选使用同样的比赛、目标进球、权重设置、共同开球前 60 分钟截止、比分矩阵和排序规则。以 10 月 15 日各联赛归档后的七天为固定窗口。两队六场完整时启用候选，其他场逐位复用基线。目标赛果在单独的结算步骤评分。较晚来源版本中已结束的比赛，只有在下一目标截止前已被接收时才进入其历史。两份本地登记保留原文，后续授权开放代码和汇总结果。[方法登记](protocol.json) · [七天窗口登记](october-window.json)

| 10 月 16–21 日，49 场 | 基线 | 非点球版本 | 改对 / 改错 |
|---|---:|---:|---:|
| 研究矩阵首选 | 6/49（12.24%） | 6/49（12.24%） | 0 / 0 |
| 研究矩阵前三 | 11/49（22.45%） | 11/49（22.45%） | 2 / 2 |
| 90 分钟胜平负 | 28/49（57.14%） | 29/49（59.18%） | 1 / 0 |
| 留存亚洲大小球主盘全赢，扣走盘 | 11/23（47.83%） | 11/23（47.83%） | 0 / 0 |
| 全比分平均对数损失，越低越好 | 3.20415 | 3.21273 | 候选高 0.00858 |

38/49 场真正用上了新特征。只看这 38 场，首选 5→5、前三 9→9、胜平负 21→22，对数损失 3.15777→3.16883。另 11 场回退，其中德甲 9 场处在赛季初，法甲 2 场缺同源记录。全部 49 场只有 5 个 UTC 比赛日。按比赛日成组抽样 1,000 次，前三净变化的描述性 95% 区间为 −4.23 至 +4.62 个百分点，胜平负为 0 至 +4.62 个百分点。这个窗口无法据以判定新赛季稳定能力。[分赛事指标与改对改错](evidence/scorecard.json)

实际大小球线来自 25/49 场同场、同机构、同时点可取得的双边主盘快照，最晚捕获距 T−60 还有 8.5 分钟至 26.1 小时。8 份来源回执的原始摘要相符。25 场均缺机构更新时间，精确报价和采集双时钟分母为 0。两臂均为 11 全赢、0 半赢、2 走、2 半输、10 全输；其余 24 场无合格盘线。实发首选、实发三比分的成对交付分母均为 0，赛前精选三场的可计批次为 0。正式阶段一四项目标仍为 20%、40%、65%、70%，精选三场另按各场首选 3/3 验收。

## 最近六场覆盖

一个位置是目标队在该场实际赛前的第 1 至第 6 场同赛事常规时间完赛。两源必须各自验证相同赛事、稳定双方身份、正确主客及数据时间。队名简写用同场有序对手与实际比赛日期交叉校验。FotMob 跨源匹配另要求独立赛历开球时刻完全相同。球队资料年龄按目标日减最近可证比赛日计算，同一球队在不同目标比赛分别计数。欧洲五大联赛的 96 支球队完成了对应。11 月 18 日留存赛历的 500 个完赛主客组合及日期在后续赛历逐一匹配，后续版新增的 48 条只用于审计赛程身份。赛后赛历用于核对分母与身份，预测特征来自各自截止前的原件。

| 赛事 | 10 月双队齐全 / 回放 | 11 月目标场 | 11 月所需位置 | Understat 已核 | FotMob 有采集时钟＋存档 / 仅存档上界 |
|---|---:|---:|---:|---:|---:|
| 英超 | 10/10 | 8 | 96 | 50 | 0 / 4 |
| 西甲 | 11/11 | 1 | 12 | 8 | 2 / 0 |
| 德甲 | 0/9 | 8 | 96 | 34 | 24 / 13 |
| 意甲 | 10/10 | 1 | 12 | 6 | 1 / 0 |
| 法甲 | 7/9 | 7 | 84 | 30 | 13 / 8 |
| 合计 | 38/49 | 25 | 300 | 128 | 40 / 25 |

10 月共 98 个球队窗口，赛季内实际已有的 570 个位置覆盖 568 个，78 个窗口有完整六场，球队最近资料中位数 7 天。原件附记的接收时间在赛前，能独立绑定这些原件内容的最早存档是北京时间 11 月 2 日 11:09，晚于这 49 场。方法和窗口于北京时间 11 月 24 日 15:08、15:13 在读取新窗口目标赛果前本地登记。10 月结果属于有原采集回执的历史开发诊断，前瞻发出场数为 0。[归档时点和覆盖](evidence/coverage.json)

11 月沿用上轮 77 个原排位置。剔除已进入旧 61 场的 42 个和 R7 的 1 个，另有 5 个实际开球超窗、4 个身份未绑定，固定对照分母 25。Understat 10 月 15 日一批覆盖 110/300，加入 10 月 19 日英超、西甲、意甲的三份原始解码快照后覆盖 128/300（83 场不同的历史比赛）。球队最新可证资料年龄中位数 41.5→40.5 天，独立赛历中球队最近比赛的中位数是 13 天。50 个球队窗口分别有 28 个只齐 2 场、18 个齐 3 场、2 个齐 4 场、2 个齐 5 场。10 月 19 日原传输压缩包未留存，三份解码正文与采集清单均由 11 月 2 日存档摘要绑定。[汇总数据](evidence/audit-summary.json)

FotMob 从本地已存比赛原件得到 101 个去重的原生双边非点球值，62 个有逐文件采集时钟和正文哈希，39 个只有存档时间上界。在上述 300 个位置中，65 个通过独立同场核验，涉及 37 场不同历史比赛；其中 40 个位置同时有采集回执，25 个只有可信存档上界。1 个虽能匹配，但在目标截止后才可证。其余 234 个未获确认。32/50 个球队窗口至少有一条记录，这 32 个窗口的资料年龄中位数为 21 天，18 个窗口没有可用资料。两来源分别都为 **0/50 球队齐六场、0/25 场双方齐全**。跨源 xG 的尺度不同，各来源独立计数。

其余 15 项正式赛事保留空的回放分母。欧国联有 29 场去重射门原件，其中 25 场可重建非点球射门 xG，45 队各最多两场。日职、澳超分别留有 6、4 场 FotMob 双边值，欧联有 17 场只有存档时间上界；这些材料缺本窗完整阶段、身份或球队六场序列。其余赛事的本轮留存源也未构成固定比较分母。[逐项 20 赛事及覆盖缺口](evidence/scope-coverage.json)

## 本地接收与下一次验收

已试读一次公开 Understat 英超资源。其 HTTP 200 响应和 robots 响应均为相同的通用 HTML，收到的原生数据为零，其余四个联赛未作实时请求。现有接收器遇到这类策略页会立即停止。手动运行的本地接收器按原始字节摘要追加收据，记录实际读取完成时间。通过独立赛历核验的原生比赛方可进入最近六场。下一次先验证同源双方各六场与回执随时间持续形成；当前替换配方到此结束。

研究代码可在仓库根目录直接进行离线合成验收。项目依赖见根目录的 requirements 文件。

```bash
export PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export PYTHONPATH="$PWD/runtime"
study=research/20261124-npxg-freshness
python -m unittest discover -s "$study" -p test_npxg.py -v
```

历史复算需自备获准使用的原件。设置 `FOOTBALL_DATA_ARCHIVE` 为有指定提交的 Data 仓库，`FOOTBALL_RESTORED_ARCHIVE` 为原归档根目录，`FOOTBALL_PRIOR_STUDY` 为上一轮复现目录（含 `inputs/`、`grade/`、`replay/` 及对应版本的 `source/`）。另需本地赛历日期与有序主客投影、由原件核验的 FotMob 原生字段表，以及有授权的双边大小球快照清单。核心数据源分别是 [10 月原件的存档索引](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/466fd626a47481e70b176a9a69c58224d7d1bf63/workspace-backup-20261102)、[固定数据版本](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329) 和 [已结束的原基线研究](https://github.com/kaka597000/Football-Analysis/tree/8de6fe91629cca642a3c90513aee7bf53134f6b5/research/20261124-prior-deep-completions)。

```bash
python "$study/audit.py" --calendar "$LOCAL_CALENDAR_PROJECTION" --out "$LOCAL_OUTPUT"
python "$study/fotmob_coverage.py" --calendar "$LOCAL_CALENDAR_PROJECTION" --fotmob-canonical "$LOCAL_FOTMOB_NATIVE" --out "$LOCAL_OUTPUT"
python "$study/model_replay.py" --out "$LOCAL_OUTPUT" predict
python "$study/verify_market_receipts.py" --source "$LOCAL_ARCHIVE_QUOTE_INDEX" --out "$LOCAL_OUTPUT/verified-totals.csv"
python "$study/model_replay.py" --out "$LOCAL_OUTPUT" grade --quotes "$LOCAL_OUTPUT/verified-totals.csv"
python "$study/report_tables.py" --out "$LOCAL_OUTPUT" --scope configs/analysis-scope.json
```

预测与结算分两步。原件重放取得的盲态进球率文件 SHA-256 为 `a80377d141a2c647ed5ee2c847a5e8aa46d1fb106aafd91d5b02ecf24fcaffb9`。换用全新输出目录后，14 份结果逐字节一致。[复算文件摘要](evidence/replay-sha256.json)

对下一场的本地只读预检可用以下命令。缓存由授权的人工读取建立，采集失败同样保留回执。独立赛历 CSV 需包含 `fixture_id,home,away,kickoff_at,status`，以完整主客赛季覆盖为准。状态为 `SCHEDULED`、`FT90`、`POSTPONED` 或 `UNKNOWN`。其 JSON 回执需有 `calendar_provider,competition,season,received_at,file,sha256,complete_league_calendar_as_of`，所有时钟带时区。在 T−60 前的预览若仍有待赛中间场次，输出明确原因。

```bash
python "$study/preview_local.py" --cache "$LOCAL_RECEIPTS" --competition eng.1 --season 2026 \
  --understat-id "$SOURCE_FIXTURE_ID" --calendar-meta "$INDEPENDENT_SCHEDULE_RECEIPT" --out "$LOCAL_PREVIEW"
```
