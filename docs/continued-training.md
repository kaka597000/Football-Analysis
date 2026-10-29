# 继续训练：ML-0.2候选

2026-10-12用户授权继续训练。当前模型WCPP-MAIN-1.2及活动test模型WCPP-ML-0.1保留。ML-0.2是非活动研究候选，不能改写旧预测、混算版本命中率或自动替代原Champion。

本轮从只读统一库补充来源有明确场地标记的更早常规赛历史；仅导出training_eligible且90分钟比分、双方身份、日期一致的资料。未能确认阶段或场地的旧资料不补造。历史同一目标的输入排除目标日期及之后的进球、xG和射门数据。

训练集由5005扩展至8451，历史xG2370行、额外射门资料4774行。英超1950行xG中部分来自callumfm/dixon-coles的9季公开研究CSV，按比赛日期、双方已登记别名及90分钟比分与库内比赛交叉匹配；其FPL/Understat混合口径和缩放方式保留标记，首个发布时间未知，所以不声称历史当时可知的独立验证。其他联赛部分HxG/AxG来自football-data.co.uk归档，不是football-data.org账户实测。

42个特征槽包括历史xG、原有射正/射偏/控球率，以及可以独立缺失的总射门与覆盖率。足球数据站HS/AS不能直接减去HST/AST后冒充5Dollar的射偏数；本轮单独保留shots_total。缺控球率不会使可用总射门被丢弃。

各分组选取4套树叶数/正则/迭代参数及5个融合系数，共20组合，只在原选择段比较Top1并以比分log loss破同分；测试段沿用上轮的同一场次，不能参与选参数。允许融合系数0，表示该组选择DC而不执行ML残差。全量重训的部署候选在完成测试之后生成。

本轮相同1238场已查看过的开发测试中，当前/旧test/新候选均150个第一比分命中，没有通过替换门槛。目标70%并未实现；重复使用已查看测试段不构成新独立验证。阵容、伤停、数学战意、旅行、完整体能和诚信系数仍未完成数值训练。

文件：/workspace/WCPP/models/ml-continued-20261012。候选状态见runtime/training-candidates.json；默认预测仍读runtime/active-model.json。

复现训练（输出必须新建目录）：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python runtime/v7/train.py --registry /workspace/WCPP/models/ml-continued-20261012/active-registry-snapshot.json --data /workspace/WCPP/models/ml-continued-20261012/data-final --out NEW_DIRECTORY
```

可加`--cohort eng.1`指定分组。软件验证：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m unittest discover -s runtime/v7 -p 'test_*.py'
```

需要显式研究新候选时使用`python runtime/v7/predict.py artifact.json future-fixture.json --out new-pair.json`；同样需要真实未来比赛、可核输入与实际盘口，不能重算已开场比赛后冒充赛前。

新API兼容用户实际变量Football_data_api、Apifootballcom_APIKEY。运行时显示允许目的地为www.football-data.org和dashboard.api-football.com，而实际请求为api.football-data.org和v3.football.api-sports.io。本轮实测分别400/403，不认定账户权限已经可用或钥匙无效。配置工具因现有绑定冲突没有保存修正；需要在环境设置修改现有绑定的允许域名，不能创建重复密钥。
