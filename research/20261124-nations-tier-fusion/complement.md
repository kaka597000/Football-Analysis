# 赛前首选究竟能否互补

欧国联 52 个原始决策截点上，球队、当时可得的同机构盘口以及预先选定的 xG 来源，至少有一个首选正确的比赛只有 **10/52**。即使在赛后逐场知道应该选谁，仍有 42 场所有可用首选都错。其中 30 场的正确比分甚至不在任何可用来源的矩阵前三。第一次择源把后段首选从 2/26 提至 3/26，净增 1 场。本次记录保留可复查的选择器，结束在本名单上继续调整。[逐场决策与命中](complement-evidence/fixtures.csv) · [统计与完整分母](complement-evidence/results.json)

## 同场同截点与来源

主表采用原 52 场第一次综合判断的资料截止；2 场没有该项存证，沿用其较早的原球队冻结。主场、客场、赛事分级、赛前状态、开球、90 分钟口径分别核验。早于主表决策的其他签发批次，只有首次预测和其原 Git 提交均已存在才可带入；盘口价格始终取当次原始回包。

| 来源 | 可评分首选 | 身份与证据 |
|---|---:|---|
| 原分级球队攻防 Test | 52 | 11 月 10 日真实首冻。工件、输入、矩阵、签发时钟及原选分相符。 |
| Pinnacle 价格模型 | 40 | 一个回包内的赛前胜平负和真实总球盘，通过机构、双边价格、更新时间、收讫时间、主客与哈希匹配。只用当时价格确定性重放比分分布；价格原件有赛前存证，这个价格模型的首选没有当年实发回执。 |
| 按十月规则选定的 BSD xG 候选 | 8 | 原计划指定 A 级 shotmap-75、B 级 expected-75，均已真实首冻。核验每级原统计响应和进球对照的共同训练名单。A 级训练 213 场、B 级 203 场，各仅 8 场有双边实际过程数据。其余训练行按原配方回退。 |
| 正常作者实际首选 | 0 | 50 场有原综合冻结，45 场签了首选。可读渠道未取得能绑定这些场次的首次可见正文、消息身份和赛前时钟。逐场作者列留空。 |

原 Current 与 Test、赛果刷新和共享节奏候选共享进球数据。逐场表保留它们的选择，独立来源统计只计一个进球来源。52 场历史 Elo 是比赛结束后构建的对照，不能追认为当时的另一票。训练和评分均限定在 11 月 12–17 日原名单，数据读取入口分离，本轮模型计算没有加载 R7 队列。

## 各来源的独中与共同漏判

“事后首选上界”只问真实比分是否恰好等于当场至少一个来源的第一比分。它需要知道赛果才能挑出命中的来源。单一来源覆盖的场次不会给该来源记独中。矩阵前三和实际展示三比分分别评价。

| 名单及原截点 | 相同来源配对数 | 球队独中 | 盘口独中 | 共同中 | 共同错 | 事后首选上界 |
|---|---:|---:|---:|---:|---:|---:|
| 原综合截点，球队与盘口 | 40 | 3 | 3 | 1 | 33 | 7/40 |
| 上述前段 | 24 | 3 | 2 | 1 | 18 | 6/24 |
| 上述后段 | 16 | 0 | 1 | 0 | 15 | 1/16 |
| 11 月 16 日修复原截点，刷新球队与盘口 | 18 | 1 | 1 | 2 | 14 | 4/18 |
| 11 月 17 日 xG 原截点，匹配的纯进球队与盘口 | 8 | 1 | 0 | 2 | 5 | 3/8 |

18 场与 8 场两组有 8 场重叠，全部属于主表 52 场。各用自己的同场赛前盘口与模型，不能相加。主表的 40 场有球队和盘口，8 场有球队和 xG，4 场只有球队，三个来源在同一个原综合节点共同齐备为 0 场。切回 11 月 17 日原 xG 批次，才有 **8 场三方输入齐备**。该节点 2 场三方都中、1 场球队和 xG 同中、5 场全错，三方各自独中均为 0。同一比赛另一个截点的早盘没有进入主表。

在主表，球队中 6/52，市场中 4/40，xG 中 3/8。各自在当时可得来源中独中的场数为 3、3、1，另有 3 场多源同中。xG 相对 11 月 10 日旧球队预测多覆盖了一个正确首选；对照同批更新的纯进球模型，两者同中 3/8，各独中 0/8。该次 xG 改了一个首选，双方都错。11 月 16 日修复候选相对同批纯进球对照有 1 场独中，但少中 2 场；与同节点球队和市场一起看，其新增独中是 0。

