# 天皇杯数据库扩展

独立SQLite扩展库：`data/emperor-cup/WCPP_天皇杯扩展库.sqlite`。不修改9月29日Canonical主库，不写入正式预测、冻结或500+200表。

## 数据规则

- 来源为JFA官方比赛页及官方记录PDF链接；原HTML按内容哈希保存，查询时间如实登记，历史首次获知时间未知。
- 2024/2025各87场；2026已公布72场。73至87号页面暂无完整日期或对手，存入scheduled_slots，不能冒充具体对阵。
- 只在官方最终记录链接存在、前后半场都有比分且显示总分一致时导出90分钟比分；另设开赛至少两小时的保守防误判门槛。
- 90分钟=前半+后半。加时前后半场、点球决胜、展示最终比分分列。进行中缺少后半场比分是正常状态，不填零，不当成终场。
- 双方各11名球员并有替补分栏时标记首发；其他名单标为registered，不冒充首发。
- first/second是JFA列示顺序，非自动确认主客场。真实场地保存，主场归属仍需核验，不能直接给列示第一队主场优势。
- 球队名称采用NFKC规范化日文原名；更名或不同来源别名不自动合并。
- 已核验90分钟样本导出到verified-90-staging.json。它仍待球队别名、场地与赛事分层审核，不直接混入日职、其他杯赛或国家队训练。

## 使用

Python标准库即可，无新增依赖或后台服务。

```bash
cd runtime/emperor_cup
python -m unittest discover -s . -p 'test_*.py' -v
python ingest.py --years 2024 2025 2026 --workers 4
```

只刷新2026今天十场：

```bash
python ingest.py --years 2026 --matches 58 59 61 62 64 66 67 68 70 72 --workers 4
```

只刷新赛后某一场，将编号传给--matches。相同比赛使用稳定主键，快照可重复采集但比赛不会重复增加；原赛果和查询记录保留。结束后输出审计和最新90分钟staging。

解析规则修正后的离线重解析：

```bash
python ingest.py --repair-only
```

原隔离记录及修复登记均保留；重解析使用原始快照真实查询时间，不伪造新查询。raw目录包含首次探测样本，正式数据库引用带内容哈希的快照文件。

## 表

- editions、teams、matches：届次、日文球队身份、具体对阵与场地。
- snapshots、observations：来源、L1等级、查询时间、哈希及原始解析。
- result_observations：90分钟、加时、点球与官方最终记录链接。
- roster_observations：首发、替补、注册名单。
- scheduled_slots：日期/对手未完整公布的未来槽位。
- fetch_log、quarantine、quarantine_resolutions：失败入口和解析修复审计。
- training_90视图：当前最新快照有正式90分钟记录的比赛。

禁止追认赛前冻结。数据库补充本身不升级Champion，也不证明模型准确率改善。
