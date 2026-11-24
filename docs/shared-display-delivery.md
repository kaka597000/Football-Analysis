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

## 11 月 24 日逐场展示表接入

11 月 23 日晚批归档的 31 个冻结哈希可复核，包含 15 个综合首冻。14 场属于正式赛事阶段，另有 1 场 MLS 季后赛观察。14 场的 `user_facing_row` 与作者写下的首选及两备选次序一致。其中 13 行还保存了 `ordered_scores`，首场分开保存 `primary` 和 `alternates`。归档中的 `displayed_scores` 为列表，14 场均缺独立的可信首次可见回执。在原字节上运行严格结算入口，两项展示指标的有效分母均为 0。[赛前批次归档](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/e958e8e09e062d147e98714a466d69ff6d35ca56/workspace-updates/20261123-evening-slate)

对今后的新赛事，逐场表的作者可以将已核阶段的 `selection_scope='active_reference'`、`primary`、`alternates`、`displayed_scores` 和 `user_facing_row` 一并交给原有综合 `publication.publish`。入口先核同场 ID、表内顺序、备选互异及已提供的北京时间，再冻结规范文本和原表。若该场先冻结、后发送，交付方须原样展示 `delivery_request(record)['text']`，持久化当时的请求摘要，将其与渠道消息 ID 及服务端首发时钟绑定。归档文件和发出渠道仍各自提供证据。无发送方回查时，调用结果为 `UNCONFIRMED`。恢复或结算旧记录沿用原格式，保持原覆盖分母。

离线回归经过真正的 `publish` 和 `settle`，发送端采用可重启的测试实现。测试覆盖表内评分次序冲突、重复、缺已选三比分、错场、开球时刻不同、漏签范围、无回执和赛后首次尝试。正式发送方的原子首发与查回、实际开球墙钟以及一次真实未来批次的端到端验收仍待接入。下一批的接受条件是从原渠道查回的正文及时间均通过核验，并能由赛后原始常规时间证据结算同一赛事。

同批赛后用 Data 提交 `a6d45024cf6cf622f05e4b378ab53cfe2d6ae329` 的 `normalized-results.jsonl` 和原始摘要重放。正式范围 14 场中，5 场的同场常规时间赛果通过 `certify_retained`，另 9 场在该固定文件未找到赛果。把原综合冻结原样交给 `settle`，登记 14 场、缺展示回执 14 场、确认赛果 5 场、待赛果 9 场，首选与展示前三可评分均为 0 场，命中率为空。

用同一固定数据提交核对 ESPN 首半场开球事件。先验原始摘要哈希、赛事身份、主客、计划开球和常规时间结果，再查 `keyEvents` 中唯一的 `type.id=80`、`type.type=kickoff`、`period.number=1`、`clock.value=0` 事件，记录事件 ID 和带时区的 `wallclock`。同批 5 场已核赛果里有 3 场具备该字段。例如英超 `401879237` 的事件 `55596585` 记为北京时间 11 月 24 日 04:00:13，赛程列为 04:00。另在休息天数研究的 99 个冻结身份中，80 场有该字段、17 场无唯一开球事件、1 场开球事件缺时间、1 场的赛果身份与原冻结开球冲突。80 场的字段比计划时刻晚 6 至 1,014 秒，中位 104 秒。其中 45 场在同源文字直播引用了相同事件与时钟。此轮只核实了事件记录时间和字段覆盖，独立的现场时钟误差尚未核定。正式结算继续使用经核对的实际开球和独立渠道回执。[固定赛果索引](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/a6d45024cf6cf622f05e4b378ab53cfe2d6ae329/normalized-results.jsonl)
