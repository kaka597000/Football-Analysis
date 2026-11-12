# Elo + 凯利指数：独立研究接入及并行修复

**后续状态（2026-11-12）**：PR #32 已合并，并已完成真实历史 1,299 场 Elo/市场/凯利拟合回溯；日职恢复与真实未来双轨运行也已完成。仍没有活动 Current/Test 同场增量或严格未来效果认证。原实验已长期归档，新增 2526 固定系数核查因盘口面板不足为零覆盖。详见[后续交付](post32-delivery-20261112.md)。下文“未训练/未恢复”等表述仅记录最初提交时点，已由该报告更新。

基准源码：`a689b151f148905a8480e6a4ed0fc34575c38a3b`。
本轮新增研究模块和配置修复，不修改 Current/Test 入口、活动注册表、旧工件、旧冻结、付费权限或定时任务。

## 已实现与未实现

- 已实现 `research_program/elo_kelly_shadow.py`：按比赛时间重建的 Elo 实力特征、同截止时点的外部参考凯利指数、只写新文件的 CLI。
- 已实现 `mixture_selection_shadow.py`：独立的校准段 DC/ML 对照允许 alpha=0。它不是修改 ML-0.1 的 CONFIG，也不会使旧工件哈希失效。
- 已修复 `challengers.py`：模型持有配置副本，推理读取工件配置，分布支持显式传入；不再受进程全局 CONFIG 后续修改影响。默认数值参数不变。
- 未训练 Elo 到胜平负/比分的校准器，未训练凯利到进球率的影响系数，未改变任何活动比分矩阵。两个新增模块输出特征，不冒充已训练第三个比分模型。

## 方法边界

### Elo（埃罗）

`E_home = 1 / (1 + 10 ** (-(R_home-R_away+H)/scale))`

`R_home_new = R_home + K * (S_home - E_home)`，客队对称更新；90分钟胜/平/负的 S 分别为1、0.5、0。

E 是预期赛果得分，含平局时不是主胜概率。不能把 E、0、1-E 当作足球胜平负。
当前研究默认初始1500、K=20、scale=400、主场加成0；这些只是可复现起点，不是已校准最优参数，也不是复制 FIFA/ClubElo 的完整规则。K、主场加成、赛季衔接方式须在时间分离的开发集上确定，再锁定验证。

单次只处理明确的同 cohort/stage，不能比较独立初始化的不同联赛评级绝对值来直接预测欧战。先由项目的 `eligible_labels` 检查留存证据，再按实际抓取可用时点筛选。核心函数排除同UTC日比赛，同日全部更新基于更新前状态；未知球队保留冷启动标记。历史赛果与xG不是等同信息，Elo也不独立预测总球。

### 市场凯利指数

固定一个数据提供方、目标机构和至少两家预登记参考机构；目标机构不进入自身参考池。

1. 每家参考机构的1X2十进制赔率分别比例去水。
2. 取固定参考池的概率均值 `p_ref`。
3. 目标机构各方向指数 `K_i = O_i * p_ref_i`。
4. 同时保留目标机构返还率、相对返还率偏差、参考概率离散度、各机构原始报价时点和行哈希。

这是定价差异特征，不是 Kelly 资金管理，不输出投注比例。K不是概率，也不表示机构真实资金流；不得采用“最低凯利必出”。若以同一机构自身去水概率回乘赔率，三个方向都是相同返还率，不能作为独立信号。

缺任一家预登记参考报价就返回 NOT_READY，不事后换参考池，不补零。重复提供方不增加同一机构票数。仅支持90分钟1X2；亚洲让球/大小球含走水、半赢半输，须走完整结算函数，不能复用三项公式。`max_age_seconds` 必须由调用方提前给定，不能为通过当前比赛事后放宽。

## 如何最终影响比分（尚待训练）

保留 Poisson/DC 作为联合比分分布层；Elo是球队实力特征，凯利是市场特征，不把三者机械分配百分比。

