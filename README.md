# Football-Analysis · WCPP

足球赛前分析与可复现研究：当前模型/test模型独立并行，保留比分矩阵、唯一Primary、胜平负及实际盘口大小球。综合人工判断另轨保存，全部发布分析追加冻结，复盘不事后择优。

当前说明更新于2026-10-29。唯一最高KPI是未来真实冻结Primary精确比分Top1，尚未证明达到70%；工程重构和训练完成不等于预测改善。原Champion V21.5不是目前恢复运行的模型。

## 运行入口

先阅读[用户需求](docs/user-primary-score-requirements.md)与[执行规则](AGENTS.md)。默认入口读取外部活动注册表，目前为WCPP-DUAL-4，登记19个赛事队列。各队列实际引擎及当前/test工件不同，以注册表和[本次身份摘要](manifests/runtime-assets-20261029-final.json)为准。

```bash
python runtime/data_sources/cli.py --plan batch-plan.json \
  --cache-dir /workspace/WCPP/cache/api-first --out /workspace/WCPP/staging/new-api-evidence.json
python runtime/predict_active.py fixture.json \
  --registry /workspace/WCPP/runtime/active-model.json \
  --out /workspace/WCPP/outputs/new-paired-prediction.json
```

这些命令需要真实、已核验的输入及外部模型资产，仓库不提供伪造的生产参数或比赛ID。
采集入口先适用API/缓存，再把缺项交Luna查网页；证据核验后才进入预测。
预测CLI自动追加保存双模型冻结；程序化run()只返回数值，不等于已冻结。
未知队列、工件哈希不符或不合格身份不得静默换模型。

赛事等级、男女、年龄、赛制、90分钟口径先区分；近期状态、对手强度、xG/xGA、战意、阵容伤停、休息旅行及盘口分别核查。缺数据时明确缺项，未训练模块不造权重。TOP3按规范冻结，不足三场不强凑。

## 安装与测试

Python 3.12，测试依赖：

```bash
python -m pip install -r requirements.txt -r requirements-test.txt
python scripts/check_repository.py
python scripts/run_tests.py
```

所有测试目录独立进程运行，避免同名模块冲突。测试不发真实API请求。少量集成测试依赖WCPP外部工件或历史数据，缺少时明确跳过；实际云环境有资产时应运行。GitHub Actions执行同一检查，远端是否通过以实际CI状态为准。

完整Python实验依赖另见runtime/research/requirements.txt，深度学习与强化学习实验不自动接入活动预测。

## 结构

|路径|职责|
|---|---|
|runtime/predict_active.py、active_route_engines.py|活动双模型路由、推理及执行身份审计|
|runtime/v4、v5、v6、continental、aus_expert|当前模型及队列特定的独立test实现|
|runtime/research|冻结、比分分布/单队进球、伤停解析、复盘与研究快照|
|runtime/data_sources|安全接口读取、接口优先批采集与过程数据匹配|
|runtime/database、emperor_cup|赛事入库、身份与来源审计、杯赛扩展|
|runtime/optimization、optimization_tools、v7、upset*、score_adjustment|独立研究、候选与诊断，不能仅因代码存在就称已用于Primary|
|docs、manifests、scripts|规范、变更、运行资产身份及交付检查|

runtime/wcpp.py、v2/v3等保留为历史实现和测试依赖，不能把它们的旧README当当前默认入口。

## 数据与版本管理

源码、配置、测试、规范和身份摘要进入Git；密钥、数据库、原始API/网页快照、冻结输出、大型工件及缓存独立保留。`manifests/`中的哈希为实测，但身份摘要不等于资产备份，克隆仓库仍需匹配外部工件与数据。

密钥仅从环境变量读取，`.env.example`只有变量名。接口覆盖与权限按实际响应判断；APIfootball.com与API-Football/API-SPORTS不是同一个服务。中国体彩官方及镜像禁止访问，澳客允许。赔率变化不等于真实资金流量。

每次可交付修改完成后测试、提交、推送并核验远端HEAD；检查点和回滚方式见[版本管理规范](docs/version-management.md)。不新建定时任务。

关键说明：[接口优先采集](docs/api-first-collection.md)、[双模型流程](docs/dual-model-workflow.md)、[冻结规范](docs/analysis-freeze-policy.md)、[比分分布审计](docs/score-distribution-adjustment.md)、[变更记录](CHANGELOG.md)。
