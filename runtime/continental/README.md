# 欧冠独立队列

默认活动路由 `uefa.champions:NEW_FORMAT:league_phase`，显式 competition=`uefa.champions`、stage=`NEW_FORMAT:league_phase`。来自2024/25以来男子成年欧冠联赛阶段；不借用旧组赛、资格赛或淘汰赛数据。范围和阻断状态见 `/workspace/WCPP/runtime/ucl-queues.json`。

当前模型使用既有MAIN1.2统计基线anchor；test使用独立 `WCPP-ML-UCL-0.1`。它沿用LightGBM泊松残差/DC，但射正、射偏和控球按字段处理缺失，未知射偏为NaN，不由射门总数冒充。原ML0.1代码和旧14条活动路由不改。默认predict_active.py按route.test_version分派；不能拿旧组赛字段直接改stage套用。

训练：

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python runtime/continental/train_queues.py --inputs <audited-one-cohort-file> --out <new-directory>

输入可以rows列表或包含rows的字典，须有来源、比分90分钟、显式neutral、era、competition、cohort、stage、观察时间。统计过程为 `process.home`/`process.away`各自字段及 `process_evidence`；query_time不得晚于observed_at。首见时间缺失标回溯，不能冒充历史冻结快照。

新增注册必须逐项校验hash、保留原路由并记录registry前版本；训练是研究执行，不是正式晋级。当前新队列306行，81病例开发测试MAIN4/81、test8/81，绝对准确率仍低；未来Primary Top1目标未证明。分类器没有UCL队列，保持ABSTAIN。

预测需实际未来kickoff、phase=PreMatch、训练中出现的相同provider球队ID、真实sources与cutoff、实际neutral。综合上下文、赛制规则和实际盘口不全时保留OBSERVE及缺项。实际盘口矛盾不能强填total_quote；Arsenal-Lille readiness捕获hasOdds=false与pickcenter共存，未施加大小球线。

真实赛前研究台账用runtime/research/prospective.py，兼容MAIN1.2未提供input_hash时独立计算保存；矩阵hash和同输入仍必须验证，不冒充正式Primary冻结。资格赛/淘汰赛需单独按年代、轮次和首/次回合训练注册；未知阶段不得自动借league_phase。

报告：/workspace/WCPP/reports/ucl-onboarding-20261013/欧冠队列接入报告_2026-10-13.md。
