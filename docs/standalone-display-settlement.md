# 综合推荐首次实发展示的结算接口

正式批次的原作者决定第一比分和两个不同备选。此接口保存作者签核的顺序，用渠道原件核验实际展示，再用主线已准入的常规时间赛果分别结算 Top1 和展示 Top3。批次的正式名单和观察名单由原有流程确定。

## 使用顺序

1. 将原作者的正式行、同场身份和签发时批准的范围行交给 `research.display_delivery.save_authored_row`。保存范围行的独立来源。函数复用主线的追加冻结和幂等键，保留旧打包器读取的 `displayed_scores` 列表，并返回 `fixture`、`payload`、`receipt` 和 `display_block`。弃权需要显式 `display_abstain=True`、空列表和空首选。只有真正展示的弃权才占首次展示名额。
2. 原负责人确定整段最终正文和顺序后，在开球前调用 `research.chat_display_readback.stage_message`，传入该正文中的原冻结、授权房间 ID、作者 ID 和本批独立的本地存证路径。函数使用当前时钟和创建时限，锁定完整正文的 UTF-8 SHA-256、有序比赛和冻结版本。重试返回同一份既有存证。开球后丢失的存证无法重新创建。
3. 在隔离复盘进程中调用 `research.display_review.review_batch`，传入完整的原正式名单、留存冻结版本、结果归档目录和带时区的核验时间。传入该次存证、授权房间与作者、服务消息号及下列四个可信的独立读取器。最后调用 `summarize` 获得分母。

| 读取器 | 必须能证实的内容 |
| --- | --- |
| `read_message(message_id)` | 指定授权房间的完整可见原文、作者、消息号、服务发送时间和删除状态。正文中的每个比赛区块必须逐行、按原顺序匹配。 |
| `lookup_original(slot)` | 该房间、作者和比赛槽的首个实际可见事件，含 `DELIVERED`、稳定消息号、服务时间与整段原文哈希。渠道当前显示的文字无法单独证明首次送达。 |
| `lookup_actual_kickoff(fixture)` | 独立核实的实际开球时间，带 `match_id`、`competition`、`home`、`away`、`actual_kickoff_at`。服务发送必须严格早于计划和实际开球。 |
| `lookup_approved_scope(fixture)` | 签发时独立留存、与本冻结一致的批准记录，含原 `competition`、`cohort` 和 `stages`。按主线范围与批准的原阶段逐字匹配。无法核验的赛事映射保留待定。 |

结果来自已有 `research_program.three_arm_review._results` 读取的 `normalized-results.jsonl`。它复核留存原始证据、身份、哈希、90 分钟完场和相冲突的已认证记录。原始响应孤件无法进入此口径。所用主线入口认证留存的 ESPN 常规完场；其他来源需要经其原有准入链独立验证。比赛范围、已采集范围、已认证赛果和已证实用户展示各有独立分母。

原名单中未正式展示的签核稿、未送达、缺件和旧版仅有列表的冻结保留在覆盖统计中。隐藏稿和修订以实际首条服务事件决定归属。证据冲突会中止核验；缺少原文、首槽或实际开球证据时没有命中值。归档中有多版时依整段原文登记的冻结版本核验，各场在复盘中只占一个位置。

## 隔离验证与回滚

```sh
PYTHONPATH=runtime python -m unittest -v runtime.research.test_display_receipt_settlement
python scripts/check_repository.py
python scripts/run_tests.py --layer integration
```

最后一项使用本项目带真实模型工件的环境。专项测试通过本地作者入口、只读模拟渠道及主线认证赛果入口，覆盖主选命中、仅备选命中和全失，以及缺件、阶段冲突、晚发、重试、原件与修订错配。

在主线 `899ce01542d90fafb7f734d08cf13c37d553505a` 上独立抽取了草稿 #71 的作者行、#51 的渠道绑定、#74 的结算接线。#51 的采集和阶段变更仍需另案验收。本改动仅添加可选模块、专项测试和说明。回滚操作为撤销本次单一工程提交。历史冻结和既有预测入口均可在原版本照常读取。
