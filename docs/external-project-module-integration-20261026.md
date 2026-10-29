# 外部项目模块审计与融合报告（2026-10-26，北京时间）

## 结论

本轮收到八个压缩包，七个已下载并完成递归安全检查；一个因附件传输层
32 MiB 上限未能下载。七个可读包及其二十个内嵌 ZIP 均未发现路径穿越、
绝对路径、链接、加密成员、重复规范化路径或压缩炸弹预警。

外部包没有任何候选具备未来正式冻结样本上的稳定增益证据，因此不替换
当前模型或 test 模型，不恢复 Champion，不启用第三模型，不修改活动参数。
本轮只融合两个不改变预测数值的基础能力：

1. 逐次调用的入口、注册表、实现源码和模型工件哈希审计；
2. 当前/test 完整比分矩阵的 Jensen–Shannon 分歧诊断。

分歧诊断不改变 Primary，不是校准置信度，不证明两模型独立，也不能用于
赛后择优。当前/test 继续独立冻结和复盘。

## 输入身份

| 包 | 实测字节 | 实测 SHA256 | 状态 |
|---|---:|---|---|
| WCPP_V22.0_RC1_source_and_validation_2026-10-08.zip | 131,220 | `de0f50018c718ddac050a9ae6dcf2e221c426696d4414160a62b9d8775757ae6` | 已审计 |
| WCPP_2026-10-08_actual_analysis.zip | 206,228 | `46a60546a349fc8abbc8e6e4eaba78ca2c0967a159b5a8aacd658f599ae36b68` | 已审计 |
| WCPP_赛事数据库_统一合并版_2026-09-29.zip | 17,743,774 | `4f333e9be01b4e4a5535c6b06547141f2b6cc1dd7afd9acd6aca2e9c15b05e19` | 已审计 |
| 01_统一规范与Canonical.zip | 17,809,456 | `addb56e8c40c8cfd2250a80e95922ae3b8cd0dcd6ea4339e1fe0758b10898eaf` | 已审计 |
| WCPP_三模型已执行_CodexWork可接续_2026-10-21.zip | 30,675,328 | `391e4fbe3d64cec8e4ee263bb09495176a49e389e721e36b2f8929925a6f84ec` | 已审计 |
| 02_原Champion追溯与历史源码.zip | 18,048,543 | `1bebc7be686171173bb84d678654dc43f6e09ff0efc3010d7b5144aaf2572ece` | 已审计 |
| 04_辅助模型_赛事族_数据源.zip | 19,838,593 | `7a81555d2b96a315ffe48db5d9c2ce9f7c1f7c29a60c64bd13bb02a8f4fab74c` | 已审计 |
| 03_模型1_modeltest_第三模型.zip | 未取得 | 未实测 | `file exceeds the executor transfer limit of 32 MiB` |

安全清单在 `staging/import-20261026-external-projects/reports/`。包内文档只作为
证据读取，没有当作用户指令执行。

## 模块处置