若把同源 Current 等版本也摆入事后选择池，主表上界会从 10/52 变为 **12/52**。这两次额外命中来自原 Current，只说明它偶尔与 Test 分歧命中。没有把这两个已见案例作为再选规则的训练标签。以 20% 为参照，52 场需要至少中 11 场；仅从独立来源的首选中择一，在本名单最多中 10 场。连同所有旧版本则有算术空间，如何在赛前识别它尚无证据。

## 预先锁定的一次择源

1. 先登记来源、赛制、时点、早晚日期、特征和停止条件。用球队与盘口各自给两个不同首选的概率比，判断哪个来源对自己的选择支持得更强。整个选择器只用这一项赛前特征。[协议](complement-protocol.json) · [登记提交](https://github.com/kaka597000/Football-Analysis/commit/b4f0523f3ead6971c4345e323f83755ca53ac48f)
2. 早段为原 UTC 开球日期 11 月 12–14 日。到北京时间 11 月 15 日 16:00 已入库的可信标签为 26/26，其中 24 场有双输入，17 场首选不同。盘口支持更强的 8 场中，盘口独中 2、球队独中 1、双方都错 5；其余 9 场分别为 0、2、7。预先规定同档至少 4 个分歧、跨两日、盘口至少独中 2 场且超过球队独中，才允许选盘口。其余保留球队。[26 场后段逐场选择锁](https://github.com/kaka597000/Football-Analysis/commit/f788ea3e382e95cea39c693ef0b1cb6db2c23cd2)
3. 后段为原 UTC 开球日期 11 月 15–17 日。26 场全部锁定，大名单中 3 场选盘口，23 场选球队。所有主表与原批次选分及来源指纹一并推送后，才运行本审计的后段评分。两次决策都有明确开球前来源，但比赛本身及部分汇总已被更早项目研究看过，证据属于时间外历史回放。[来源与预测锁](https://github.com/kaka597000/Football-Analysis/commit/3a2803d3136ae025136b2fe3ddd869e32fc9920b)

| 后段 | 原球队 Test | 事前固定择源 | 改对 / 改错 |
|---|---:|---:|---:|
| 第一比分 | 2/26 | 3/26 | 1 / 0 |
| 矩阵前三 | 7/26 | 7/26 | 0 / 0 |
| 胜平负 | 16/26 | 16/26 | 0 / 0 |
| 实际盘大小球全赢 | 8/19 | 10/19 | 2 / 0 |

21 场有核准原盘，扣除 2 场走盘后严格全赢分母为 19。全赢/半赢/走盘/半输/全输，球队为 8/1/2/1/9，择源为 10/1/2/1/7。三个改选中只有一场改对，双侧成对精确检验 p=1。单一国际比赛窗口不足以识别稳定收益。实际展示首选、展示三比分及精选三场批次的可结算分母均为零。

## 巴甲独立回放

在原三臂研究的 6 场中，所有比赛都有同截点的原球队、双市场报价及认证赛果。原球队和纯盘口都中 0/6，二者 6 场全错。将已登记的两条同源校准配方也算入敏感性比较，市场校准中 1/6，赛事分组校准中 0/6，四种选分共同漏掉 5 场。唯一成功的是弗拉门戈 2–1 巴拉纳竞技。该独立研究旧批次与用户收到的巴甲比分表分别计数。赛前原数据提交为 `aa565df1`，认证赛果为 `69821e4c`。纯盘口的首选是从同一份开球前 Pinnacle 原始响应回放所得；输入选择 SHA-256 `5aa46d7bf5eb402fcf96bd5c20b8052e8debc89b412907d06c2906fa336dccf2` 在本回放读取标签前已封存。[巴甲逐场 6 行](complement-evidence/brazil-fixtures.csv)

## 九个俱乐部联赛的补充清点

这组 11 月 7–8 日的旧档只做逐场互补清点，从未用于欧国联择源规则的拟合或测试。先锁定综合首冻和同截点的 Pinnacle 两个市场，再逐场追溯 `current-test-pair` 自己的第一次冻结、来源时间、主客、矩阵哈希与两臂明示首选。65/65 场双模型首冻都已在综合判断前签发。65 场 Current 均标记盘口数值调整关闭。实际可见作者首发的有效分母为 0。综合冻结综合了球队及市场输入，占单独一列。[输入与代码先行锁](https://github.com/kaka597000/Football-Analysis/commit/1b8e7df2e3152503b6a6e6b3fecf9056e17a6a68) · [逐场原选择、球队和认证赛果](complement-evidence/club-fixtures.csv)

- 两份原始名册分别登记 55 和 46 场。能核到正式范围、综合首选、原生赛前阶段、同机构同截点双盘的分别为 27、38 场；两份原名册有一场相同，65 场有效配对间没有重叠。五大联赛后续包的 20 条有效记录全部重复。所有 65 场的原 Git 包均在开球前提交，所有常规赛果均有归档 ESPN 原件和认证记录。英超 8、西甲 7、德甲 8、意甲 7、法甲 7、荷甲 7、美职联 10、挪超 6、瑞典超 5。其余赛事本清点未覆盖。
- 65 场里，球队 Current 中 7、纯盘口中 4，球队独中 5、盘口独中 2、共同中 2、共同漏 56；事后逐场择优上界 **9/65**。7 日为 6/27，8 日为 3/38。两个旧模型共用球队输入，Test 中 6 场，6 场均被 Current 同时命中，Test 独中 0。
- 原综合冻结中 8/65，其中 3 场是与 Current、Test、盘口一起比较时只有综合冻结选中的。四者全算，事后上界为 **12/65**，仍有 53 场全错。本名单达到首选 20% 至少要 13 场。综合冻结首选是可追溯的研究信息，实际向用户首发的正文和顺序仍需服务回执确认。[按批次、联赛和来源统计](complement-evidence/club-results.json)

## 下一项数据缺口

先补原负责人正常发送时留下的整文与首发回执，才可让作者首选进入同场诊断。模型侧要解决的是让正确比分出现在首选候选池。评分后按常规时间总球数拆开 30 场所有来源矩阵前三都漏掉的比赛，其中 17 场打进至少 4 球，9 场恰好 3 球，还有 4 场最多 2 球。仅重排原有首选无法补回这些比赛，统一加球也解释不了全部漏判。本次 xG 原批次只有 A、B 两级，每级仅 8 条真正的双边过程训练记录。同一将来决策截点能否同时留下近期过程证据、球队分布、具名盘口和作者实发，是下一项可核的数据缺口。保留本次选择器的唯一配方作复核记录，结束本名单上的选参，不再增加常驻对照。

## 离线复现

准备 Python 3.12 和源码 requirements.txt 固定的数值库。DATA 指向拥有上述原提交的项目数据 Git 对象库。源码 Git 对象库需要包含 #72 和本草稿的固定提交。命令使用全新的输出目录，欧国联的 grade 和两个俱乐部附表的 score 或 score_review 步骤才会读取各自评分标签。

```sh
export DATA=/path/to/kaka597000-Football-Analysis-Data
export PYTHONPATH="$PWD/runtime" OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
S=research/20261124-nations-tier-fusion
OUT=$(mktemp -d)
python "$S/complement_run.py" selector --data "$DATA" --out "$OUT/selector.json"
cmp "$S/complement-selector-lock.json" "$OUT/selector.json"
python "$S/complement_run.py" seal --data "$DATA" --out "$OUT/panels.json"
cmp "$S/complement-panels-lock.json" "$OUT/panels.json"
python "$S/complement_run.py" grade --data "$DATA" --seal-commit 3a2803d3136ae025136b2fe3ddd869e32fc9920b --out "$OUT/grade"
cmp "$S/complement-evidence/results.json" "$OUT/grade/results.json"
cmp "$S/complement-evidence/fixtures.csv" "$OUT/grade/fixtures.csv"
python "$S/brazil_sources.py" choices --data "$DATA" --lock "$OUT/brazil-lock.jsonl"
python "$S/brazil_sources.py" score --data "$DATA" --lock "$OUT/brazil-lock.jsonl" --out "$OUT/brazil.csv"
cmp "$S/complement-evidence/brazil-fixtures.csv" "$OUT/brazil.csv"
git show 156fc1f29890c03cc07e5a21471442d9760d407f:research/20261124-same-line-motion/export_clean_native.py > "$OUT/club-stage.py"
python "$S/club_first_sources.py" --data "$DATA" --stage-adapter "$OUT/club-stage.py" --out "$OUT/club-lock.json"
cmp "$S/complement-evidence/club-first-source-lock.json" "$OUT/club-lock.json"
python "$S/club_score_review.py" --data "$DATA" --lock-commit 1b8e7df2e3152503b6a6e6b3fecf9056e17a6a68 --out "$OUT/club-grade"
cmp "$S/complement-evidence/club-fixtures.csv" "$OUT/club-grade/club-fixtures.csv"
cmp "$S/complement-evidence/club-results.json" "$OUT/club-grade/club-results.json"
python -m unittest discover -v -s "$S"
python scripts/run_tests.py --layer integration
```

逐场 CSV 的 78 行对应 52 场及其部分比赛的另外两个原截点，panel、比赛 ID、截点三者共同定位一行。报价、原始供应商数据和私人渠道正文留在原证据处。结果仅发布项目派生的比分选择、命中标记、证据身份和汇总。
