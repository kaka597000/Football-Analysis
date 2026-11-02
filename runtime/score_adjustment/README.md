# 独立比分分布校准研究

模块仅校准固定系统研究基准 `M2-h365-r30`（core、half_life=365、ridge=30、alpha=0），不适用于 current/test 的其他分布；不修改原矩阵或综合 Primary。统计矩阵最大格子只称 `statistical_mode`。

已有研究工件：`/workspace/WCPP/models/score-calibration-20261020/{C0,C1,C2,locked-model}.json`。中段严格 Top1 与损失/偏差门槛选择锁定 C0，不能依据最后段诊断换为 C1/C2。所有历史已可能被检查，属于内部时间复核，不是未来实绩；未来晋级默认禁用。

```python
from runtime.score_adjustment import load, apply, train
artifact = load('/workspace/WCPP/models/score-calibration-20261020/C1.json')
# identity 必须来自实际原工件与已核 hash 的协议，不能为 current/test 伪造相同身份。
identity = {
    'model': 'M2-h365-r30', 'cohort': 'eng.1', 'feature_group': 'core',
    'half_life': 365, 'ridge': 30, 'alpha': 0.0,
    'source_protocol_sha256': '<真实已绑定协议的 SHA256>'
}
calibrated = apply(artifact, original_matrix, base_identity=identity)
# train 只接收早期训练段。调用方必须先按全局日历日隔离 train/select/final。
```

CLI 输入是含 `matrix`、`base_identity` 和可选原 `primary` 的 JSON；禁止把标签传入 apply。输出 `comprehensive_primary` 原样复制输入，校准不生成综合首选。

```bash
cd /workspace/Football-Analysis
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python runtime/score_adjustment/runner.py apply \
  --artifact /workspace/WCPP/models/score-calibration-20261020/C1.json \
  --input /absolute/input.json --out /absolute/new-research-output.json
python -m unittest discover -s runtime/score_adjustment -p 'test_*.py' -v
```

训练 CLI 为 `python runtime/score_adjustment/runner.py train --report <已预注册的新目录> --models <新工件目录>`。原实验结果/锁定文件存在时拒绝重跑；新实验必须先保存协议及 checkpoint，不能覆盖旧结果。主流程从固定19队列的 dev1–3读取并通过 `optimization.ledger.read` 检查 receipts，MLS/沙特使用 repair，拒绝重复 match_id；不选各队列旧开发赢家、不使用 audit 标签。不同大小矩阵只在训练内部零填充，不创造原零概率。

C0 恒等。C1 的主客尺度由训练观测总球/训练矩阵均值总和估计，限制 [2/3,1.5]，可升可降。C2 固定五维 `[h,a,I(h=0),I(a=0),I(total>=4)]`，mean NLL + .01 sum(theta²)，linear 参数限制 ±log(1.5)、indicator 参数 ±1。所有参数仅训练段拟合；中段以比分统计Top1严格优于 C0，且 logloss/CRPS 非劣 .02、绝对总球均值偏差非劣 .05 为门槛。末段只评价已锁定结果，其他候选只能诊断。区间按全局日历周聚类重采样2000次，不能作为选择校正或未来提升证明。

详细结果和预注册：`/workspace/WCPP/reports/score-bias-adjustment-20261020/experiment/`。
