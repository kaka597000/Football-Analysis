# 第三比分影子验证

以正式作者交接的三个有序比分为对照。保持第一、第二比分。在作者引用的原始 Test 矩阵中按概率排名，前三若含原顺序未覆盖的 1–0 或 0–1，用排名最靠前的一项替换第三比分。没有合格候选的场次两组相同。并列时复用项目的稳定矩阵排序，主队进球是行，客队进球是列。

按原计划开球时间纳入北京时间 11 月 25 日 12:00 至 12 月 9 日 12:00 之前的正常正式批次。12 月 16 日 12:00 截止结算。批次需保留正式入选全名单，同场只入组一次。排除 11 月 24 日晚场及 R7 原 192 场。R7 源归档同时按稳定赛事 ID 和同一赛事、对阵、开球的组合校验，跨供应商身份需沿用正常流程已核证的规范身份。未签核、原作者弃权、缺双模型、晚到和冲突均计入相应覆盖。不存在完整比分的行不产生配对。

## 在正常作者交接完成后锁定

从现有 Data 仓库的本地对象准备原 R7 归档。研究状态使用源码目录外的本地路径。

```bash
export PYTHONPATH="$FOOTBALL_SOURCE/runtime"
git -C "$FOOTBALL_DATA" show a6d45024cf6cf622f05e4b378ab53cfe2d6ae329:workspace-updates/20261123-prematch-literature/first-issues.tar.gz > "$R7_ARCHIVE"
python -m research_program.third_score_shadow --state "$SHADOW_STATE" register --r7-archive "$R7_ARCHIVE"
```

`register` 使用当前时钟、锁定协议、实现指纹和 R7 原始名单。恢复时沿用已有状态目录。下一次正常发布前，使用作者已定稿的正式全名单和原作者交接目录。

```bash
python -m research_program.third_score_shadow --state "$SHADOW_STATE" lock \
  --roster "$FORMAL_ROSTER" --authored-dir "$NORMAL_BATCH/authored"
```

- `FORMAL_ROSTER` 是 JSON 对象，包含生产方稳定的 `batch_id` 和 `fixtures` 数组。每个元素取原正式名单中保留的 `match_id`、`competition`、`cohort`、`stage`、`home`、`away` 和带时区的 `kickoff_at`。常规名单以其真实纳入范围为准，保留缺交接文件的记录。如果最终交接使用一个后续冻结版本，从正常定稿清单保留该场确切的 `author_freeze_sha256`。
- `authored/<原事件ID>.json` 是正常 `research.display_delivery.save_authored_row` 原返回值，含 `fixture`、`payload` 和指向真实冻结的 `receipt`。它引用定稿当时已存在的原始双模型冻结。作者目录、双模型快照和正式名单均只读。
- 对定稿的整个正常子批次调用一次。批次最早仍准入的计划开球是提交时限。重试读取同一份封存记录。覆盖记录可含零个有效候选；正式名单尚未提供时没有可锁定的批次。

## 复盘

```bash
python -m research_program.third_score_shadow --state "$SHADOW_STATE" review \
  --results-state "$RETAINED_RESULT_STATE" --kickoffs "$VERIFIED_ACTUAL_KICKOFFS"
```

`RETAINED_RESULT_STATE` 是已有的 `normalized-results.jsonl` 与留存原始证据目录。`VERIFIED_ACTUAL_KICKOFFS` 是正常复盘独立核证的开球映射，以 `match_id` 为键，每场含 `match_id`、`competition`、`home`、`away`、`actual_kickoff_at`，与正式展示复盘入口使用同一开球合同。缺少确切开球、晚于真实开球才锁定，或 90 分钟赛果无有效证据时保留待定。缺分母返回空值。

结算报告适用场次及占已配对、全名单的比例，两组同场 Top3 命中与分母、配对补中、丢失及净增。正式展示仍经项目原渠道证据结算。影子组只提供第三比分变更的研究结果，两组第一比分相同。
