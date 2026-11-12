# 2026-11-12 比分混合权重独立实验

本轮没有证据支持切换活动权重。固定方案实际跑完后，末段 1,696 场的统计比分 Top1 从 190 场变为 189 场；胜平负从 826 场变为 836 场。比分最高目标没有改善，概率质量的轻微改善不能替代它。Current/Test、注册表和历史冻结未改动。

这些是反复使用过的真实历史数据上的 **development_diagnostic**，不是全新留出、真实时点认证或未来效果。研究基线 `goals_only` 也不是活动 Current；矩阵模态不是独立综合 Primary。综合 Primary 与实际盘口大小球本实验均无合格输入，后者有效分母为 0，未用固定 2.5 代替。

## 固定协议与可复算输入

协议先于本实验读取赛果写入，SHA256 为 `7d76c14924c2db906faaee8a511dc93432e1963ccaf0b60c88bb4158078e942b`。原始预测输入 SHA256 为 `3b33bf9f8d3f81add48bf17ad8b43f3837c012e7c60a6906f5a628ce446276fa`。

- 输入为既有 native-scale xG 实验的 8,685 条逐场预测、253 份月度拟合记录和来源协议；英超、西甲、德甲、意甲、法甲独立处理。
- 基础模型按先前日期逐月拟合；本次不重训，不更改基础模型。已知训练日期无越过拟合边界；1,090 个暖启动训练 ID 的日期不在预测文件中，本次核验不能独立认证这些日期及历史 first-known 时间。
- 每个精确赛事/阶段队列按完整日期分成 60% 选权重、20% 独立门控、20% 一次末段诊断，共 5,272 / 1,717 / 1,696 场。同一日期不跨分区。
- 候选仅为 `goals_only` 与 `fresh_missingness` 或 `opponent_residual` 的概率混合；α 固定为 `0、0.25、0.5、0.75、1`。α 是第二专家的概率质量权重，不是进球率平均。
- 选权重优先统计 Top1，要求比分 log-loss、WDL Brier、WDL 命中相对同场研究基线不劣。独立门控只评一次已选候选；任一条件失败回原基线，不尝试第二名。
- 每队列权重在该队列末段评估前保存独立 selection-lock；末段结果不反向选参。分母不足 100 时保留基线；身份、哈希、训练日期或概率异常停止，不删异常行补做。

混合器保留无限支持泊松的真实比分概率、Skellam 胜平负、零球与高比分尾部概率；有限区域仅用于寻找确切模态，不截尾再归一化。正温度校准本身不会改变同矩阵的 argmax，本次也没有把它当比分命中优化。

## 权重与一次末段结果

所有“通过”仅指开发实验内部门控，均不代表活动晋级。

| 队列 | 选权重阶段结果 | 独立门控 | 末段 n | 基线→门控候选统计 Top1 | 基线→门控候选 WDL |
| --- | --- | --- | ---: | ---: | ---: |
| 英超 | goals_only，α=0 | 保持基线 | 422 | 46→46 | 193→193 |
| 西甲 | opponent_residual，α=0.5 | Top1 少 1，拒绝 | 329 | 50→50 | 168→168 |
| 法甲 | opponent_residual，α=0.5 | Top1 少 1，拒绝 | 334 | 32→32 | 157→157 |
| 德甲 | goals_only，α=0 | 保持基线 | 261 | 23→23 | 128→128 |
| 意甲 | opponent_residual，α=0.75 | 开发门控通过 | 350 | 39→38 | 180→190 |
| 合计 | 各队列独立 | 不晋级 | 1,696 | 190→189 | 826→836 |

末段覆盖率为 1,696 / 1,696。日期聚类 bootstrap 2,000 次，固定 seed=20261112：统计 Top1 净 -1 场，净命中率 95% 诊断区间为 [-0.502，+0.383] 个百分点；WDL 净 +10 场，区间为 [0，+1.238] 个百分点。这些区间未校正历史反复研究，不能充当正式晋级证据。意甲单独的 WDL 区间也包含 0。

比分 log-loss 从 2.955985 降至 2.954121，WDL Brier 从 0.617023 降至 0.616583。未经门控的候选只有 186 场比分命中，比基线少 4；门控避免了一部分损失，但没有使最高指标出现可靠提升。

1–1 模态输出从 1,287 场变为 1,284 场，实际 1–1 为 185 场；基线平均 P(1–1)=11.384%，实际发生率为 10.908%。零球平均预测 26.288% 对实际 26.887%，≥4 球概率 30.426% 对实际 30.601%。模态集中不能直接等同于整体概率失准，不能因此手工加球或压低 1–1。≥4 球只是分布诊断，不是实际盘口结算。

## 活动双轨固定网格复算

另用既有 `score_frozen_alpha_grid.py` 重新验证 214 条原首次冻结与组件哈希，没有再次选权重。194 条属于历史重构，其中 76 条有合格赛果、118 条缺赛果；20 条未来冻结仍待结算。76 场上 Current/Test 统计 Top1 为 3/5；固定 α=0/.25/.5/.75/1 分别为 3/4/5/6/6，WDL 分别为 42/40/39/38/42。α=0 不是 Current。小样本、历史重构和未完成未来验证不支持采用末段最好的 α。

## 代码、证据与复现

实现：[score_mixture_shadow.py](../runtime/research_program/score_mixture_shadow.py)；运行入口：[run_score_mixture_shadow.py](../scripts/run_score_mixture_shadow.py)；验证：[test_score_mixture_shadow.py](../runtime/research_program/test_score_mixture_shadow.py)。8 项测试覆盖概率混合、无限尾部、整数模态平局、Top1 优先和概率守门、失败回退、日期队列隔离、输入异常及同日训练越界。

工作区证据包：`/workspace/optimization-20261112/numerical/`，包含 `protocol.json`、`run-002/execution-lock.json`、逐队列 selection-lock、`report.json`、1,696 行 `diagnostic-predictions.jsonl`、`manifest.json`、`calibration-diagnostics.json`、`alpha-fixed-grid-recomputed.json` 及运行/测试日志。最终长期位置由总交付索引记录。

第一次运行在评估前因日期字符串与带时区拟合时间直接比较而终止；已修复为显式 UTC 时间比较并新增回归测试，保留 `run-001.log`。没有改变已锁网格、日期分割或门槛。

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=runtime \
  python -m unittest research_program.test_score_mixture_shadow -v
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  python scripts/run_score_mixture_shadow.py \
    --protocol /workspace/optimization-20261112/numerical/protocol.json \
    --out /workspace/optimization-20261112/numerical/reproduction-new-directory
```

复现依赖协议列出的原输入，先核验哈希；输出目录和文件只能新建。代码完成、历史开发诊断完成；活动部署未变，综合 Primary 效果未验证，未来效果未验证。保留此次失败/无增益结果，后续只能用新事前方案与未来冻结验证，不回头反复调这次末段。
