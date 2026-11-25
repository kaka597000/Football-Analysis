# 模块、路由与候选状态

[机器清单](../configs/module-portfolio.json)记录 2026 年 11 月 25 日在源码 main 与留存数据上核实的模块分工、路由快照和候选状态。目录可以同时承载共用代码与研究工具。条目的状态适用于所列模块、具体配方或批次。

| 状态 | 含义 |
|---|---|
| 活动 `active` | 现行入口或共用依赖，例如 `predict_active.py`、复盘及阶段准入 |
| 验证 `validation` | 隔离的研究工具或独立登记的验证，例如 R7 和第三比分对照 |
| 淘汰 `retired` | 已停止继续评估的具体配方，保留评估证据 |
| 归档 `archived` | 已完成的原批次、历史参数、记录或复现工具 |

采集和证据身份由 `runtime/data_sources/`、`runtime/automation/` 与 `runtime/database/` 提供。训练的输入与阶段由既有训练入口核查。分析从 `runtime/predict_active.py` 进入 `prediction_engine.py`，由 `prediction_service.py` 和诊断模块组织结果，经 `publication.py` 保存冻结。复盘沿 `runtime/review_active.py`、`v5/paired_review.py` 及各冻结协议的专用读取器执行。

旧版本仍有调用和工件依赖。v5 复用 v4 的预测逻辑，v4 又使用 v2/v3 的共用实现。活动推理还依赖 `optimization`、`optimization_tools` 与 `ml_core`。清理文件之前应分别核对源码导入、锁定工件和历史回放。

## 路由依据

| 口径 | 核查结果 | 依据 |
|---|---|---|
| 源码正式分析范围 | 20 项 | [分析范围](../configs/analysis-scope.json) |
| 数据仓常驻采集范围 | 17 项。巴甲、欧国联及日联杯没有常驻条目 | [当时的采集范围](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/e38e5ab2144d9ed8a114f68e106f0b17f6176c5d/config/scopes.json) |
| 本地登记的模型路由 | 19 条。15 条通过正式赛事范围检查，4 条供历史复现 | [路由快照与注册表指纹](../configs/module-portfolio.json) |

正式范围内缺登记路由的五项是世界杯、日联杯、欧联、欧协联和欧国联。欧冠与亚冠精英保留各自的时代和阶段键。日常调用由 [注册表解析器](../runtime/runtime_config.py) 读取实际外部注册表，并由 [预测入口](../runtime/predict_active.py) 检查工件与比赛身份。快照核查的对象是登记键，实际加载及阶段准入以对应入口的验证为准。

## 原候选批次

在数据仓固定提交上，用 #50 原评估实现重放 #32 保存的赛前治理记录。三组各完成 6/6 场的赛果认证，共用同一批六场巴甲，各组按自己的原对照独立比较。

| 原批次 | 相对各自基线的统计首选净增 | 原治理规则复算结果 |
|---|---|---|
| 三臂 | 市场 +1，市场加联赛校准 0 | 仅一个比赛日，市场臂证据不足，联赛校准无首选增量 |
| Sequence | LightGBM −1，LSTM −1 | 两臂均无首选增量 |
| Transformer | LightGBM 0，LSTM 0，Transformer 0 | 三臂均无首选增量 |

原六场首冻及赛果均可继续回放。七个候选的结果均为关闭当轮，独立复核名额为零。这里记录原研究治理规则的复算结果。正式首发及其验收仍按[项目目标](project-goals.md)和原冻结合同执行。[原治理记录](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/bb9326923ce3554e216d32ee7e9c5734cbb9c567/workspace-updates/20261118-governance/third/policy.json) · [#50 原评估源码](https://github.com/kaka597000/Football-Analysis/blob/783e02ddebd427383dbdd6a9e4b359dc68c13d8b/runtime/research_program/portfolio_lifecycle.py)

有界 LightGBM 融合使用 1,737 条已知开发记录，五个联赛都关闭这版搜权。手动 50/50 融合脚本用于历史复现。R7 的原 192 场和 #79 的第三比分影子对照仍按各自登记窗口复盘。[有界融合结果](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/bb9326923ce3554e216d32ee7e9c5734cbb9c567/workspace-updates/20261118-governance/third/README.md) · [R7](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/e38e5ab2144d9ed8a114f68e106f0b17f6176c5d/workspace-updates/20261123-prematch-literature/README.md) · [#79 预登记](https://github.com/kaka597000/Football-Analysis/pull/79)

## 离线核对

在源码仓根目录运行。第一条检查当前源码的模块覆盖及正式范围。第二条额外核对指定注册表的版本、字节指纹和路由键。

```sh
python -B -m scripts.check_module_portfolio
python -B -m scripts.check_module_portfolio --registry /path/to/active-model.json
```

如需复算上表，使用固定源码 `783e02ddebd427383dbdd6a9e4b359dc68c13d8b`、固定数据 `e38e5ab2144d9ed8a114f68e106f0b17f6176c5d` 和 #32 上链接的原 `policy.json`。在该源码根目录执行以下命令，结果输出到标准输出。

```sh
PYTHONPATH=runtime python -B -m research_program.portfolio_lifecycle \
  --data /path/to/data-checkout --policy /path/to/policy.json
```

[源码 #50](https://github.com/kaka597000/Football-Analysis/pull/50) 与 [数据 #32](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/pull/32) 保留完整的复现实验与跨仓接口迁移证据。迁移需要独立核对六个固定运行版本及原回放结果。
