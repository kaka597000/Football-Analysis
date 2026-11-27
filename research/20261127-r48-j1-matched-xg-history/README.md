# R48 日职联同样本 xG 与进球回放

从留存的 FotMob 比赛详情恢复 51 场双边 xG、射门和射正，对齐日职联常规赛 90 分钟赛果。44 场能以原响应哈希绑定采集时点。冈山对神户两版 xG 进入敏感性检查。[运行前登记](protocol.json)

150 场历史回放中，R7 首选及三选命中 13 场和 35 场。按较早比赛日使用所留存的同组数据，进球轨为 14 场和 38 场，xG 轨为 15 场和 38 场。使用目标比赛日开始前已有哈希绑定回执的资料时，进球轨为 14 场和 36 场，xG 轨为 13 场和 35 场。真实前向使用周末已冻结的 R28 和 R46 对照。[周末固定总球的 xG 对照](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/116)

```bash
PYTHONPATH=runtime python -B -m research_program.j1_matched_xg_history \
  --data /path/to/Football-Analysis-Data --out /tmp/j1-r48-replay
```
