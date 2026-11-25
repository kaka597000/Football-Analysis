# 定位球射门位置与机会质量的赛前覆盖

这次核查的是信息在原预测截止时是否可用。每场要求主客队各自最近三场成年正式比赛均可识别，且其中每次非点球定位球射门都能核对来源、接收时间、球队、比赛身份、原生场景、坐标和 xG。原前三取各自已有赛前版本，赛果只用于给漏判分组。后段缺少能独立检验的双边样本，本轮没有拟合模型、创建预测或启动前瞻队列。

## 原前三漏判的覆盖

每格为「两队资料齐全的漏判场数 / 该档全部漏判场数」。档位按两队常规时间总进球数划分。「—」表示该档没有漏判样本。同一晚作者实际展示与模型矩阵分轨，勿相加。

| 样本与原轨道 | 全部场数 | 全部漏判 | 0–2 球漏判 | 恰好 3 球漏判 | 至少 4 球漏判 |
|---|---:|---:|---:|---:|---:|
| 2024、2025、2026 年 2 月，五大联赛 Current 历史重建 | 588 | 0/408 | 0/128 | 0/96 | 0/184 |
| 10 月 19 日，范围内原 Current、有认证赛果 | 1 | 1/1 | — | 1/1 | — |
| 10 月 24 日，范围内原 Current | 22 | 11/11 | 5/5 | 3/3 | 3/3 |
| 10 月 25 日，范围内原 Current、有认证赛果 | 20 | 13/13 | 5/5 | 4/4 | 4/4 |
| 10 月 26 日，范围内原 Current | 2 | 1/2 | 1/1 | — | 0/1 |
| 11 月 4 日欧冠及 6 日澳超，原球队矩阵 | 10 | 4/8 | — | 1/1 | 3/7 |
| 11 月 21 日，原首版 Current | 9 | 0/7 | 0/3 | 0/1 | 0/3 |
| 11 月 24 日晚场，原作者正文三比分 | 12 | 0/9 | 0/4 | 0/1 | 0/4 |
| 同一批晚场，原 Current 矩阵 | 12 | 0/11 | 0/6 | 0/1 | 0/4 |

10 月 24–26 日的三批合计列出 52 场，其中 45 场有范围内原 Current。43/45 场有双方完整的定位球资料，44/45 场有可复核常规时间赛果，42/44 场同时具备两者。10 月 24 日的一场巴甲两源开球差一小时，保留其原模型成绩，过程数据列缺。10 月 25 日的一场美职联保有完整前赛资料，两份复盘和已核赛季归档仍是赛前状态，赛果留空。这三批的结果此前已复盘，只能用作开发覆盖诊断。

