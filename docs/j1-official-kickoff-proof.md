# 日职联官方开球证据接入

日职联首版复盘可读取 J.League 官方赛中或赛后页的 `KO` 分钟。赛前先将原登记的 ESPN 比赛、主客队、官方比赛地址、球队 ID 和赛轮逐场绑定。运行时重读原赛前页面，核验同场身份、计划时间和哈希，再处理同一个官方地址下的赛中或完赛原件。

- ESPN 的上半场开球事件仍能提供秒级时点。官方分钟按左闭右开区间保存。两源同场相符时采用 ESPN 秒级时点；冲突时该场的开球证明待核。
- 官方报时与计划分钟重合时，需要相符的 ESPN 开球事件。官方页显示原计划时间或尚未证实实际开球时保留证据状态。
- 采用官方分钟时，已核实的首次送达必须早于整个分钟区间。落在该分钟内的首送时点待核，分钟结束或之后判定为迟到。完赛证据的观察时间须晚于该区间上界加 90 分钟或与其相同。
- 赛果需使用已有 90 分钟核验口径，官方终场比分冲突随该场保留。原聊天回读、首送回执、正式盘口机构报价时钟及各窗口覆盖要求继续按登记判定。

`research_program.display_window_review` 接收可选的 `--jleague-kickoffs`。文件包含 `binding_record` 和按 ESPN 比赛 ID 分组的 `captures`。绑定的预赛原件和随后的官方赛中、完场原件均相对 `--kickoff-evidence-root` 解析。每条官方采集至少含 `raw_file`、`url`、`code`、`captured_at_utc`、`http_status` 和解压后的原响应 `sha256`。比赛仍在预赛阶段时可用空 `captures` 重放原绑定。通过正式核验的间隔输出在 `actual_kickoff_interval`。

相关测试可在源码根目录用以下命令运行。

```bash
PYTHONPATH=runtime python -B -m unittest \
  research.test_display_receipt_settlement \
  research_program.test_display_window_review \
  research_program.test_nov25_first_review
```

## 从原件接收赛果

把已登记窗口的 ESPN 单场摘要和 J.League 同一地址的采集放在一个新目录。ESPN 采集清单用 `espn.jsonl`，沿用 `nov25_first_review.inspect` 的 GET、请求与接收时刻、原件路径、字节数和哈希字段。官方采集清单用 `official-captures.json`，以 ESPN 比赛 ID 分组，引用采集目录内的原件。源数据可以逐场追加。每次复盘选择自己的 `--at` 并写到新输出目录。

```bash
PYTHONPATH=runtime python -B -m research_program.j1_window_intake \
  --registration PATH/ORIGINAL-REGISTRATION.json --window nov28_29 \
  --binding PATH/kickoff-packet.json --binding-root PATH/ORIGINAL-CARDS \
  --intake PATH/LIVE-CAPTURES --out PATH/NEW-RESULT-STATE \
  --issued PATH/EXISTING-RESEARCH-ISSUE
```

输出目录包含主线复盘用的 `normalized-results.jsonl`、赛果原件、`actual-kickoffs.json` 和 `jleague-kickoffs.json`。后两份传给 `display_window_review` 同名选项，以输出目录作为结果及开球原件根。综合首版仍须传入原发消息及独立首送回执。尚未核实实际开球与完场的赛事保留在窗口内。研究轨以原预测冻结严格早于实际开球时刻或区间下界为准，可重复传入 `--issued` 核对已有研究。Current 和 Test 的原冻结可用同一结果目录复盘。
