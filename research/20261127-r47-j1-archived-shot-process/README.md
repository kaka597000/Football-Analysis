# R47 日职联射正历史回放

从留存的 5Dollar 原件恢复 136 场常规赛双边射正。R47 逐场复原原 R7 分布，使用 R10 的球队射正量、转化率和联合波动，按整天比赛完成后更新。把修正投影到原总进球分布的对照单独计算。[预登记](protocol.json) · [来源及逐场对照](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/66c85919bf8cf32408b4b2cb1b5b3fb9f1d20f18/workspace-updates/20261127-r46-j1-process-at-fixed-total/history)

86 场验证中，原 R7 与射正半权重方案均为首选 11 场、三选 24 场。按登记的同分顺序选中 R7。后续 64 场中五条轨均为首选 2 场、三选 11 场。R47 研究结束于历史回放。11 月 28 日至 29 日的真实过程数据对照沿用 R46 的十场赛前冻结。[周末独立冻结](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/116)

在源码仓根目录运行以下命令，使用数据仓的 R5 面板、R7 已签模型及配对射正文件复算。

```bash
PYTHONPATH=runtime python -B -m research_program.j1_archived_shot_process \
  --data /path/to/Football-Analysis-Data --replay /tmp/r47-j1-replay
```
