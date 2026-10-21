# WCPP Python研究工作流

默认架构为Python，无MATLAB许可证依赖。当前模型WCPP-MAIN-1.2和test模型WCPP-ML-0.1按runtime/active-model.json分别输出，永久λ及Champion历史登记不改。原Champion源码未恢复，不声明新实现是V21.5恢复版。

运行环境依赖在runtime/research/requirements.txt；PyTorch CPU轮子单独由官方CPU索引安装，脚本/workspace/WCPP/setup/python/install.sh。已验证CPU可运行ML、PyTorch、Optuna、统计泊松和CartPole PPO能力检查；无CUDA。CartPole不是足球RL训练成果。

冷门活动旁路为WCPP-UPSET-0.1，新0.2独立候选字段upset_candidate由runtime/upset-candidates.json路由。不融合两套比分或概率，不给未验证报警。两者都是研究输出；九联赛分别训练，未训练亚冠不得借用。新增86项特征仅用严格先前日期，训练区没有数据的字段强制屏蔽；英超1900场旧研究xG独立research_xg，绝不混入当前CSV xG口径，已进行候选训练但最终未获选。过去射门和牌数不等于当前首发、停赛或数学战意。

重训：

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python runtime/upset_v2/train.py <new-run-directory> <audited-enriched-history.json>

必须使用新目录，保存预实验协议、数据/代码哈希、校准选择锁和全分母。训练区内Optuna调参，校准区选模型/融合/温度；旧测试已看过，仅开发诊断，不能晋级证明。类别标签为热门赢/平/输，热门不胜单独算；不能代替Primary精确比分Top1。

Luna收集并保存公开来源和时间证据，主代理核验身份、90分钟比分、盘口和权限。后续分析时安排T-24h可用快照及首发发布附近快照；未部署自动定时爬虫，不能称持续自动采集已经运行。runtime/research/snapshots.py校验保存赛前证据，缺项显式列出；不记录密钥或秘密URL。

真实赛前双模型输出独立研究记录：

    python runtime/research/prospective.py record paired-forecast.json --ledger <paired-version-directory>
    python runtime/research/prospective.py review --ledger <paired-version-directory> --results verified-results.json --out <new-review.json>

record验证相同输入、两份实际矩阵及其哈希、赛前生成时间，禁止覆盖。review保留待结算和拒绝项，不同模型版本不得混算，冷门按队列/版本/工件独立评估。该记录不是正式Primary冻结，不重新登记旧500+200。所有收益最终仍须未来Primary Top1同场对照。

完整报告：/workspace/WCPP/reports/python-upset-20261013/Python架构与冷门模型优化报告.md。
