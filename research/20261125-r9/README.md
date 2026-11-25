# R9 球队的进球数分布

一支球队过去有多少次进了零球、一球、两球、三球或至少四球？R9 比较每一档的实际次数和当场赛前基准预期的次数，失球也独立计算。对近期的比赛赋更大权重，并为低频档加入平滑。新队或新阶段没有可用历史时，该队的调整量为零。赛事和阶段以面板实际身份区分。[训练前登记](protocol.json)

每个赛事只拟合两个系数，分别表示两队进攻记录和对手防守记录的作用。训练和选权使用不同日期窗口，先比较首选比分，再比较三选和比分对数损失。历史回放用固定系数和不断向前更新的球队记录。同日所有比赛完成预测后，程序才将当天赛果写进球队历史。

## 复算

使用项目的 Python 3.12 环境和 LightGBM 4.6.0。下列变量指向数据仓库对应提交下的证据目录。

| 变量 | 数据提交 | 目录 |
|---|---|---|
| `R5` | `a2c680d` | `workspace-updates/20261123-all-scope-kpi` |
| `R7` | `9490479` | `workspace-updates/20261123-prematch-literature` |
| `R8` | `7e9e6a2` | `workspace-updates/20261125-r8-prematch` |
| `OLDER` | R9 数据交付 | 解压 `inputs/pre2006.tar.gz` 后包含 `panels/` 的目录 |

```bash
export PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
python -m research_program.r9_score_study --r5 "$R5" --r7-evidence "$R7" \
  --r8-evidence "$R8" --out "$NEW_ARCHIVE_OUTPUT"
python -m research_program.r9_score_study --older "$OLDER" --out "$NEW_OLDER_OUTPUT"
```

主回放和近期窗口逐场核对原 R7、R8 的比分和两个方向，三个对照均按原始归档计分。运行时重建与原首版有选分差异时，逐场保存两份结果。原首版决定前三名，完整排名的其余位置取重建矩阵的顺序。零调整沿用原首版。另有 `both_local` 比较，把 R7 和 R9 都放在当前数值环境下计分。2005/06 六联赛测试独立重训三代模型，市场基准只在原始报价有覆盖的指标上计算。历史报价的原始接收时间未知。

每个窗口导出逐场预测、实际比分的完整排名、四项命中、逐赛事覆盖和按比赛日抽样的同场差区间。每项赛事的 `fit.json` 保留选权前每个强度的成绩，主面板的 `forward.json` 另存重新拟合的赛前工件。使用全强度的固定对照也进入逐场记录。

独立研究首次冻结入口为 `research_program.r9_forward`。它核对 R8 的注册和首次回执、原始赔率冻结、R7 选分，以及 R9 工件与 R7 配方的哈希。复核已有冻结时使用归档回执和 `research_program.goal_state_forward`。首次冻结的证据由数据 PR 随附。
