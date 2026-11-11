# Football-Analysis · WCPP

足球赛前分析与可复现研究。当前模型/test独立运行，综合人工判断另轨保存；所有发布分析追加冻结，首次版本与修订分别评价。

[当前状态](CURRENT_STATE.md) · [模型与代码用途](models/README.md) · [架构](ARCHITECTURE.md) · [运行](OPERATIONS.md)

## 运行

先阅读 [执行规则](AGENTS.md) 和 [用户需求](docs/user-primary-score-requirements.md)。活动版本和工件路径读取外部注册表；克隆源码不能恢复完整运行资产。未知队列、工件哈希不符或身份不合格时不得静默换模型。

```bash
python runtime/data_sources/cli.py --plan batch-plan.json \
  --cache-dir /workspace/WCPP/cache/api-first --out /workspace/WCPP/staging/new-api-evidence.json
python runtime/predict_active.py fixture.json \
  --registry /workspace/WCPP/runtime/active-model.json \
  --out /workspace/WCPP/outputs/new-paired-prediction.json
```

输入必须是真实、已核验的比赛。采集先适用API/缓存，再补网页缺项。预测CLI自动保存双模型冻结；程序化 `run()` 只计算。输出使用新路径，恢复失败交付见运行文档。

如需独立融合参考，见[融合入口](docs/unified-model-fusion.md)。当前等权融合为未训练研究基准，不替换两模型或综合判断。

## 安装与测试

Python 3.12；基础和测试依赖统一在仓库根目录维护：

```bash
python -m pip install -r requirements.txt -r requirements-test.txt
python scripts/check_repository.py
python scripts/run_tests.py
```

测试不发送真实API请求。外部资产缺失时普通检查明确跳过；有完整资产的云环境必须执行严格集成检查，命令见 [OPERATIONS.md](OPERATIONS.md)。可选深度学习实验另用 `runtime/research/requirements.txt`，不自动接入活动预测。

## 规则与资料

- [赛事范围](docs/analysis-scope-18.md)：分类、阶段及90分钟口径先核实；准入不等于模型验收。
- [项目目标](docs/project-goals.md)：未来完整冻结样本的Primary Top1为最高指标，70%/80%目标尚未证明达到。
- [采集流程](docs/api-first-collection.md)与[公开来源](docs/public-data-sources.md)：来源登记、接口权限、字段覆盖、模型使用分别核验。
- [冻结](docs/analysis-freeze-policy.md)、[比分审计](docs/score-distribution-adjustment.md)与[市场规则](docs/market-and-lineup-policy.md)：缺项不补造，实际盘口单列判断，TOP3不足不强凑。
- [无人值守运行](docs/unattended-operation.md)：数据仓库Actions按固定源码运行，候选不自动晋级。
- [流程Skills及存储](docs/football-storage-and-skills.md)：GitHub保存权威版本，展示副本须有实际写入与验证回执。
- [版本管理](docs/version-management.md)与[变更记录](CHANGELOG.md)：测试、提交、推送并核验远端；历史记录追加保留。

密钥只从环境读取，禁止入库。数据库、冻结和大型工件在独立数据仓库/运行目录管理；`manifests/`只登记身份，不是完整备份。用户已接受公开数据仓库。中国体彩官方及镜像禁访，澳客允许；赔率变化不等于资金流。
