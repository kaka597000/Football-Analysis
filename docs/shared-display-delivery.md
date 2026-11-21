# 综合比分发布与复盘

本草稿把实际展示的第一比分和两个不同备选接到现有 `publication.publish()`。它使用 `comprehensive` 轨的原冻结格式和已选定的 `primary`，调用方继续提供选分依据。当前正式范围取自 `configs/analysis-scope.json` 的20项配置。发布接入不依赖某项赛事已有活动模型路由。

## 接入

1. 用 `attach_display(payload, first, alternatives)` 生成有序三比分和文本。第一比分须等于综合 `primary`。待定使用 `abstain=True`，`primary` 为空。把此 payload 和已核对的 fixture 交给 `publication.publish(..., track='comprehensive', publisher=...)`，发布器先保存不可变冻结。
2. 交付方使用 `send_first(request)` 原样展示 `request['text']`。请求绑定首发槽、赛事ID、综合轨、版本、冻结ID和SHA，以及计划开球前的最晚送达边界。槽按赛事ID和综合赛前轨唯一。发送方应对同一槽原子去重，检查过期及已开球状态，并在重启后仍能查回该槽首次实际可见的消息。一个批次应统一使用已核对的赛事ID和同一权威发送方。
3. `lookup_first(slot)` 从该权威渠道返回首条实际可见记录。字段为 `status='DELIVERED'`、`message_id`、渠道的 `delivered_at`、原请求 `request_sha256` 和渠道记录中的准确 `text`。SHA采用 `analysis_freeze.digest(request)`。渠道适配器负责核验真实消息身份、交付状态、服务端时间和可见文本，且须覆盖首次展示历史。排队或发送响应均不能代替该查询。无法确认时返回空或报告查询失败，不能从本地文件、时钟或冻结推算确认时间。
4. 发布器按原请求校验回执，并把匹配证据独立保存在 `OUT.display-receipt.json`。缺确认时返回 `UNCONFIRMED`。同一 `OUT` 通过 `resume=True` 恢复，使用原冻结及同一权威渠道。已经确认的记录只查回原回执，即使原JSON或报告丢失也不重新发送或补生成首次展示文件。尚未确认的请求只能在冻结开球边界前尝试发送；实际展示时间始终取自渠道。

首次展示由渠道确认决定。此前隐藏的草稿可以形成冻结修订，但不占首次展示槽。若第一条实际展示是待定，仍保留为首版，计入登记和弃权覆盖。此入口用于首次及其重试，原分析流程可以独立保留后续修订。是否另设首份完整比分的评价留给后续策略决定。

## 结算

调用 `research.display_review.settle()`，传入与首次回执绑定的冻结、同场fixture、现有保留90分钟赛果证据和同一权威 `lookup_first`。`lookup_actual_kickoff(fixture)` 从核对过的同场开球证据返回实际开始时间。若只有计划开球或无法核验，返回空。只有发出时间严格早于实际开球且原始赛果通过认证，才计命中。覆盖分母保留缺失、待结算及弃权。Top1命中也计展示Top3，备选命中只计展示Top3。原模型矩阵和既有冻结比较继续各用其原入口。

## 草稿验证与上线条件

```sh
python -m unittest discover -s runtime/research -p test_display_review.py -v
PYTHONPATH=runtime python -m unittest discover -s runtime -p test_publication_recovery.py -v
python scripts/check_repository.py
python scripts/run_tests.py --layer integration
```

离线测试从真实发布函数经可重启的测试发送方走到结算。英超、MLS、欧联和世界杯小组赛为合成fixture，覆盖ESPN配对及football-data.org完赛证据格式。它们验证合同与恢复行为，真实20项比赛覆盖和预测成绩需要各自另有证据。

现有数值发布器交付本地文件，综合分析保存入口也只冻结内容。两者都没有可查回的用户可见回执。检查的两份ESPN归档提供赛程日期与开场描述，未提供可据此核定的实际开球墙钟。正式使用需要接入可信的首次可见确认和实际开球证据，先验证新比赛的一次受控交付、失联恢复和结算，再考虑正式覆盖。旧冻结和历史统计继续使用各自的版本与口径。
