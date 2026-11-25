# R8 首选和三选比分研究

运行环境使用仓库依赖和 LightGBM 4.6.0。赛前分布按常规时间胜平负和实际亚洲大小球结算类别分组。在每组内，用验证期学到的比分偏差调整概率，再还原该组的原概率。没有大小球线的场次只按胜平负分组。R7 两侧的胜平负和大小球方向应逐场相同，运行器逐一校验。实验仅产生独立候选。

## 复算历史比较

把数据仓库相应版本的目录分别设为 `R7_EVIDENCE`（`9490479` 的 `workspace-updates/20261123-prematch-literature`）和 `R5_EVIDENCE`（`a2c680d` 的 `workspace-updates/20261123-all-scope-kpi`）。入口先核验档案和训练面板的哈希，再从 R7 固定工件重建每场分布。测试必须逐场重现原 R7 选分，校准只用原 R7 测试前的中间窗口。

```bash
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
PYTHONPATH=runtime python -m research_program.r8_score_study \
  --scope configs/analysis-scope.json --evidence "$R7_EVIDENCE" \
  --prior "$R5_EVIDENCE" --out "$NEW_OUTPUT"
```

输出包括各赛区 36 项比分偏移权重、测试期逐场选择、市场覆盖和同场差异。早期复验另列。前向运行使用 `research_program.r8_forward`，从原始冻结的赛程和报价重建 R7 首次预测，仅在开球前追加 R8 的一个首选与两个有序备选。

## 临场变化

`r8_market_movement` 使用 T−1h 面板、其 R7 工件和带原来源及机构的 JSONL 轨迹。每条轨迹需有 `id`、`competition`、`date`、`home`、`away`、`source`、`bookmaker`、`market="1x2"`、`seconds_to_kickoff` 和 `odds={"home": …, "draw": …, "away": …}`。盘口变动只在同一数据源、机构及市场内比较。开球前两时点取各自截止时刻之前的最新报价，同时报价相互冲突时排除该端点。

原加密档案需要本地注入原试用凭据。轮到公开档案复验时可使用下列入口。它按各固定窗口抽取预定的比赛排名，复用已保存的原响应，可从新输出目录复跑。报价时间只有来源展示时钟，历史收到时间未知。公开档案没有可结算的双边大小球报价，因此实盘大小球保留缺项。

```bash
PYTHONPATH=runtime:runtime/database python -m research_program.r8_phoenix_public \
  --cache "$PHOENIX_PUBLIC_CACHE" --out "$NEW_OUTPUT"
```

两组使用相同的 T−1h 先验、同期价格、训练比赛和混合权重。两时点无效时，实验组使用对照分布。报告保留完整样本、双时点交集、固定半权重参照和按比赛日聚类的同场差区间。所有步骤均独立于活动路由。
