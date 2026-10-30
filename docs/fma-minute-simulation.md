# FMA分钟级模拟研究组件

2026-10-30用户要求纳入分钟级模拟。实现位于`runtime/research/minute_simulator.py`，独立于当前/test正式执行入口；未修改任一活动工件、原概率矩阵或Primary。

## 已实现

- 从已保存且校验SHA256的模型输出JSON读取主客预期进球率，不接受LLM手填的无来源λ。
- 按一分钟网格及最后不足一分钟的实际步长，模拟剩余常规时间的进球增量。固定seed和draws可复现。
- 保存分钟均值轨迹、最终比分频率、主平客频率和解析独立泊松均值对照；所有结果标为未实测校准的研究频率，不称真实“剧本概率”。
- 支持实际已观测比分与事件边界检查。事件不能晚于当前分钟或as_of；常规上下半场与加时/点球大战隔离。乌龙记录必须明确进球获益队。
- 无状态参数时，领先收缩、追分、红牌乘数均不凭空设置。已知红牌会明确拒绝模拟，标记红牌状态引擎未训练，避免默默按11对11继续给出结果。
- 可加载另行拟合的比分状态hazard参数，要求同赛事队列、训练截止/工件时间、事件覆盖门槛及可核验训练manifest和时间验证报告。覆盖门槛本身不证明预测效果。

当前没有生成任何真实训练的状态参数工件；默认是**无状态调制的Poisson分钟参考**。它只把已有进球率展开到时间网格，没有凭空产生额外预测信息。

## 输入契约

```text
artifact.schema = WCPP-MINUTE-RATES-1
artifact.cohort = 现项目实际赛事/赛制队列
artifact.score_basis = regulation_90_including_stoppage
artifact.trained_through / created_at = 带时区的实际可用时间
artifact.source = {path, sha256, rates_pointer, observed_at}
```

`rates_pointer`是保存JSON中真实主客进球均值数组的JSON Pointer，例如`/test_model/expected_goals`，必须实际存在，不能把历史xG当成本场拟合λ。源文件本身的时间/身份仍须由主代理核验；一个匹配的哈希不证明数据天然可信。

`state`含`minute`、`score=[主,客]`、`clock_basis=regulation_elapsed_minutes`、`observed_at`及可选`events`。完整事件表须能重建当前比分，局部事件不能超过当前进球数。事件种类仅支持`goal`和`red_card`；红牌不自动触发任意系数。

滚球状态（`minute>0`）必须显式提供`red_cards=[主队累计红牌,客队累计红牌]`，缺失或未知时拒绝模拟，不能把局部事件列表中没出现红牌当作11对11。已观测红牌事件不得超过声明计数；`events_complete=true`时必须逐队完全一致。红牌事件使用`carded_side`标明受罚队。即使事件列表不完整，只要声明任一队已有红牌，当前未训练红牌引擎就明确拒绝；只有赛前minute=0且比分0-0、无已发生事件时可省略红牌字段。

`regulation_minutes`是明确的90分钟加补时**情景时长**（默认96），不是程序预测了裁判补时。它必须包含常规补时、排除加时与点球。原始比赛钟需要先转换到累积常规比赛分钟，第一节补时与第二节比赛钟不能直接拼接；未经转换的事件不应进入本组件。

状态参数工件须为`WCPP-MINUTE-STATE-HAZARDS-1`，有`FITTED_RESEARCH`状态、同cohort、完整比分状态乘数、事件覆盖数据以及训练/验证证据引用。默认实验覆盖门槛为至少200场、95%事件完整率；这是待实测修订并冻结的初始研究门槛，不是行业准确率标准，也不是新增模型的晋级门槛。

## 运行与验证

```bash
python runtime/research/minute_simulator.py actual-source-backed-input.json --out new-minute-research.json
PYTHONPATH=runtime python -m unittest discover -s runtime/research -p test_minute_simulator.py -v
```

CLI拒绝覆盖输出。实际发布前应通过现有追加冻结流程保存，并标明赛前或滚球；本组件不自行发布、下注、调度或把历史重放追认为前瞻冻结。

单元测试只使用明确标识的合成资料，验证模拟对解析Poisson的数值误差、seed复现、事件越界、源文件哈希、状态覆盖门槛及红牌未训练拒绝，不是足球准确率证据。

## 后续训练所需

先从现有免费来源核查逐场进球时间、红牌、比分变化、赛制和90分钟口径，按赛事/赛季报告完整率。仅完整率过门槛仍不足：必须按时间隔离拟合状态hazard，比较同一源λ下“无状态调制”和“已拟合状态调制”的同场Top1、总进球误差、分布损失及校准。
红牌、追分、领先保护和补时长度分别做消融；没有稳定样本外增益就保留基础参考或停用相应状态。禁止因单场结果调整系数，禁止在赛前输入真实未来红牌或未来进球。
