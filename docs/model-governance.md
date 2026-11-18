# 模块治理与模型收敛（2026-11-18）

机器可读入口为 [module-portfolio.json](../configs/module-portfolio.json)。它是源码用途和研究组合清单，不替代活动注册表；`active` 表示当前执行依赖，绝不等于正式验收。19 条路由保存既有 Current 与 test 的工件身份，保留其实际阶段路由键，不把普通联赛模型套到杯赛或其他阶段。外部活动工件仍以运行目录注册表及实测哈希为准。

## 目录责任

- `data_sources/` 与 `automation/collector*`：采集、来源、原始响应和赛前时点；数据落在数据仓。
- `research_program/`：训练、历史开发比较、被冻结的前向研究及复盘。`automation/run.py train` 只产生候选。
- `prediction_engine.py` → `prediction_service.py` → `prediction_diagnostics.py`：当前分析；`publication.py` 负责交付与冻结。
- `v5/paired_review.py` 和各实验 reviewer：按原首次冻结、版本、比赛身份结算；不重新预测旧比赛。
- `models/` 和 `docs/archive/`：逻辑归档，保留原路径。`v2` 至 `v6`、`optimization` 中仍有活动调用或冻结依赖，不按版本号删除。

`active / validation / retired / archived` 分别代表活动依赖、验证中、已有证据淘汰但保留、历史归档。此次没有凭目录旧或开发成绩差把模型标为 retired。目录可以兼具共享基础与候选用途；模块状态不是其中每个模型的验收结论。

## 有限组合

每个赛事阶段只登记一个当前入口、一个固定基线和最多一个整合候选。已有活动 test 固定保留；不因治理文档切换线上对照。巴甲 regular 的整合优先项复用现有 sequence 实验的盘口基线 + LightGBM 状态修正，其上游来自 Current；联赛校准作为默认关闭的可选组件。这个名称是收敛方向，不是新工件，不创建另一组预测。

三臂、sequence、Transformer 是已冻结例外：完成原方案后再据证据保留或淘汰，不停掉 LSTM/Transformer 的未完成实验。其他阶段尚未登记整合候选，不自动复制巴甲方案。

同场、同赛事阶段、同赛前截点、同市场证据才可直接比较。Primary Top1 优先，同时报告比分 log loss、WDL Brier、校准、实际盘口结果、覆盖与不确定性。开发样本只能辅助选择下一项研究；任何新配方需重新登记未来验证，不能复用已看过的历史作为晋级验收。无自动晋级。

## 跨仓接口和兼容期

公开接口 `automation.research_api` v1 提供 `ScoreboardQueue`、`read_predictions`、`read_results`、`certify_result` 及原采集辅助函数。它们是原实现的直接别名，签名、异常、队列持久化和数学保持不变。

数据仓 `runtime/source_review_api.py` 是唯一旧私有调用适配层；有 v1 时使用公开接口，当前 `3fd411a…` 冻结版本没有该模块时走显式 legacy 路径。不吞掉实现的导入错误，不猜测新版本协议。`poll_three_arm_results.py` 只消费公开命名。移除 legacy 层需要单独迁移运行 pin 并重放冻结；本次不更新 pin、不安装部署。

`install_forward_overlay.py` 暂保留：它在覆盖前后都核查协议锁定的源码。消除 overlay 需要分别解除国内联赛、欧冠协议的依赖，不能把 main 源码整体覆盖到旧推理版本上。

## 验证与回滚

```sh
python runtime/module_governance.py
python runtime/module_governance.py --data /path/to/kaka597000-Football-Analysis-Data
PYTHONPATH=runtime python -m research_program.consolidation_audit --data /path/to/data
python scripts/check_repository.py
python scripts/run_tests.py --layer integration
```

治理检查在源码 CI 运行，检查未分类模块、新增阶段组合超额、重复阶段和冻结例外被提前退休。跨仓检查另校验数据仓 `config/source-contract.json` 中的原工作流、冻结、登记与工件哈希。合同是本次整理基线，不是永久禁止工作流更新；以后迁移须同时更新合同和实际重放证据。

源码、数据清单、活动工件身份和部署版本各自记录。回滚只需 revert 对应整理提交；没有迁移数据或修改模型权重。新增 API 与旧调用并存，因此数据仓和源码仓可以分别回滚。

## 第二批：版本执行入口收敛

数据仓五个运行/回放工作流改为通过 `runtime/source_versions.py` 读取同一份 `config/source-contract.json`，不再各自硬编码源码版本。训练、采集、sequence 和 Transformer 仍锁原提交；三臂 reviewer 的草稿迁移指向公开 API v1 的 `8534d90…`。该版本相对原 `3fd411a…` 只有新增 runtime 文件，原 runtime 文件 Git blob 全部一致；旧适配层只为回滚保留。这里描述 PR 中待合并的配置，不表示部署已改变。

源仓旧部署密钥安装器不再生成密钥、修改 Secrets 或触发任务；原内容与重复执行工作流逐字节归档。原运行注册表中范围外路由改标为清单中的 archived，并由 `competition_scope.permitted` 校验；外部注册表、参数和原模型保持。

后续可执行迁移：国内联赛/欧冠 overlay 须分别从 `future/bundle-v2/manifest.json` 与 `future/ucl-bundle-v2/manifest.json` 提取锁定源码集合，在独立目录生成无覆盖版本并逐场重放，原集合与新收据一并保存后才改 pin。LSTM/Transformer 须等待已登记六场原赛果，按原协议比较同截点净增命中、概率质量和覆盖，再记录保留或淘汰；目前没有可删除这些实验入口的证据。历史回放专用工作流仍保留原历史提交，不强行统一到最新源码。
