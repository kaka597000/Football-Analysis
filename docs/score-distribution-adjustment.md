# 比分分布与综合选分：2026-10-20实际执行规则

本文件记录新增代码行为；用户第一比分需求仍以 `user-primary-score-requirements.md` 为准。当前/test/综合判断独立，综合判断不得用统计众数替代，也不得为了比分显得更大而机械加球。

## 已接通的入口

`runtime/predict_active.py` 的 `run()` 和命令行现在同时输出每侧 `team_goal_analysis`、`score_distribution_audit` 和工件实际 `training_data_cutoff`。原矩阵、概率、Primary、融合参数不变。审计给出单队均值/区间、0球与1球概率、总球分布、三球/四球以上概率、总球众数与联合比分众数，以及三球/四球路径的原模型候选。它们是候选比较材料，不自动成为推荐，不是提供方xG或已校准的综合概率。

```bash
cd /workspace/Football-Analysis
OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/wcpp-mpl \
  python runtime/predict_active.py /absolute/new-fixture.json --out /absolute/new-pair.json
```

程序API仍只计算；CLI默认在正式台账追加同场双模型冻结。输入必须真实核验并处于赛前。冻结后综合判断使用 pair 输出的 `freeze_receipt` 引用，不改原矩阵。不要对已经开场的旧比赛重跑后追认赛前。

## 综合记录需要比较哪些内容

`runtime/research/analysis_freeze.py` 的 `save(..., track='comprehensive', stage='prematch')` 读取 payload 的 `current_test_reference`，核验被引用冻结的哈希、比赛ID、开球时间、轨道和时间先后，在**新冻结记录顶层**写入 `selection_audit`。原 payload 不变；旧记录无需迁移或补写。

payload 使用下列字段，内容必须来自实际赛前分析，示意字符串不是比赛结论：

```json
{
  "primary": "1-0",
  "wdl": "主胜",
  "total_line": 2.5,
  "total_direction": "小",
  "current_test_reference": {"path": "实际双模型冻结路径", "sha256": "实际冻结哈希"},
  "selection_evidence": {
    "compared_scores": ["1-0", "1-1", "2-1"],
    "zero_vs_one": {
      "away": {
        "reason": "结合已核实近期同源xG/xGA、主客、阵容和赛程，解释为何选0球而非1球",
        "counterevidence": "记录支持客队进球的实际反证，不仅复述选择理由"
      }
    },
    "three_plus_path": "比较领先后继续进攻、落后追分和双向得分的证据与限制",
    "total_reason": "针对所列实际盘口独立判断，不从唯一比分总和直接推大小球"
  }
}
```

- 至少两个明确比分候选，包括最终Primary。选择任一侧0球，必须写该侧0与1球的支持和反证。该规则同样适用于0–0、0–2等零封比分。
- 只要一侧模型联合众数总球≤2而完整P(总球≥3)>50%，必须显式比较至少一个三球以上候选及其路径。50%只触发比较，不是经训练的选分阈值，不自动升球。
- 选择大/小时必须有独立实际盘口理由；概率属于引用模型，不冒充综合判断概率。
- 缺比较仍允许全批如实冻结，记录 `NEEDS_COMPARISON`；无矩阵、无引用、待定仍保留，不剔除分母。完整性检查不证明理由正确、数据充分或命中率提升。

## TOP3实际约束

新 `top3-selection` 赛前冻结只接受已经通过上述比较完整性检查的综合冻结引用。名单最多3场、ID不重复、排名连续；比分/胜平负/盘口/方向必须与被引用综合冻结一致，时间不得穿越。资料不齐时不足3场，不强凑，也不删全批次记录。规则不改旧TOP3及其表现。

`save` 支持原命令：

```bash
python runtime/research/analysis_freeze.py /absolute/comprehensive-input.json \
  --stage prematch --track comprehensive
python runtime/research/analysis_freeze.py /absolute/batch-top3-input.json \
  --stage prematch --track top3-selection
```

输入封装为 `{"fixture": {...}, "payload": {...}}`。TOP3 payload沿用 `top3` 数组，每项包含 `rank/match_id/primary/wdl/total_line/total_direction/comprehensive_reference`。

## 校准研究与晋级

`runtime/score_adjustment/` 已实际训练两种独立校准候选；19队列8963场，日期隔离5159/2385/1419。选择段两者各280/2385，原基准282/2385，且偏差门槛失败，锁定恒等C0。末段仅9队列有覆盖，不能称19队列均通过验证；历史已参与开发，不是未来未见实绩。C1/C2只绑定系统研究M2基准，不能套用到MAIN/ML冒充同一模型；未晋级，未进入当前/test路由。

完整结果、逐场来源及回滚：[实际调整报告](/workspace/WCPP/reports/score-bias-adjustment-20261020/小比分偏向诊断与实际调整.md)。未来仍以完整预先冻结分母的Primary Top1评价；大球比例提高不算成功。