可研究的进球率残差：`log(lambda_new) = log(lambda_base) + f_theta(Elo_gap, market_probabilities, Kelly_deviations, ...)`。

基础 lambda、Elo和市场特征都必须来自目标赛前可用数据；f_theta只用样本外基础预测构成的训练表拟合。足球平局需要单独校准三分类概率，Elo强弱不能替代总球信息。

预登记四臂同场比较：B0原模型、B1原模型+Elo、B2原模型+市场/凯利、B3两者。B2再与“同样赔率水平+市场共识但不加凯利变换”比较；凯利由赔率确定，不是新增独立情报，避免重复计权。缺市场的比赛保留全量分母及未执行原因，Elo/原模型不因凯利缺失而被伪装成同样覆盖。

主指标仍为真实唯一Primary Top1与同场净增，概率LogLoss/Brier、胜平负、实际盘口五类结算、覆盖率和校准为约束。历史开发、时点可重放、未来首版冻结分别报告。参数/候选选择不能访问留出标签，所有晋级另行审核。

## CLI

```bash
PYTHONPATH=runtime python -m research_program.elo_kelly_shadow \
  --request /absolute/research-request.json \
  --evidence-root /absolute/retained-evidence \
  --out /absolute/NEW-elo-kelly-features.json
```

请求字段：`target`（沿用现有真实fixture）、`history`（沿用replay标签格式）、`observations`（沿用V2市场观测格式）、`elo_config`（上述四个字段）、`kelly_config`（provider/bookmaker/reference_bookmakers/max_age_seconds）。CLI复用 `competition_scope.require`、`replay.eligible_labels` 和 `data_contract.validate`，不自行扩大当前赛事范围。

核心函数只核验给定字段一致性；CLI额外检查留存原件哈希和身份契约。两者都不能独立证明提供方事实真实。输出不是自动发布的推荐，不追加活动预测冻结，也不覆写旧文件。

## 上轮优化的同步状态

|工作|本轮状态|仍需完成|
|---|---|---|
|动态模型全局配置缺陷|代码已修复并有专项回归|在完整仓库CI和真实候选环境复验|
|ML允许alpha=0|新增独立选择实验，不改旧模型|真实滚动校准/留出比较后生成新版候选|
|Elo与凯利|可执行特征模块及证据适配入口|真实同场训练、校准、增量验证|
|日职依赖/球队ID|未绕过哈希，未伪造修复|恢复原源码与原件或新版本重拟合；逐队核实ID映射|
|Current/Test独立故障留存|尚未修改活动入口|恢复真实工件后设计并验收冻结兼容，不能用假工件验收|
|盘口采集状态与失败分类|本模块明确给出市场通道缺失|无人值守采集器与远端数据任务仍需实际请求/配额/时点验收|
|固定配方刷新/1–1集中/xG极端外推|保留旧实验，不回填结果|新版本预登记稳健特征实验和完整未来配对|

## 验证范围

本地27项 Elo/凯利/混合选择器测试通过。另6项配置测试用当前源码AST隔离加载通过：仅省略三个项目依赖import，fit测试显式mock输入prepare边界，因此不是整仓集成。

本地网络无法解析 github.com，未克隆完整源码/运行资产；没有声称完整套件、生产模型加载、真实Elo训练或真实盘口回测通过。原challengers文件的Git blob SHA已核对为 `e896e8d42611bd0df9a72cc4b880b5ecca854e20` 后才生成补丁。远端CI以真实回执为准。

## 定义参考

- FIFA Elo/SUM 方法：https://inside.fifa.com/fifa-rankings/world-ranking/procedure-men
- 市场页面对凯利指数的计算定义：https://op1.titan007.com/Oddslist/2997089_2.htm （只采用计算定义，不采用其未验证的赛果推断）
- 原始Kelly研究：https://www.nokia.com/bell-labs/publications-and-media/publications/a-new-interpretation-of-information-rate/