| 模块 | 证据 | 处置 |
|---|---|---|
| V22.0-RC1 原始 Poisson | 开发回放 92/684，Top1 13.45%；正式未来样本 0 | 仅保留审计证据，不替换当前模型 |
| V22 四态/残差校准 | 各 89/684；残差较原始 Poisson 少 3 中，配对差异区间跨 0 | 不接入活动链 |
| modeltest 0.3 score repair | 预选 173/1443 对 raw 163/1443；差异 +0.693 个百分点，95% 区间 `[-0.329,+1.719]`，正式未来样本 0 | 继续外部研究，不替换当前 test |
| ExtraTrees 第三模型 | 315/2713 对经验基线 287/2713，但 LogLoss 更差（3.30683 对 3.27697）、Top2 更差且 28 个实际类别概率为 0，正式未来样本 0 | 从当前双模型候选链排除；不复制源码/工件 |
| 外部澳超专家 | 487 条训练；后续 156 场 Poisson 16 中，简单联赛均值 18 中；正式未来样本 0 | 不接入；当前项目已有 2,807 场边界修复版研究工件，外部版本覆盖更弱 |
| 外部 MLS 专家 | 1,916 条训练；后续 420 场 Poisson 47 中、DC 49 中；选择器却固定 Poisson；正式未来样本 0 | 不接入；与现有 `usa.1` 双模型路由重复，且无晋级证据 |
| 单队进球工具 | 2,713 条仅回放注释，只有 18 条完整矩阵，未拟合、未改变 Primary | 不复制；当前 `team_goal_analysis.py` 已有更严格矩阵哈希、区间、缺失和复盘验证 |
| 旧比分诊断 | 包含 Wilson 区间、概率分箱和多矩阵分歧 | Wilson/可靠性当前已有；仅吸收缺失的双矩阵 JSD 分歧诊断 |
| 数据源 smoke | ESPN 可用；Understat/Sofascore 当时未取得 xG/阵容；其他多为连通性探测 | 作为历史证据，不接入采集链，不用旧响应冒充当前数据 |
| Canonical SQLite | 两份解压数据库均为 56,631,296 字节，SHA256 均为登记值 `8b83...bcc0`，与当前 `packages/core-2026-09-29` 完全相同 | 重复副本不入库；当前 1.03 GB 统一库不被旧库覆盖 |
| 10月8日实际分析 | 六场实验性日期输入，伤停、首发、天气和盘口均未数值接入 | 仅保留历史证据，不作为训练/正式冻结 |
| 原 Champion 追溯 | 只读审计与 V22 候选，没有恢复完整可验证 Champion 主链 | 保持归档，不恢复活动身份 |

“排除”表示没有进入当前运行目录或活动注册表；原始附件和审计证据保留，
避免不可逆删除来源。旧 Canonical、第三模型工件、旧采集脚本和重复专家均未
复制到活动代码。

## 实际融合

### 执行身份审计

`runtime/predict_active.py` 现在随每次输出记录：

- fixture、入口源码、活动注册表的 SHA256；
- 当前模型与 test 模型实际执行函数的源码 SHA256；
- 两个实际工件路径、SHA256和模型报告版本；
- `prediction_values_modified=false` 与固定双模型独立策略。

这解决“修改是否真正进入输出”的可追溯问题，不改变任何概率或比分。

### 当前/test 分歧诊断

`runtime/research/score_distribution_audit.py` 新增：

- 两矩阵相同支持、非负、归一化和哈希验证；
- 两个 Primary 是否不同；
- Jensen–Shannon divergence（nats）；
- 明确 `changes_primary=false`、`formal_kpi_eligible=false`。

该结果写在 `current_test_disagreement`，不能作为综合人工改分的唯一证据。

## 验证

在 Python 3.12 环境执行当前项目相关回归测试，共 111 项全部通过：

- research 35；upset/dispatcher 11；v4 35；v5 9；v6 8；v7 4；
  continental 6；aus_expert 3。
- 新增覆盖：相同矩阵 JSD=0、不同矩阵 JSD>0、Primary 分歧标记、矩阵支持
  不一致拒绝、诊断不改原输出、执行哈希存在。

当前活动注册表 `/workspace/WCPP/runtime/active-model.json` 的 SHA256 仍为
`9f3eccbabbf73c5d7b4f96a8da8808a7bd2634baf6d4b7435a68b2e912fb0362`；
本轮没有改模型参数、工件或活动路由。

## 未完成项

`03_模型1_modeltest_第三模型.zip` 尚未取得，因此不能声称八包全量审计完成。
它需要拆成每个小于 30 MiB 的分卷，或提供 GitHub/直接下载地址。已读取的
`WCPP_三模型已执行_CodexWork可接续_2026-10-21.zip` 已包含 model1、
modeltest 和 third 的可执行副本，但不能假定它与缺失包逐字相同。
