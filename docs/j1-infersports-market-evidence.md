# 日职联赛前盘口证据

日职联常规赛可沿用 `scripts/capture_public_club_markets.py` 获取公开的 InferSports 盘口。赛历对象的联盟为 `lg_VWS270YX`、`Jap1`。采集以 ESPN 的原登记比赛、已核实的两源球队 ID 和准确开球时刻匹配唯一的提供商赛事。球队对照的字段为 `espn_name`、`provider_name`、`provider_id`，按联赛与 ESPN 球队 ID 索引。

```bash
python scripts/capture_public_club_markets.py \
  --fixtures CURRENT-FIXTURE-INPUTS.json --team-ids VERIFIED-TEAM-IDS.json \
  --archive-root PATH-TO-BATCH/evidence \
  --out PATH-TO-BATCH/evidence/market-dec04_06
```

12 月两窗分别在自己的赛前截止前采集。第二窗使用同一 `--archive-root` 和新的 `--out` 子目录，例如 `market-dec12_13`。原件路径从共同的 `evidence/` 解析，两个窗口各自保存采集清单和准备好的比赛。单次批采集可仅传 `--out`，该目录同时作为原件路径的根。

`prepared-fixtures.json` 保留全部输入场次。通过检查的报价携带原始赛历及赔率的 URL、文件哈希和实际接收时点。大小球采用实际盘线及大小两侧价格。同机构 1X2 按原响应核对三项价格。两类市场各自保留提供商行级 `as_of`。仅 `stale=false`、全场、开售、实际截止前一小时内来源原行可进入严格市场；缺项留在原场次中。

用于新首版时，将通过核验的 `total_quote`、`market_snapshots`、`market_team_ids` 和 `market_evidence` 一并交给同场输入。综合首版的市场对照引用同一原盘线和同份同机构快照。记录实际选分截止。正式复盘从批次 `evidence/` 重读三份原件，并核对归档的球队身份、价格和两项市场各自的时钟。第一比分、三选、胜平负及盘口按原登记结算。

需要按赛前 24 小时、6 小时、60 分钟和 15 分钟取证时，传 `--checkpoint T-24h`、`T-6h`、`T-60m` 或 `T-15m`，分别使用新输出目录。每场从自己的计划开球时刻计算截止，截止前一小时才进入可用采集区间。采集在截止前结束，市场行按该截止复核。过早、逾时与报价缺项均在完整名单和回执中保留。同批开球分散时可按时段分别采集并共享原件根。

采集清单记录真实接收和核验时刻，以及对应节点截止。预测冻结仍走现有接口并登记真实签发时刻。该节点的报价供该节点或通过新截止复核的后续版本使用。盘口行的 `as_of` 来自提供商具名机构对象，独立于响应包时钟和下载时刻。上游接口说明未给出机构原生发布时间的定义。

自包含采集目录可在自己的节点截止前签发独立市场基准。入口使用原登记窗口，重核同机构原始报价，并把首次冻结和回执写入该采集目录的 `benchmark/`。恢复同次采集时会读回已保存的原记录。不同采集到达同一场同一节点时以原首次冻结为准。

```bash
PYTHONPATH=runtime python -B -m research_program.j1_checkpoint_issue \
  --registration PATH/ORIGINAL-REGISTRATION.json --window nov28_29 \
  --capture PATH/SELF-CONTAINED-MARKET-CAPTURE
```

用 `j1_window_intake --market-checkpoint` 接入对应赛果窗口，并按每个节点的原盘线独立复盘。正式综合首版继续使用当时的冻结证据。[赛果入口](j1-official-kickoff-proof.md)

北京时间 11 月 27 日的公开样本定位鹿岛鹿角对冈山绿雉，返回六家机构的同场 1X2 与全场大小球。返回对象为 `stale=true`，最新行距采集约 7 小时 27 分钟，当前严格盘口覆盖为 0/1。12 月 4 日和 12 日的查询尚未定位原登记的 J1 比赛。新入口在该真实陈旧样本上复核拒收，时钟与价格一致的接入由回归用例验证。
