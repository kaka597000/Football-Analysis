# R35 欧联与欧协联相近比分对照

根据两套已归档的进失球及射正模型，检验能否在 R13 概率接近的比分中调整唯一首选或第三选。首选候选限原三选且距首选概率不超过 1.5 个百分点。第三选补入范围为原矩阵第 4 至第 6 位且距第三选不超过 1.5 个百分点。实际赔率及方向来自原同场记录。[运行前登记](protocol.json)

两种选分规则在各自验证窗均净少 1 场第一比分。两项赛事按登记选回 R13。2026/27 历史日期留出的欧联 72 场中，真实比分有 37 场排第七或之后。在本轮限制下，即使用赛果事后挑选，首选和三选也最多命中 11/72 与 28/72。两项历史上限均低于第一阶段阈值。此结果把欧联的后续重点放在生成有足够覆盖的比分候选。[第一阶段协议与实际赛程](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/77)

```bash
PYTHONHASHSEED=0 OPENBLAS_CORETYPE=Haswell OPENBLAS_NUM_THREADS=1 \
  NPY_DISABLE_CPU_FEATURES=AVX512F OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  PYTHONPATH=runtime python -B -m research_program.europe_close_score_agreement \
  --data /path/to/kaka597000-Football-Analysis-Data --out /path/to/new/replay
```

每窗输出原始排序、有效证据、双方比分意见、同场对比和可检验的选择条件。实际赛事复盘使用原前向首版的 90 分钟结果。
