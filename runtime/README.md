# WCPP 云端数值实现 0.1

HELLO BOSS。该实现是新的 Challenger，Python 3.12 标准库即可运行，没有第三方依赖。不是原 V21.5 Champion 恢复，不代表已提升精确比分 Top1。使用已有云端目录，不创建 Git worktree。

已实现：同赛事族时间衰减及收缩强度拟合、Poisson 比分基线、64 格与三个互斥 8+ 尾部桶、Primary/Alternative、胜平负、指定大小球盘口（整数/半球/四分之一盘口的全赢、半赢、走、半输、全输）、来源时间校验、带代码和参数哈希的研究冻结、90 分钟赛果复盘及历史只读重放。数值计算内部最多至80球，输出计算截断误差。并列概率固定按较低主队/客队进球排序。

λ必须有来源，不默认猜测球队强度。拟合器默认180天半衰期及8场等效先验是本实现的新候选设置，不是从原Champion恢复的参数。按赛事族分别拟合，未知球队拒绝预测，不静默套用其他赛事。输入历史至少20条，须明确90分钟口径、开赛时间、赛果known_at、来源；晚于训练截止的赛果自动排除，缺失时间则拒绝。原库的历史时间缺口不能用赛果反补。

尚未实现原 TFA/MPF/URE/ISP、Selector4.1-RP1、Four-State/DC/NB及市场有界重排数值链。暂以 Raw Mode 作为透明基线，不用媒体叙事手工改λ或Primary。`shadow.py`利用库内标准化/逻辑回归系数执行Shadow推理及8×8条件分布IPF，核对726条历史记录；不替代正式Primary，不晋级，不校准8+尾部。500次迭代、1e-10残差为新实现数值求解设置；未收敛必须保留状态，不能称已得到满足目标的矩阵。

## 验证

工作目录 `runtime`：

```bash
python3 -m unittest -v
python3 wcpp.py replay ../packages/core-2026-09-29/WCPP_赛事数据库_统一合并版_2026-09-29.sqlite --out ../reports/cloud-baseline-replay.json
python3 shadow.py ../packages/core-2026-09-29/WCPP_赛事数据库_统一合并版_2026-09-29.sqlite --out ../reports/cloud-shadow-replay.json
python3 wcpp.py predict demo-fixture.json --out ../reports/cloud-demo-prediction.json
```

Replay只使用已保存λ重算Poisson及其历史指标，不重新拟合，不认领未来验证。报告分别列原历史train/test及联赛。输出文件拒绝覆盖，复跑时使用新文件名。

## 数据与运行

`fit`历史输入为JSON数组，每行字段：`cohort, home, away, kickoff_at, result_known_at, score_basis="regulation_90", home_goals, away_goals, source_url`。所有时间含时区。训练前应完成比赛去重及身份/来源核实。

```bash
python3 wcpp.py fit history.json --cutoff '2026-10-06T18:00:00+08:00' --cohort MEN_TOP5_LEAGUE --out parameters.json
python3 wcpp.py predict fixture.json --parameters parameters.json --out prediction.json
python3 wcpp.py freeze prediction.json --parameters parameters.json --directory ../research-freezes
python3 wcpp.py evaluate ../research-freezes/实际记录哈希.json result.json --out evaluation.json
```

上述数据文件由实际数据提供，不是已存在或已经验证的真实比赛示例。`fixture.json`需要：`match_id, home, away, competition, cohort, phase="PreMatch", identity_scope="jingcai|beidan|extended", cutoff_at, kickoff_at, total_line, sources`。sources每条需`source_url, source_tier, supports_field, known_at, query_time`；有原始发布时间则另提供`source_published_at`。字段来源时间均不得晚于cutoff。

拟合参数会自动生成λ及参数哈希。不指定参数文件时须提供`lambda_home, lambda_away, lambda_provenance`，只能生成研究草稿。正式调用freeze必须提供实际拟合参数文件，同时`identity_verified=true`及`context_audit_complete=true`；这两个标记是操作者对身份和情境核查的声明，程序无法自行证明网页事实正确。演示输入标`demonstration=true`，不能冻结。输出冻结状态始终为CHALLENGER_RESEARCH_FREEZE，formal_validation始终false。不会重新预注册500+200。

`result.json`需要`match_id, home_goals, away_goals, score_basis="regulation_90", known_at, source_url`。以冻结记录哈希核验原始记录，不修改预测；竞彩、北单及Extended保持独立范围。复盘记录只代表该Challenger研究样本，不能与原Champion成绩混算。

冻结只允许实际尚未开赛的比赛；绝不回填旧冻结时间。文件创建拒绝覆盖，已有文件在逻辑上保持追加；云端文件系统本身不提供防篡改签名，哈希不是第三方时间戳证明。长期验证需外部可信留存。

API密钥仅通过WCPP_5DOLLAR_API_KEY使用，不放入输入、输出、参数或日志。该工具当前不发网络请求，也不调用付费接口。
