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

本批逐条核对两仓 main、打开的 PR、现行调度固定源码及已登记的前瞻入口。上列分支均指向表中提交，未发现作为活动分支、CI 输入或新运行依赖的引用。归档时保留原分支和所有历史固定提交。

部分已结束研究仍用固定 Git 对象复现彼此的历史比较。#65 引用 #64，#68 引用 #67，#70 引用 #69，#73 引用 #69 与 #70，#75 引用 #73，#77 引用 #72。复现时沿用原报告的精确提交与证据权限。
