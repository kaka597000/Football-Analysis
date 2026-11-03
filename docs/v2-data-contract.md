# V2阶段二：来源与时点数据准入

2026-11-02新增独立研究入口，不修改当前/test路由、矩阵、权重或活动注册表。阶段二尚未整体完成；本轮完成的是可执行的数据入口和真实响应验收，不是动态融合、球员影响模型或收益证明。

## 可运行入口

`runtime/v2_test/ingest_fotmob.py`从保留的FotMob响应及人工核准的capture清单导入xG/xGA。清单登记相对文件路径、实际文件SHA256、原始GET地址/抓取时间、预期比赛/双队/联赛ID及明确cohort。导入检查ID、成年男子、FT、无加时/点球/判胜和完整场统计；缺值不补零。xGA仅取同提供方、同比赛对手xG。杯赛、欧战与各国内联赛分别登记，不自动混入同一个训练队列。

有完整shotmap时，将逐射门xG合计与展示球队xG比较，允许0.02的显示差。超出该保守阈值的响应进入隔离清单，不挑一个数值当真值。差异可能来自提供方定义、修订或数据问题，隔离不等于已证明数据错误；保留原文件、清单下标及全部分母，等待口径核实。shotmap缺失/不完整会明确标注，不能称完成一致性核查。

```bash
python runtime/v2_test/ingest_fotmob.py \
  --captures /absolute/reviewed-captures.json \
  --evidence-root /absolute/retained-evidence \
  --out /absolute/NEW-import-result.json
```

清单元素示意（软件格式，不是真实比赛）：

```json
{
  "file": "raw/SYNTHETIC_ONLY.json",
  "sha256": "ACTUAL_FILE_SHA256",
  "url": "https://example.test/football",
  "query_time": "2026-11-02T10:00:00+00:00",
  "expected_match_id": 1,
  "expected_home_id": 2,
  "expected_away_id": 3,
  "expected_league_id": 4,
  "cohort": "eng.1",
  "identity_verified": true
}
```

`runtime/v2_test/data_contract.py`验证保留的源文件哈希及路径边界，建立某个真实未来比赛、某个截止时点的特征快照。observation字段支持xG/xGA/npxG、射门、球员分钟、人员可用性与市场报价；本轮真实导入只覆盖xG/xGA，另用现有一份真实报价验证时效拒绝。字段契约存在不等于已有全部球员、npxG或盘口历史训练数据。

```bash
python runtime/v2_test/data_contract.py \
  --observations /absolute/observations.json \
  --target /absolute/target.json \
  --evidence-root /absolute/retained-evidence \
  --provider FotMob --out /absolute/NEW-feature-snapshot.json
```

target包含`match_id/cohort/home_id/away_id/kickoff_at/cutoff_at`，并可附明确的`provider_match_ids`别名。球队标识映射须有身份核验，不猜名字或修改活动模型ID。observation包含`provider/cohort/match_id/entity_id/field/value/period/event_status/event_kickoff_at/observed_at/evidence`；xG/xGA另需`entity_side`，xGA附同源对手xG的`derived_from`。evidence登记`url/query_time/file/sha256/identity_verified/source_tier`。

## 时点、覆盖与拒绝规则

- 实际抓取时间与evidence.query_time一致，文件存在且SHA256匹配；文件须在evidence-root内。检查软件输入与保留证据，不保证提供方事实正确或人工审核无误。
- 导入器始终保留`first_known_at=null`、`source_published_at=null`。没有历史首次可知时间就不补造。手填更早的first_known_at不能将今天下载的数据送入昨天的快照：最早可用时间仍受实际capture限制。
- 只选截止前已捕获且在窗口内的历史比赛，不使用目标比赛自身赛后数据。历史完整场统计要求FT且至少在开球90分钟后抓取。只保留截止前最新修订；同一时点矛盾记录拒绝，完全重复也拒绝，不重复增加样本量。
- 每次快照显式指定提供方和cohort，其他来源/联赛的排除量保留。默认最近3场、90日窗口，缺几场就报告实际n；球员分钟/可用性保留逐人记录，不把未知系数当已训练。
- 市场报价必须有源quoted_at、机构、市场、明确盘线和完整两侧或三侧十进制赔率；不同机构/盘线分开保留。默认报价最多60秒，人员资料最多6小时，策略随快照保存。跨提供方建立独立快照，不把多个转引源当独立事实。
- L2平台“不可用”不能提升为官方确认缺阵。`confirmed_out`需经审阅的L1证据；无首发不默认阻断模型，重大风险仍单列。
- 密钥字段、带凭据URL和认证文本拒绝写入；程序不发网络请求、不改源文件，输出必须使用新路径。

## 本轮真实验收与限制

私有报告`/workspace/WCPP/reports/v2-data-contract-20261102/`：输入43份真实保留响应，32份通过本轮规则，形成128条双侧xG/xGA观测；11份因展示xG/shotmap差异被保守隔离。包括新英格兰—迈阿密明显差异，也包括较小差异，不能把11份都宣称为提供方错误。7场未来比赛特征快照实际生成，当前历史每队合格联赛n为1–3而非强凑3。

真实旧Pinnacle报价在60秒规则下被排除；真实晚抓历史在更早cutoff下不能进入快照。这是实际输入/行为验证，不是模型绩效回测。23项新软件测试覆盖未来/缺失时间、哈希篡改、重复和矛盾修订、来源/联赛混合、xGA身份、报价时效、非官方伤停、缺xG、加时和shotmap冲突。

没有训练动态融合、球员影响、独立市场概率或新校准，没有认证旧历史as-of无泄漏，没有自动晋级。下一阶段需解决隔离数据口径、积累同节点真实前瞻基础预测/赛果，再作分联赛时间隔离拟合及消融；不得把本轮工具测试当样本外收益。
