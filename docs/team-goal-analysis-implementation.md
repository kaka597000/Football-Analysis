# 单队进球摘要实现与调用

实现：`runtime/research/team_goal_analysis.py`，版本`WCPP-TEAM-GOALS-0.1`。
业务解释和分析顺序见[单队进球政策](team-goal-analysis-policy.md)。

## 已接入范围

`runtime/predict_active.py`的CLI在`run()`返回之后、发布冻结之前，分别为`current_model`和`test_model`添加`team_goal_analysis`。输出JSON与同次冻结的payload均保留这两份摘要。纯计算`run()`的输出合同不变；直接调用`run()`的业务方需要显式调用`attach_pair(output)`。

独立研究候选、xG Shadow和优化工具的推理源码、注册与工件保持原有流程，本次未自动接入它们，也未重训。未来业务展示这些轨道时，应对该轨实际输出另调用`summarize_prediction(prediction)`，在新分析版本发布前保存；不得借用当前/test摘要。既有冻结不可回写字段。

## 字段与数学口径

- `source_matrix_sha256`使用原矩阵JSON（UTF-8、排序键、紧凑分隔符、禁止NaN）的SHA-256；若预测包含`matrix_hash`，必须与之吻合。
- `matrix_axes.home_goals=0`、`away_goals=1`：行对应主队进球，列对应客队进球。主队边际逐行求和，客队边际逐列求和。支持非正方形矩阵。
- `home`与`away`分别含`mean`、`marginal`（下标即整数进球数）、`p0`、`p_ge_2`、`p_ge_3`和`central_interval`。
- 区间默认名义覆盖`.8`，下/上分位为`.1`/`.9`。取CDF达到分位的最小整数，端点均包含；仅用四个机器精度量级的相对容差处理浮点并列。`matrix_coverage`是区间内原矩阵边际概率之和；`width=upper-lower`，单位为球，不是整数个数。
- 非空、二维、有限、非负、实数且原概率和为1才可计算；和的绝对浮点容差为`1e-9`，不重新归一。非法矩阵抛`ValueError`；有`ABSTAIN`状态或缺矩阵则返回`UNAVAILABLE`，不补造数值。`OBSERVE`但有有效真实矩阵仍可计算。
- 校准状态固定为`MODEL_IMPLIED_NOT_EMPIRICALLY_CALIBRATED`。均值不是提供方原始xG，概率质量不是实测历史覆盖率，区间不是硬边界或单场保证。

## 独立sidecar CLI

在`/workspace/Football-Analysis`运行：

```bash
python runtime/research/team_goal_analysis.py /path/to/prediction.json --out /path/to/new-team-goals.json
```

支持单轨预测、含`current_model`/`test_model`的双轨输出，以及`analysis_freeze.py`生成的冻结记录；冻结记录先核验原checksum。`--coverage .8`可显式指定名义覆盖。输出仅含来源文件路径/文件哈希与摘要，不改写输入，不允许覆盖已有输出，标注`DERIVED_SIDECAR_NOT_AN_ORIGINAL_PREMATCH_FREEZE`。给历史记录生成sidecar不构成历史原始赛前字段或重新冻结。

## 复盘API

```python
from runtime.research.team_goal_analysis import summarize_prediction, review
summary = summarize_prediction(actual_model_output)
diagnostics = review(summary, home_goals=actual_home, away_goals=actual_away)
```

调用者须先核实比赛、模型版本和90分钟实际赛果。双方独立保存`mean_error=预测均值-实际球数`、`absolute_error`、`squared_error`、`interval_hit`和`interval_width`。这是独立诊断，不改Top1、胜平负、大小球的分母或结算；单场命中不能证明区间实测覆盖80%，跨样本汇总仍须保留完整未来样本。

## 针对性验证

```bash
python -m unittest discover -s runtime/research -p 'test_team_goal_analysis.py' -v
python -m unittest discover -s runtime/research -p 'test_analysis_freeze.py' -v
python -m unittest discover -s runtime/upset -p 'test_dispatcher.py' -v
```

测试只用合成分布及临时目录；不改任何真实旧冻结。前两套覆盖轴/均值、离散实际质量、分位边界、非法输入、ABSTAIN、OBSERVE、矩阵hash、逐队复盘、sidecar不覆盖，以及CLI/freeze两份独立摘要和原矩阵/Primary保持不变。第三套检查既有默认推理分派。
