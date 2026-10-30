# FMA隔离研究第一阶段交付

## 验证结果

本云端18个测试目录共290项测试通过、0跳过；含分层选择与旧冻结保护、默认双模型输出不变、五角色契约、分钟模拟边界，以及13项共享额度测试（含多进程竞争与策略冲突）。源码和凭据检查无匹配发现，diff格式检查通过。
测试日志保存在`/workspace/WCPP/reports/fma-selector-20261030/validation-tests.txt`，SHA256为`c88090d68b5398bdebc5b9c0f7873674170693b3f99fac8acdb3fa48a8e80e43`。
来源工件分钟模拟实测20000次：模拟主客均值1.70925/1.3577，解析值1.71066/1.35533。此为模型能力参考，不对应本场预测或新增冻结，不证明精度提升。五角色测试使用mock，未将其算作真实LLM运行。

2026-10-30，用户授权执行适配建议，并明确要求更多智能体与分钟级模拟。赛事范围及免费来源继承现项目；不新增订阅、仓库、定时任务或UI。

## 已实现

1. `runtime/research/layered_selector.py`及`layered_policy.json`：在同一真实矩阵上并列输出joint_top1和layered_pick。固定方向优先级、0–1/2–3/4+区间、并列处理与警告阈值。无法确认的overflow、非法矩阵、来源模型弃权均拒绝，不补概率。
2. 活动入口追加`selection_experiment`。当前模型/test原矩阵、Primary、融合参数保持，原综合判断另轨。未来通过原CLI生成的配对冻结将同时保存实验元数据；没有新建第三个默认模型。
3. `layered_replay.py`核验旧冻结哈希，只用首次赛前版本，保留模型版本/队列分组、缺赛果和拒绝记录；报告改错为对、改对为错、净增和分歧。重建不是追认历史分层预测，也不重新写旧冻结。
4. `evidence_agents.py`提供身份数据、状态/xG、阵容赛程战意、市场、反方审计五角色。独立首轮同包哈希、最多两轮与十次调用、仅引用已有证据。没有实际caller就NOT_RUN；本轮软件mock测试不是已运行真实五角色足球分析。
5. `minute_simulator.py`提供有来源工件与seed的分钟网格研究。基础模式是原泊松率的模拟参考，不增加新预测信息；输出模型内模拟频率和解析均值对照。滚球须明确红牌状态；未经训练的红牌效应拒绝，不能造未来事件。状态参数工件需覆盖/训练/时间验证证据，当前没有生成此类真实工件。
6. `data_sources/quota.py`用SQLite事务跨进程预留额度，追加账本、别名共用账号、限速与429熔断。已接API-first：cache/dedup之后才预留，显式配置quota_policies和共享--quota-ledger时生效；未配置时明确NOT_CONFIGURED_LEGACY_LIMITS_ONLY，不宣称全部历史脚本已迁移。配置中limit未知则阻止该服务回源，不默认猜500credits。

## 实际历史选择器对照

来源：`/workspace/WCPP/forecasts/issued-analyses`只读，赛果取既有`review-20261029-0839/normalized-results.json`。本次重建索引180场首次赛前配对记录；输入15条合格90分钟赛果，但仅9场同时具备可比较的当前/test矩阵及匹配赛果。其余矩阵弃权/无匹配赛果保留，不能称180场全部评估。
逐模型版本与赛事分组报告在`/workspace/WCPP/reports/fma-selector-20261030/replay.json`；亚冠精英2场中两模型joint各命中1场，layered均0；意甲5场test的joint命中1场、layered为0。其余本批共同样本均未增加命中。
因此该小批历史没有支持替换原选分器，存在把原本正确比分改错的例子；不宣布统计显著，也不据此重新挑区间或反复试阈值。结果不足以否定所有未来应用，但默认Primary继续保留。

## 分钟事件真实覆盖审计

Luna只读审计：统一库33项赛事、46,190条比赛、42,765条training_eligible记录；没有事件事实表，observations.first_known_at全部空。部分比赛级红牌总数不是红牌分钟。零散赛后事件原件可做局部研究，但全量分钟覆盖分母不可得，不能造覆盖率或说状态转移训练已完成。
证据：`/workspace/WCPP/reports/fma-event-coverage-20261030/audit.json`，包含SQL、分组计数、样例原件哈希。分钟模拟不因达到某个软件测试门槛就自动晋级；默认覆盖阈值只是研究门禁起点，不是统计充分性保证。

## 命令与后续执行

```bash
python runtime/research/layered_replay.py --root /workspace/WCPP/forecasts/issued-analyses \
  --results RESULTS.json --out NEW-REPLAY.json
python runtime/research/minute_simulator.py SOURCE-BACKED-INPUT.json --out NEW-SIMULATION.json
python runtime/data_sources/cli.py --plan FREE-BUDGET-PLAN.json \
  --cache-dir /workspace/WCPP/cache/api-first --quota-ledger /workspace/WCPP/runtime/free-quota.sqlite \
  --out NEW-COLLECTION.json
```

上述输入必须真实核验，不代表仓库已有完整生产配置。原输出追加实验元数据会改变新文件哈希，但不改既有文件或矩阵数值。回滚可通过revert本次源码，旧工件无需重训；保留已产生的新研究记录。
下一阶段优先补事件事实/首知时点、真实免费额度配置及宿主caller，再逐项验证市场反演、融合、离散度和情景参数。没有数据的模块保留NOT_TRAINED；不能一次接通所有层后把变化冒称命中率提升。
