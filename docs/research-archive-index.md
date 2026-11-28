# 已结束研究归档索引

审计日期为 2026 年 11 月 25 日。下表记录已经作出停止决定的配方。原报告中的赛果口径、样本范围与不确定性随固定提交保存。

| 原 PR | 研究 | 原报告及精确源码提交 | 结论 |
| --- | --- | --- | --- |
| [#63](https://github.com/kaka597000/Football-Analysis/pull/63) | 联赛总球校正 | [`06453e75c081b592d0f5ec69303fecc97c4d409d`](https://github.com/kaka597000/Football-Analysis/blob/06453e75c081b592d0f5ec69303fecc97c4d409d/docs/research/frozen-current-total-20261124.md) | 后段 61 场首选 8→9，矩阵前三净少 1 场，比分对数损失与高总球概率质量退步。该配方止于开发。 |
| [#64](https://github.com/kaka597000/Football-Analysis/pull/64) | 近期危险区域传球 | [`8de6fe91629cca642a3c90513aee7bf53134f6b5`](https://github.com/kaka597000/Football-Analysis/blob/8de6fe91629cca642a3c90513aee7bf53134f6b5/research/20261124-prior-deep-completions/README.md) | 有赛前输入证据的 25 场首选无净增，矩阵前三少 1，比分对数损失退步。淘汰该系数。 |
| [#65](https://github.com/kaka597000/Football-Analysis/pull/65) | 近六场非点球 xG 替换 | [`02153f64c6b86757d4a8201c4bd8b0f4ddaa6794`](https://github.com/kaka597000/Football-Analysis/blob/02153f64c6b86757d4a8201c4bd8b0f4ddaa6794/research/20261124-npxg-freshness/README.md) | 49 场历史回放的首选、矩阵前三均无净增。较严格的 25 场中，双方六场记录同源齐全为 0。停止该替换。 |
| [#67](https://github.com/kaka597000/Football-Analysis/pull/67) | 跨赛事休息天数 | [`1e9ecf68fbcfcb67723b06cc8430458f3e68cc44`](https://github.com/kaka597000/Football-Analysis/blob/1e9ecf68fbcfcb67723b06cc8430458f3e68cc44/research/20261124-cross-competition-rest/README.md) | 99 场锁定两臂、98 场核准常规时间，41 场校正休息。首选、矩阵前三与胜平负净增均为零。停止该配方。 |
| [#68](https://github.com/kaka597000/Football-Analysis/pull/68) | 单机构胜平负去水 | [`e096d2cf099274ab34186c736dde36b263b6e135`](https://github.com/kaka597000/Football-Analysis/blob/e096d2cf099274ab34186c736dde36b263b6e135/research/20261124-single-book-score/README.md) | 55 场同源同刻，首选均为 4/55，矩阵前三均为 17/55。停止该候选。 |
| [#69](https://github.com/kaka597000/Football-Analysis/pull/69) | 历史红牌失衡过滤 | [`54ec6d70fdff7145c1889ffe6071b2188663bef2`](https://github.com/kaka597000/Football-Analysis/blob/54ec6d70fdff7145c1889ffe6071b2188663bef2/research/20261124-prior-dismissals/README.md) | 后段 1,135 场首选净增 8，矩阵前三净少 5，比分对数损失退步。按登记门槛淘汰。 |
| [#70](https://github.com/kaka597000/Football-Analysis/pull/70) | 近六场半场表现 | [`6e3906fb24e487eec736e73ff304975d0ac35e93`](https://github.com/kaka597000/Football-Analysis/blob/6e3906fb24e487eec736e73ff304975d0ac35e93/research/20261124-halftime-balance/README.md) | 后段 1,764 场首选净增 6，95% 净差区间包含零。31 场原时钟回放的首选逐场一致。按登记门槛淘汰。 |
| [#72](https://github.com/kaka597000/Football-Analysis/pull/72) | 同机构同线变价 | [`156fc1f29890c03cc07e5a21471442d9760d407f`](https://github.com/kaka597000/Football-Analysis/blob/156fc1f29890c03cc07e5a21471442d9760d407f/research/20261124-same-line-motion/README.md) | DraftKings 同线 95 场首选 10→11，实盘大小球全赢 49→47，比分对数损失退步。结束该配方。 |
| [#73](https://github.com/kaka597000/Football-Analysis/pull/73) | 近期射正短缺 | [`1788f1b14f5d8ed7e336e65bc3177cf4f3290415`](https://github.com/kaka597000/Football-Analysis/blob/1788f1b14f5d8ed7e336e65bc3177cf4f3290415/research/20261124-prior-shot-drought/README.md) | 历史后段 688 场首选 85→84，实盘大小球全赢 399→397。停止该配方。 |
| [#75](https://github.com/kaka597000/Football-Analysis/pull/75) | 固定净胜差的比分重排 | [`8f69f2137b6ca8f69843295b45a48edf618b2faf`](https://github.com/kaka597000/Football-Analysis/blob/8f69f2137b6ca8f69843295b45a48edf618b2faf/research/20261124-fixed-goal-margin/README.md) | 时间外 612 场首选 84→82，矩阵前三 201→196。停止该配方。 |
| [#77](https://github.com/kaka597000/Football-Analysis/pull/77) | 欧国联分层融合 | [`642aa2f83b75a43c4098fce7232eb9d75daa5965`](https://github.com/kaka597000/Football-Analysis/blob/642aa2f83b75a43c4098fce7232eb9d75daa5965/research/20261124-nations-tier-fusion/README.md) | 后段 26 场首选均为 2/26，矩阵前三 7→8。按首选门槛关闭该配方。 |
| [#78](https://github.com/kaka597000/Football-Analysis/pull/78) | 逐次非点球射门 | [`ce49b42f49351f7cd0c65faef7a9cdbd7d5e9d7c`](https://github.com/kaka597000/Football-Analysis/blob/ce49b42f49351f7cd0c65faef7a9cdbd7d5e9d7c/research/20261124-chance-quality/README.md) | 可启用的五场历史比赛首选少 1，矩阵前三补一丢一。独立后段合格分母为零。停止该配方。 |
| [#27](https://github.com/kaka597000/Football-Analysis/pull/27) | 比分诊断、按日滚动与 xG 同场影子实验 | `9828f06c1c7100c2533a983a20b6953b8c5c234e` | 45 场有效首版 Current/Test 均为 2/45，滚动 3 场净增 0。保留固定提交归档。 |
| [#42](https://github.com/kaka597000/Football-Analysis/pull/42) | 五项受限阶段赛事可选适配器草稿 | `dd35b9ce1fb50d680a846631ee0587ceeacd1ffc` | 仅独立草稿验证，未晋级活动注册表。保留原分支与提交归档。 |
| [#50](https://github.com/kaka597000/Football-Analysis/pull/50) | 旧版模型治理草稿与五联赛固定权重混合 | `783e02ddebd427383dbdd6a9e4b359dc68c13d8b` | 五联赛混合均退回或劣于基线（德甲 8/63 vs 9/63，意甲 7/80 vs 8/80），分支已冲突关闭。 |
| [#53](https://github.com/kaka597000/Football-Analysis/pull/53) | 近六场比分形态与历史 xG 对照 | `e7fd00ff14136014cab65422c7dd10e6dab6f9b5` | 相关实现已纳入 `score_form.py` 后续研究，独立旧 PR 归档。 |
| [#66](https://github.com/kaka597000/Football-Analysis/pull/66) | 官方首发与缺阵在 T−60 / T−15 可用性审计 | `be1568aefe03e647a158533c19c3670ae438a675` | 研究审阅草稿已完成审计结论，按原说明关闭草稿并保留提交。 |
| [#71](https://github.com/kaka597000/Football-Analysis/pull/71) | 正常综合表赛前冻结交接草稿 | `42a59caf85de9f2f8890156313678218337186b3` | 四个改动文件已完整包含于 `#74` 首提交，原冲突草稿归档。 |
| [#79](https://github.com/kaka597000/Football-Analysis/pull/79) | 第三比分低分影子对照预登记草稿 | `86663b69968bad935d044749e962cfc706ff615b` | 已被后续覆盖率与结构核研究替代，保留封存提交归档。 |
| [#85](https://github.com/kaka597000/Football-Analysis/pull/85) | R11 亚盘让球在精确比分分布中的增量 | `6372ba1ea0de7538c9d14b4df60f745ded63453f` | 2,061 场留出首选均为 269，57 场扩展首选持平、三选少 1，不晋级活动模型。 |
| [#86](https://github.com/kaka597000/Football-Analysis/pull/86) | R12 精选三场全中可靠性排序 | `e1fcf8e742ad1e36e21c3a6f154ebe247a9def4f` | 已被后续全赛事 R16 排序校准扩展替代，叶节点归档。 |
| [#91](https://github.com/kaka597000/Football-Analysis/pull/91) | R17 逐赛事赛程密度对比分分布增量 | `52afde502578686e79ff636ffbdab9d7b50d9aa4` | 主窗口首选改对 18、改错 21，95% 区间包含零，未获全局增益。 |
| [#95](https://github.com/kaka597000/Football-Analysis/pull/95) | R20 日职半场领先/落后状态估计赛前比分分布 | `5adcca645b0dea1f6765d32f105fb95255b71b1a` | 2024 验证段选中零调整强度，止于历史研究。 |

本批逐条核对两仓 main、打开的 PR、现行调度固定源码及已登记的前瞻入口。上列分支均指向表中提交，未发现作为活动分支、CI 输入或新运行依赖的引用。归档时保留原分支和所有历史固定提交。

部分已结束研究仍用固定 Git 对象复现彼此的历史比较。#65 引用 #64，#68 引用 #67，#70 引用 #69，#73 引用 #69 与 #70，#75 引用 #73，#77 引用 #72。复现时沿用原报告的精确提交与证据权限。
