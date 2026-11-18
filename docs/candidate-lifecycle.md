# 有限融合开发与候选闭环

本批复用 `score_mixture_shadow` 的固定权重网格、择优及一次门控逻辑，并复用已有市场基线、LightGBM 工件、比分选择与结算函数。没有另训练一个模型，没有给 `fuse_models.py` 的未训练 50/50 参考冒领收益。

`research_program.bounded_fusion` 使用此前已研究过的欧洲 2025/26 赛季1,737条合格记录。组件训练截止2025-08-01；2026-01-01之前选权、1月至3月只对所选配方门控、4月起只诊断。各联赛分开，固定比较权重0/.25/.5/.75/1；矩阵、市场及行身份一致，绝不在门控失败后尝试次优。数据缺原始报价时刻，因此只是同一历史代理输入的开发诊断，不认证真实赛前截点。全量覆盖分母为已准备的1,737条研究输入，不代表整个赛事日历。

实际结论：英超和法甲门控退回基线，西甲选基线。德甲选0.75、意甲选1.0，但后段分别只有8/63、7/80个统计第一比分命中，基线为9/63、8/80。这一轮五个联赛均没有后段优势，标为关闭的开发搜索，不创建新长期前向对照，不继续选次优救结果。所选权重仍原样记录，诊断不反过来调权。统计首选不替代用户实际发布的综合第一比分。

## 原前向实验的闭环

`research_program.portfolio_lifecycle` 复用原三臂、sequence、Transformer 的冻结读取、身份检查与认证赛果；各组各自同场、同截点评价，不跨组池化。新增生命周期规则已在原六场开赛前通过 `analysis_freeze.save` 单独首冻，绑定三份原计划、原候选集合、实现哈希与关闭条件；没有重置或修改三组原实验。

- 任何待结算比赛：继续原验证，不提前退休。
- 完整结算但缺配对覆盖、没有Top1净增、概率质量退化：关闭这轮候选，不自动再报名。
- 正净增但少于两个比赛日或配对检验未达按候选数校正的阈值：关闭为证据不足，不把小样本成功包装为推广依据。
- 满足条件：同一原实验最多占一个独立复核名额，仍保留现主线。必须另经既有 `promotion_review.assess` 及独立审查，不能据本报告切注册表。

这些是下一轮候选管理建议，不是修改原实验验收条款；原试验记录、原复盘和全部失败证据保留。当前三组都仍是 `PENDING_ORIGINAL_VALIDATION`。新合入的市场信号和欧国联Elo保留原登记、监测与复盘设施，未因本批而中断。

## 源码身份与导入边界

新研究模块使用包相对导入，不再自行修改 `sys.path` 或导入预测/采集CLI以取得数学函数。旧 v5/v4/v2/v3、optimization 和 optimization_tools 的活动及哈希绑定边界仍保留；数据交付的 `import-boundaries.json` 记录静态引用，不冒充穷尽动态依赖。

最新主分支给 `three_arm_model.py` 增加排序函数，虽不改变原数学，字节哈希已经不同。旧冻结回放必须读取合同中的 `frozen_replay_revision`（8534d90…），与最新代码回归分开。不能修改原预期哈希来使回放通过。新加入的市场信号运行版本也纳入同一版本合同，原提交保持。

## 复现

在源码根目录，使用已有依赖：

```sh
PYTHONPATH=runtime python -m research_program.bounded_fusion --data /path/to/data --out /tmp/new-development
PYTHONPATH=runtime python -m research_program.portfolio_lifecycle --data /path/to/data --policy /path/to/data/workspace-updates/20261118-governance/third/policy.json
python scripts/run_tests.py --layer integration
python runtime/module_governance.py --data /path/to/data
```

输出目录必须是新路径。开发报告及逐场记录可字节重放；未来复盘随真实结果更新，不能要求永远等于首次“待结算”快照。未来优势仍未证明，正式权重、冻结、工件、部署与自动晋级均未改变。
