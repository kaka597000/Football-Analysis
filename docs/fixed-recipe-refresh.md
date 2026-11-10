# 固定配方的数据刷新研究

2026-11-10 新增英超/意甲/瑞典时间留出入口及澳超独立入口，保持本旧研究入口不变。181 条新增与 102 场留出结果、固定配方和限制见[四联赛刷新对照](four-cohort-refresh-20261110.md)。最终全量候选与评价工件分开，不自动晋级。

独立入口 `runtime/research/fixed_recipe_refresh.py` 读取活动registry所登记且哈希一致的四个原工件，仅对 `ita.1` / `swe.1` 生成非活动副本，不写registry或旧工件。

```bash
python runtime/research/fixed_recipe_refresh.py \
  --rows /absolute/history/rows.json \
  --coverage /absolute/history/coverage.json \
  --raw-root /absolute/history/raw \
  --cutoff "$ACTUAL_PAST_CUTOFF" --end 2026-10-31 \
  --out /absolute/NEW-output-directory
```

`ACTUAL_PAST_CUTOFF`须设置为实际已过去、且不早于所有数据观测的带时区截止时间；未来截止会被拒绝。输出目录必须不存在。先追加协议和原哈希，再核查增量的实际ESPN原响应：赛事/球队ID、主客、FT、两节90分钟、明确非中立、比分、抓取时刻和响应hash；坏行记录到row-audit。新xG/过程字段不填零，已有history整行原样保留。缺少日期清单、独立完赛ID清单或存在未解决缺项，覆盖状态为 `PARTIAL_BLOCKED`。已返回清单中的FT全部取得summary，也不能证明供应商没有遗漏比赛。

current复制weighted容器及MAIN anchor，刷新history/core/时刻；保留baseline配方及原expert weights。ML使用原CONFIG/FEATURES，意甲alpha=.5、瑞典=.25，`rolling_items(history,history)`→`fit_booster`，不调用两个旧train入口，不重选配方或融合系数。新工件兼容原加载契约，`refresh_experiment`明确inactive及覆盖状态；原acceptance指标仅保留为旧版本信息，不能解读为刷新候选已验证。

`predict_current(candidate, fixture)`复用原MAIN赛前入口，并拒绝未知或中立场（旧baseline中立实现问题另轨修复）。`offline-replay.json`直接加载新旧工件，对真实旧fixture调用distribution，明确赛后离线数值检查；最终全量工件包含部分目标标签，不能作为其历史赛前验证。旧first_known_at不足，历史研究不称真实asof；时间前推开发评估本轮未执行。候选不进入今晚分析，也不自动晋级。未来研究按Astra锁定窗口逐场开球前同场冻结。

验证：`python -m unittest discover -s runtime/research -p test_fixed_recipe_refresh.py -v`。真实执行与独立核验留在 `/workspace/WCPP/reports/auto-optimization-20261101/execution/`；`fixed-recipe-002`为最终本轮候选，001为保留的先前执行记录，不用于择优。

测试包含可移植的显式synthetic ESPN响应校验，不代表真实采集或模型训练。私有集成输入缺失时仅该class明确SkipTest；云端资产存在则仍执行真实27项。可用 `WCPP_SKIP_PRIVATE_REFRESH_TESTS=1` 显式验证无私有资产模式，其余portable测试照常执行。
