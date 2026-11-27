# 日职联赛前盘口证据

日职联常规赛可沿用 `scripts/capture_public_club_markets.py` 获取公开的 InferSports 盘口。赛历对象的联盟为 `lg_VWS270YX`、`Jap1`。采集以 ESPN 的原登记比赛、已核实的两源球队 ID 和准确开球时刻匹配唯一的提供商赛事。球队对照的字段为 `espn_name`、`provider_name`、`provider_id`，按联赛与 ESPN 球队 ID 索引。

```bash
python scripts/capture_public_club_markets.py \
  --fixtures CURRENT-FIXTURE-INPUTS.json --team-ids VERIFIED-TEAM-IDS.json \
  --out PATH-TO-BATCH/evidence
```

`prepared-fixtures.json` 保留全部输入场次。通过检查的报价携带原始赛历及赔率的 URL、文件哈希和实际接收时点。大小球采用实际盘线及大小两侧价格。同机构 1X2 按原响应核对三项价格。两类市场各自保留提供商行级 `as_of`。仅 `stale=false`、全场、开售、实际截止前一小时内来源原行可进入严格市场；缺项留在原场次中。

用于新首版时，将通过核验的 `total_quote`、`market_snapshots`、`market_team_ids` 和 `market_evidence` 一并交给同场输入。综合首版的市场对照引用同一原盘线和同份同机构快照。记录实际选分截止。正式复盘从批次 `evidence/` 重读三份原件，并核对归档的球队身份、价格和两项市场各自的时钟。第一比分、三选、胜平负及盘口按原登记结算。

北京时间 11 月 27 日的公开样本定位鹿岛鹿角对冈山绿雉，返回六家机构的同场 1X2 与全场大小球。返回对象为 `stale=true`，最新行距采集约 7 小时 27 分钟，当前严格盘口覆盖为 0/1。12 月 4 日和 12 日的查询尚未定位原登记的 J1 比赛。新入口在该真实陈旧样本上复核拒收，时钟与价格一致的接入由回归用例验证。
