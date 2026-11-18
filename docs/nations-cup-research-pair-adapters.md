# 欧国联与日联杯：显式研究双轨适配

`runtime/nations_cup_pair_adapters.py` 提供 `predict_pair(fixture, route)`，只计算、不发布、不写注册表。调用者须另行冻结，不能将返回的统计首选当成综合第一推荐。旧活动入口不变。

研究注册表外层为 `WCPP-RESEARCH-PAIR-REGISTRY-1`，`routes` 以赛事代码索引。每项包含：

- `engine: nations_cup`、`status: READY_RESEARCH_PAIR`；这是可计算研究候选，不是Champion状态。
- `competition`、`stages`、`index_path`、`index_sha256`。
- 可选 `dependency_paths`：原绝对文件路径到恢复文件路径的精确映射。每次读取校验原工件登记的SHA，原位置存在且内容冲突则拒绝。不得修改旧工件SHA来适配新代码。

欧国联仅接受 `WCPP-NATIONS-REPAIR-INDEX-1`，按A/B/C/D等级和league-phase选择固定配方 `fixed_current` / `fixed_test`。不选择历史诊断中表现较好的候选，不启用challenger。复用严格未来来源验证、原数值算法；对已训练源码按完整模块集合及SHA校验，允许仅执行路径迁移。

日联杯仅接受 `WCPP-JCUP-QF-TRANSFER-1`，限定2026/27八强双回合机制证书。fixture须明确 `cohort=jpn.league_cup:qf-transfer:leg1` 或 `leg2`，不得映射J1。规则证书仅在临时副本内迁移已核SHA的证据路径，并由原证书验证器再次验证；原证书与工件字节不改。次回合仍须经严格核验的首回合90分钟赛果，缺失即拒绝。

返回 `current_model` / `test_model`，身份与阶段、统计矩阵、代码/依赖/工件SHA审计，固定 `formal_kpi_eligible=false`、`automatic_promotion=false`、`active_route=false`。不向缺失实盘填写默认盘线，不修改历史冻结。

验证命令：

```sh
PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1 python -m unittest runtime.test_nations_cup_pair_adapters -v
```

2026-11-17工程核验：恢复754件原归档字节。8场仍未来的欧国联真实输入，双轨矩阵与原 `repair.distribution` 逐元素一致；日联杯4场首回合以显式离线时钟回放核验，两轨矩阵与原 `model.distribution` 逐元素一致。次回合4场缺首回合记录，全部按预期拒绝。正常时钟下旧日联杯8场均拒绝，不制造回溯赛前预测。以上为工程兼容性而非未来效果、活动晋级或新发布冻结。
