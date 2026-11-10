# 欧国联独立数据库

2026-11-10按用户授权新增成年男子 UEFA Nations League（ESPN `uefa.nations`，赛事 ID 2395）。Luna 负责采集，主代理核验并交付数据库。正式范围扩至19项；当前/test活动模型保持原状，欧国联暂缺通过验收的活动模型。

## 数据与来源

SQLite独立存放在 `/workspace/WCPP/data/nations-league/`；原统一赛事库只读。源文件、查询时间、SHA256及导入输入随数据快照保存到数据仓库，不进入源码仓库。历史原文在本次取得，不可将取得时间追认为赛前 first-known，历史 as-of 回测未认证。

表包括 `teams`、`events`、`source_records`、`observations`、`import_runs`、`quarantine`。来源与观察记录追加保存，触发器拒绝更新或删除；`events`是当前可信观察的投影。相同采集幂等，冲突记录隔离；库存可经后续原文升级，但旧响应不覆盖新观察。导入事务及外键检查保证失败不留下半条事件。

同一来源的全量清单与逐场摘要只能用于内部交叉核对，不冒称独立来源验证。覆盖状态在完成独立赛事清单对照前保持 `PARTIAL_UNTIL_INDEPENDENT_INVENTORY_VERIFICATION`。

## 阶段与比分规则

- 等级A/B/C/D及跨级附加赛单列；联赛阶段、八强、半决赛、决赛、季军赛和升降级附加赛分开。历史 `group-stage`归一为 `league-phase`。
- 阶段和等级须在实际原文中找到依据；缺失等级不推定A，未知中立场保持NULL，缺项记录仍可入库存。
- 比赛摘要缺少等级时，可使用同场赛程原文补证，但须同时核验两份文件的哈希、赛事、球队、开球时间与阶段一致性，保留两份来源时间。赛程补证不能替代摘要的90分钟赛果核验。
- 总比分、90分钟、加时和点球字段分别存储。待赛0–0不当完赛比分；状态及分段得分不足以证明90分钟结果时，不进入训练导出。
- 训练资格由原文重新计算，输入中的 `training_eligible`不能授予资格。数据库准入只证明结果与阶段可用于后续研究，不代表已完成训练或赛前回测。
- 导出必须显式选定一个等级和阶段，禁止与俱乐部或其他国家队赛事混训。未验证比赛保留在库存，可用 `--inventory`单独导出。

## 使用

在仓库根目录执行（已有数据库不会被 `build`覆盖）：

```bash
python -m runtime.nations_league.database build --db /path/to/new.sqlite
python -m runtime.nations_league.database import --db /path/to/new.sqlite --input /path/to/fixtures.jsonl --raw-root /path/to/staging
python -m runtime.nations_league.database import --db /path/to/new.sqlite --input /path/to/summary-captures.jsonl --raw-root /path/to/staging
python -m runtime.nations_league.database stats --db /path/to/new.sqlite
python -m runtime.nations_league.database export --db /path/to/new.sqlite --out /path/to/new-export.json --stage league-phase --league-level A
```

导出文件拒绝覆盖；后续刷新追加导入新capture，保留原来源时间和原始字节。本次初始回填由任务执行，不代表已部署欧国联定时刷新。无新定时任务、无活动模型晋级、无自动赛事预测。