588 场逐一对回固定赛季 CSV 的联赛、日期、主客队、比分及上轮封存的排名。每年 2 月 1 日为该历史月块的日期级研究截止。现存赛季角球/射门计数、Understat 球队聚合和更晚捕获的过去比赛详情不能提供当时的逐射位置与 xG。11 月 21 日的 9 场另有精确首版截止。覆盖“零”限于已核的项目保有物。[上轮赛季窗口和首版清单](https://github.com/kaka597000/Football-Analysis/blob/2ced663ec1a90367270d643ff6044d6b61b009e1/research/20261125-corner-opportunities/README.md)

11 月 24 日晚场，12 场 24 队的原 ESPN 摘要列出 72 个近期比赛位置。仅 6/72 能与已核历史来源清单的详情精确配对，六处射门图均为空。另有一支亚冠球队的较新赛历与摘要不同，摘要本身也不能认证最近三场的顺序。欧冠原摘要的双边 npxG 为 49/54 个位置，双方近三场齐全 5/9 场；它没有逐射原生场景、x/y 与逐射 xG 的已保存序列。另两个亚冠和一个巴甲目标的实物、时间与身份也逐项核对。赛程、原页面 URL 或摘要值各自保留其证据等级。作者命中位置源自已发正文回读，严格逐场首次可见回执仍缺。[原摘要的字段审计](https://github.com/kaka597000/Football-Analysis/blob/ce49b42f49351f7cd0c65faef7a9cdbd7d5e9d7c/research/20261124-chance-quality/evidence/current-intake.json)

## 能核实的射门原件

复用 #78 的两份已封存球队/盘口分布、原球队赛历读取器、射门分母与 90 分钟评分器。新增检查核对日历比赛与原生比赛的赛事 ID、两队有序身份及定位球 x/y。原生场景保留 FromCorner、FreeKick、SetPiece、ThrowInSetPiece，剔除点球、乌龙球。完整的零次定位球和没有射门图分别记录。目标自己的赛后射门不作输入。[原研究及来源清单](https://github.com/kaka597000/Football-Analysis/pull/78)

- 10 场中两队完整 5 场，单队完整 14/20。60 个近期位置中 51 个完整、6 个缺图、3 个常规时间不合格。完整位置涉及 49 场不同的过去比赛。
- 49 场既往比赛共核实 319 次定位球射门有原生 x/y 和 xG。角球产生 203 次、直接任意球 30 次、其他定位球 66 次、界外球定位球 20 次。231/319 次来源标为禁区内。原射门质量仅能说明保有数据的粒度。
- 欧冠的本地收讫索引在原截止前，保有该索引的 Git 包于北京时间 11 月 5 日 13:53 提交。澳超原件有各自接收时钟，Git 包于北京时间 11 月 6 日 16:15 提交，晚于 16:05 的原模型截止、早于开球。两包均不能提供早于模型截止的独立 Git 见证。
- 11 月 17 日欧国联八场另核 758 条历史和 148 条机会索引。25 份非估算、与原生射门数一致的记录中，仅 14/16 支目标队各匹配到一场，双方完整 0/8。此处未以另一模型轨补算 Current 命中。[欧国联后段证据](https://github.com/kaka597000/Football-Analysis/blob/ce49b42f49351f7cd0c65faef7a9cdbd7d5e9d7c/research/20261124-chance-quality/evidence/nations-coverage.json)

10 月 19 日另有四场赛历，在当前 20 项范围内的是两场。两场均保存双方各三场的可核射门原件。该日的复盘另按常规时间来源检查，瑞超一场的认证终场为 1–2，原前三漏判。巴甲同批两份复盘原件均仍在下半场，故原复盘口径保留待定。10 月 30–31 日还可找到其他前赛文件，文件数没有计入已认证分母。10 月各批及 #78 的结果此前已接触；按日期重新排列无法产生独立未见验证。

复查 10 月 24–26 日时，另以原首冻及原复盘摘要、ESPN 的有序球队、开球和上下半场合计逐场对回。52 行私有账 SHA-256 为 `c188b050c68657da48e089bf274ccac96d3e12397500a08890d3ef2d13c3b8a0`。命中分组使用同一个 `coverage_ledger.py`。三批原收讫时间均是归档记录的来源时钟，缺独立时钟时仍标历史覆盖。[汇总及各档不可用场数](coverage.json)

## 正常读源时保存证据

`keep_prior.py` 是手动调用的本地研究入口，输入取自已经授权的正常读取。程序自己没有网络请求。收到原页面字节时，用原服务 URL、请求/收讫时钟、HTTP 状态和 SHA-256 交给 #78 的既有留存器。没有原生球队赛历时也能保存原件，输出 `raw_only` 和零个合格位置。不能用文件 mtime 或事后重取的网页补出当时的页。新入口未接入采集、发送、训练或生产。若需统计某个原截止的可用性，必须保有届时已成立的关联或独立原件时钟；单独保存一次页面不会形成目标场次成绩。

给定同源且带回执的球队赛历时，程序再核目标主客、赛程开球和赛事 ID，以及前赛在该队最近三场中的位置。准入逐射前核实常规时间、全图与两队总射门分母，然后核验定位球分型和坐标。通过后在私有目录登记原件、原赛历的内容摘要与不可覆盖的关联回执。超过截止的重启只能读回已存在的同一关联；失件保持缺证。这是本地赛前证据，实际计分仍须与原模型或正文的截止、赛事身份相配。整场还须匹配真实首次冻结的比赛身份和截止，六个球队/顺位位置各有唯一且无矛盾的原件。

`--target` 取原读取计划中的 FotMob 目标 `id`、有序 `home`/`away`、原生 `league_id`、含时区的 `kickoff`/`cutoff`。URL 中的 `matchId` 必须等于 `--prior-id`。完整解析支持含原生 `general/header/content` 的单一 `__NEXT_DATA__.props.pageProps`；真实历史输入是原生 API JSON，渲染 HTML 接线用明确标注的合成输入测试。

```bash
export PYTHONPATH="$SOURCE78/research/20261124-chance-quality:$SOURCE78/runtime"
export PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
study=research/20261125-set-piece-evidence
python "$study/keep_prior.py" --raw "$NORMAL_PAGE_BYTES" --receipt "$RESPONSE_RECEIPT" \
  --target "$TARGET" --prior-id "$PRIOR_ID" --private-dir "$PRIVATE_CACHE"
# 在同一原截止前，如已有原球队赛历，可给同一命令添上以下三项
# --calendar-index "$CALENDAR_INDEX" --calendar-raw-dir "$CALENDAR_RAW_DIR" --team "$TEAM_ID"
```

## 离线复核

`SOURCE78` 为源码提交 `ce49b42f49351f7cd0c65faef7a9cdbd7d5e9d7c` 的干净 checkout。`DATA` 为含原始提交 `a6d45024cf6cf622f05e4b378ab53cfe2d6ae329` 的数据对象库。每次用新的私有输出目录。真实原件与逐射记录仅在获准的本地保有物上读取。

```bash
old="$SOURCE78/research/20261124-chance-quality"
python "$old/replay.py" --data-repo "$DATA" --out "$OUT/ucl.json"
python "$old/australia.py" --data-repo "$DATA" --out "$OUT/aus.json"
python "$old/grade.py" --data-repo "$DATA" --blind "$OUT/ucl.json" --australia-blind "$OUT/aus.json"
python "$study/audit.py" --data-repo "$DATA" --ucl "$OUT/ucl.json" --aus "$OUT/aus.json" \
  --summary "$OUT/coverage.json" --private-ledger "$OUT/slots.json"
python "$old/later.py" --data-repo "$DATA"
python -m unittest discover -s "$study" -v
python scripts/check_repository.py
python scripts/run_tests.py --layer integration
```

`coverage_ledger.py --help` 给出全资料持有人合并历史目标身份、近期射门审计和原成绩所需的各项本地输入。公开分支只保留代码、合成测试与汇总数字。撤回本研究目录和测试入口即可回滚。
