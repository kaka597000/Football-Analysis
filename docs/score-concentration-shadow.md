# 比分集中诊断与未来冻结评价（独立影子工具）

当前/test、综合判断、生产路由和活动工件保持不变。工具不训练、不重排比分、不强制分散1–1、不机械加球、不自动晋级。Primary Top1仍是最高指标；校准和概率质量是约束，不能替代Primary。

## 1. 描述性诊断

`runtime/research/score_concentration.py`读取标准化JSON数组。每行含`match_id`、`track`、`cohort`、原`prediction`和可选`result`。prediction沿用`matrix`、`matrix_hash`、`primary`、`version`等字段；result含整数`home_goals`与`away_goals`。同场同轨不能重复，修订必须另跑。

```sh
python runtime/research/score_concentration.py rows.json --out new-diagnosis.json
```

输出分轨/版本/队列的完整覆盖分母、选分次数、原联合众数与次选差距、1–1最优替代候选、零球与三球以上路径、单队/总球均值误差、中央80%区间覆盖。校准同时检查1–1、平局、双方0球/1球、总球≥3/4及已选Primary；固定10个概率区间，报告Brier、ECE及描述性Wilson区间。精确比分和WDL的Brier、RPS、截断log-loss另列。

完整矩阵不得重归一化；非法矩阵单列并留在人口分母。矩阵支持外赛果记概率0，log-loss截断值为1e-15并单列次数。ABSTAIN/NOT_EXECUTED/FAILED的残留比分不计推荐和命中，独立WDL可以继续结算。待赛不算失败，待定/缺失不算已发比分，却留在总覆盖分母。

这些结果是诊断，不证明历史未见、冻结真伪或选择器有效。1–1成为多数比赛众数，不意味着P(1–1)占多数概率。候选领先差距是比较材料，不是已训练的改分阈值。

## 2. 已恢复归档适配器

```sh
python runtime/research/score_concentration_archive.py frozen-records.json results.json \
  --batch night --out preserved-batch-diagnosis.json
python runtime/research/score_concentration_archive.py frozen-records.json results.json \
  --batch night --earliest-pairs earliest-pairs.json --out first-version-diagnosis.json
```

适配器核验每份冻结的语义SHA256、比赛/轨道/赛前时间、批次和赛果身份标志。按match_id连接，不按数组顺序；不同内部ID映射同一FotMob比赛也拒绝，防重复分母。输出输入文件哈希、首次/修订数量和分版本跨队列描述性汇总。必须另核对归档manifest及原赛果响应字节；已有标志不等于重新查询独立来源。最早版本仅指本次恢复档案所含记录，不宣称穷尽全部历史存储。

若最早记录未执行且后来修正开球时间，保留该未执行人口与冲突，不补后版矩阵；已给比分的时间冲突直接阻断。诊断输出和私有归档只保存到私有数据目录，不提交公开源码仓库。

## 3. 新的未来评价协议

入口：`runtime/research/frozen_score_evaluation.py`。这是新影子评价契约，不会把现有历史记录追认为新协议合格。

```sh
python runtime/research/frozen_score_evaluation.py protocol
```

预登记spec必须明确：

- experiment_id、protocol_sha256、implementation_sha256（protocol命令同时返回，锁定实际评价代码）。
- matches：完整预定比赛清单，每项match_id/cohort/kickoff_at；不能事后删难场。
- development_match_ids：已用于开发的比赛，不能进入holdout。
- tracks：轨道name、真实freeze_track、payload_key（null/current_model/test_model）。
- 每轨identity_by_cohort：version、artifact_sha256、training_snapshot_sha256、带时区training_cutoff。综合或手写选择器也要保存方法工件与数据快照身份，不虚构已训练模型。
- baseline_track与evaluation_not_before：预先锁定对照和正式查看时间。末场至少3小时后；实际未完赛继续pending，不强行结算。

```sh
python runtime/research/frozen_score_evaluation.py register spec.json --root PRIVATE_PLAN_ROOT
python runtime/research/frozen_score_evaluation.py evaluate plan-record.json issued-records.json results.json --out new-review.json
```

register用真实当前时间和现有原子、追加冻结写入器；首场开始后拒绝登记。只接受计划第1版。spec调整必须新experiment_id和新的未开赛完整队列，不能以改计划方式更新原成绩。同experiment_id修改内容会产生冲突，不生成替代首版。

每条新影子预测应保存模型身份字段、input_snapshot_sha256、data_cutoff_at、generated_at，以及原primary/矩阵/独立方向。模型及训练快照须与计划吻合；训练截止≤数据截止≤生成≤冻结<开球，生成不得早于计划。缺身份/来源时保留未合格人口，不以另一版替代。若保存input，必须与冻结fixture相同。现有production预测没有这些新增影子字段时，需要在独立影子生成环节收集真实来源并记录，不补造哈希、不修改旧冻结。

评价仅使用各场各轨revision=1且FIRST_PREMATCH、previous_sha256=null的记录。缺失、弃权、生成失败仍在预定完整分母。首版弃权不会从v2找一个有比分的替代。相邻修订哈希链一并校验，修订/滚球另列。

results要求唯一match_id、FT/completed、regulation_90、整数比分、HTTPS来源和开球90分钟以后且不在未来的观察时间。报告已结算、待赛、全分母命中率、有效推荐命中率、覆盖率；这三者不能互换。

## 4. 大小球及统计边界

WDL以明确发布方向单独结算，不要求Primary已发布。大小球必须有原total.quote、reference_direction，以及verified、bookmaker、match_id、source_url、source_file_sha256、quoted_at、captured_at或query_time。market只接受standard_total/asian_total，score_basis必须regulation_90；不混首半场、单队或含加时市场。报价≤采集≤预测数据截止≤生成，采集还须不晚于fixture截止。缺真实线或来源只记缺失，不默认2.5；全赢/半赢/走盘/半输/全输独立计数。

候选对照按同场配对，同时给完整已结算人口和双方均有Primary的子集。报告改对/改错/净增、北京时间开球日期聚类bootstrap 95%区间（固定种子、5000次）。单日期不产生区间，少于20日期提醒不稳定。这是描述性固定窗口不确定性，不是连续偷看可用的显著性检验，不能自动触发晋级。

计划/预测/训练快照的自报SHA只证明内容一致，不独立证明真实首次获取时间、外部可信时间戳、完整来源清单。保留真实原始字节、来源first-known信息和远端时间证据后才能审计。evaluate输出horizon_closed和population_complete，并始终automatic_promotion=false。开发用过的批次不能再称未见验证；训练刷新和xG变体须各自锁定方法、输入与未来holdout。

## 5. 本次重建的身份

本实现是在先前临时工作区已不可访问后按保留设计重建；使用新的源码提交与重新运行的测试结果，不沿用旧本地提交或旧测试通过证明。只提交工具、合成测试和使用说明；真实诊断输出仍在私有目录。未登记真实未来队列、未部署、未自动晋级。
