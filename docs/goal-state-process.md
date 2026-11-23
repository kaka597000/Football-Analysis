# 逐球状态模型

研究轨 `WCPP-GOAL-STATE-1` 根据进球发生前的比分状态和比赛时间估计下一球强度。每队进球都会改变双方下一球的分布。输入的主客基础进球率来自同场同机构的90分钟胜平负与实际总球盘。另一个前向对照使用已冻结的球队进球率。

训练按球队当时落后、打平、领先，以及三个30分钟时段统计实际进球与预期进球暴露。后者用此前比赛日估计的球队攻防强度乘以上述状态持续的时间。每格以30个预期进球向原强度收缩。上下半场的补时球归入相应半场的结尾，保留事件顺序。完整的0比0进入暴露时间。比赛结束时的联合分布由连续时间马尔可夫过程计算。

可从数据仓库的 `workspace-updates/20261123-goal-state` 恢复输入。以下命令在源码根目录运行，`PYTHONPATH=runtime`。输出目录应为新目录，选择结果、逐场预测与验证报告逐步写入。

```bash
export PYTHONPATH=runtime
python -m research_program.goal_state_study fit --inputs events/locked-training.jsonl --out study
python -m research_program.goal_state_study selection --inputs modern-market-paths.jsonl --out study
python -m research_program.goal_state_study known --inputs modern-market-paths.jsonl --out study
python -m research_program.goal_state_study dutch --inputs new-panels/normalized.jsonl --out study
python -m research_program.goal_state_study english_second --inputs new-panels/normalized.jsonl --out study
python -m research_program.goal_state_study portuguese --inputs new-panels/normalized.jsonl --out study
```

单一候选由2024/25五大联赛的首选命中数决定，同数依次比较三选、胜平负、全赢大小球及联合比分对数损失。另存一套四项目标配方，分别固定首选来源、两项备选的来源、胜平负与大小球来源。选出备选时先放入固定的首选比分，去重后补足三项。荷甲外测与英冠、葡超外部对照各自报告。历史收盘快照缺少机构报价时钟，前向实盘采用单条报价时钟。标准窗口为1小时，另行登记的24小时早盘研究窗口保存各市场实际报价年龄。

`goal_state_process.predict` 接受工件、赛前赛事及已锁定的配方；其结果含同机构参考、候选和报价证据。保存首次研究预测使用 `goal_state_forward.register` 与 `issue`。赛后用 `python -m research_program.goal_state_forward --issued DIR --results RESULTS.json --evidence-root RAW --out NEW.json` 核验首次记录和90分钟赛果，再按固定名单结算。待赛可传入空数组，缺少报价的比赛保留在覆盖分母。
