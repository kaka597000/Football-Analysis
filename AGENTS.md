**2026-10-15最新用户授权：后续所有分析默认冻结。** 发布前保存；当前/test独立并行，综合判断另轨，更新追加不覆盖，赛前/滚球分开。首次赛前版本为默认逐轨主评价版本，后续版本单列；不追认历史、不择优。完整规则见 /workspace/Football-Analysis/docs/analysis-freeze-policy.md。此条优先于下文旧的研究不冻结表述；冻结不等于模型晋级。

# WCPP 项目工作规则

- **2026-10-30 FMA执行**：用户授权实施PRD适配，并明确需要更多智能体和分钟级模拟。赛事范围沿用现项目，数据沿用现有免费来源与实际免费权限，订阅后另评估；不以log-loss/RPS替代Primary Top1。已建立同矩阵joint/layered独立实验、五角色有界审查框架、独立分钟模拟及可配置共享免费额度账本。默认预测附selection_experiment元数据，原Primary和矩阵保持；角色无caller标NOT_RUN，分钟状态参数缺合格训练时禁用，不能将mock/基础泊松模拟说成已训练AI增益。进度与限制见docs/fma-implementation-20261030.md。不新建仓库/定时任务。

- **2026-10-30项目长期目标**：持续优化模型代码，深入研究并验证足球分析方法，提高未来真实冻结样本的第一推荐精确比分、胜平负和实际盘口大小球命中率。Primary Top1是最高指标；70%/80%为尚未证明达到的目标。后续任务遵守[项目目标](docs/project-goals.md)及project-goals.json，按“采集核验→冻结→复盘诊断→独立实验→时间隔离验证→保留/切换/回滚→版本同步”执行。本次目标登记不代表启动后台任务或完成训练。

- **2026-10-29采集与版本管理**：默认批量入口为 `python runtime/data_sources/cli.py --plan PLAN.json --cache-dir /workspace/WCPP/cache/api-first --out NEW.json`。先读适用接口/已有缓存，独立服务并行、同服务限速，按赛事/日期复用请求；API阶段结束后只把实际未补齐字段交给gpt-6-luna查公开网页。ESPN/FotMob公共JSON计入接口阶段；首发历史列表、空伤停、陈旧盘口不算已补齐。完整流程见docs/api-first-collection.md。完成一次可交付变更即测试、提交、推送并核验远端HEAD，按docs/version-management.md执行；不新建定时任务。此条替代先大范围网页搜索的旧流程。

- **2026-10-19输出要求**：每批分析结尾列最推荐TOP3赛事，含胜平负、实际盘口大小球及唯一综合第一比分、排序理由；当前/test继续独立，名单和排序随分析赛前冻结，复盘单列且不替代全批统计。不足三场不强凑。完整规范见 docs/user-primary-score-requirements.md 的TOP3条款。

每次足球预测或复盘先读 [用户核心需求](docs/user-primary-score-requirements.md) 和 `/workspace/WCPP/AGENTS.md`；需要模型推理时再读取 `/workspace/WCPP/runtime/active-model.json`。

- 中文，用户回复首句 HELLO BOSS，所有用户可见时间使用北京时间 UTC+8。
- 用户要的是综合多方证据与比赛路径后选出的唯一第一推荐比分。统计矩阵最大值、市场最大概率和标签改名不能替代这一交付。关键证据不足时明示待定，不补造。
- 当前模型/test模型独立保存，统计基准与综合人工研究另列；保留版本、原矩阵、候选比较、依据、反证及缺项。人工选择不冒充已训练模块或重新校准概率，不混入原模型业绩。
- 数据收集、采集和查询按用户授权交给 gpt-6-luna；主代理核验来源、球队身份、阶段、时间与盘口并负责综合判断。中国体育彩票官网及镜像禁访，澳客允许。密钥仅从环境读取，不能写入日志或文件。
- 准确率目标指未来赛前完整样本的实际命中率：第一比分70%以上，胜平负/实际盘口大小球80%以上。尚未达到；唯一正式最高KPI为Primary Top1。
- 不覆盖旧预测、不改原Champion/永久λ、不追认正式冻结、不赛后挑选较好的模型或样本。

- **2026-10-15来源使用要求**：后续分析必须实际核查近期xG/xGA及最新伤停/停赛；按覆盖使用Understat、Transfermarkt、StatsBomb开放数据及已授权备用源。逐场保存来源/时间/窗口/缺项及是否进入判断或数值模型，禁止仅登记网站就称已使用。执行细则见docs/user-primary-score-requirements.md的2026-10-15补充。

- **2026-10-20执行约束**：分析前读 `/workspace/Football-Analysis/docs/score-distribution-adjustment.md`。默认入口已附单队/总球审计；综合冻结必须引用实际双模型冻结并填写比分候选、零球vs一球反证及实盘大小球独立理由。缺比较不进入新TOP3，但全批仍如实冻结；不强制加球。校准C1/C2未过门槛，仅M2独立研究，当前/test未晋级。

- **2026-10-21复盘约定**：比分、胜平负与大小球分母独立；比分待定但已明确方向仍结算方向，观望和候选不得补命中。test欧冠/英冠版本分列。乌龙球员队与受益队使用 `runtime/research/review_events.py` 校验分数变化及shotmap球队ID，不能直接取isHome；新增8测试及真实42球验证通过。复盘证据见 `/workspace/WCPP/reports/review-20261021-0813/赛事复盘与调整决定.md`；本次数值参数不变，未宣称训练或晋级。
