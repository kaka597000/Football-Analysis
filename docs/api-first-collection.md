# 默认接口优先采集流程

2026-10-29起执行。先定位比赛身份、北京时间开球及赛制，再按字段列所需数据。
先复用仍有效的历史/来源缓存，调用适用API，最后仅为真实缺项安排Luna公开网页查询。
ESPN、FotMob公共JSON属于接口阶段；有apikey的接口不是唯一API。

```bash
python runtime/data_sources/cli.py --plan batch-plan.json \
  --cache-dir /workspace/WCPP/cache/api-first --out /workspace/WCPP/staging/本批次-api.json
```

批计划由主代理或Luna在核准赛事ID后构造，不把未知ID拼成看似真实的请求。
同一接口/参数只取一次，同日赛程、同联赛赔率按批复用；独立服务可并行，同服务串行。
API-Football默认至少间隔10秒；本入口尚无跨进程配额锁，因此同批只启动一个采集进程，不能并发执行其他消耗相同账号配额的脚本。
401/403/429或缺绑定在本批停止同服务后续请求；不重复探测已明确受阻的入口。
暂时不自动重试，网络失败保存原因后进入适用备用接口。状态与权限按当次证据判断，不把旧Free套餐结论永久化。

已提供安全读取适配：5Dollar、football-data.org、API-Football/API-SPORTS、APIfootball.com、The Odds API、ESPN、FotMob。
5Dollar先查/status，竞彩参数固定types=jingcailottery；参数本身不证明权限。
APIfootball.com与API-Football是两个不同服务，不交叉发送密钥。
The Odds API的spreads保留原市场名，未独立验证前不称亚洲盘。
未适配接口仍可作为候选来源，但不能宣传已经自动接入；新增服务须先核实官方地址和鉴权方式。

计划示意（纯软件测试身份，不是真实赛事）：

```json
{
  "requests": {
    "summary": {"provider": "espn", "path": "/usa.1/summary", "params": {"event": "SYNTHETIC_ONLY"}, "ttl_seconds": 60}
  },
  "fields": [{
    "match_id": "SYNTHETIC_ONLY", "field": "fixture",
    "candidates": [{"request_id": "summary", "pointer": "/header",
      "identity_checks": [{"pointer": "/header/id", "equals": "SYNTHETIC_ONLY"}]}]
  }]
}
```

`fields[].candidates`依优先级排列；接口尚可满足字段时不请求下一级。
JSON Pointer和identity_checks由实际响应模式及独立核准的球队/比赛身份决定。
身份检查至少覆盖唯一赛事ID；不能只核一个球队名就混入其他日期、男女或年龄队列。
`value_checks`可要求确认首发标志等精确值，不能将previousLineup等历史阵容当本场首发。
赔率字段必须设置`requires_source_time=true`、`max_age_seconds`和候选`source_time_pointer`；查询时间不代替机构报价时间。

TTL建议：赛前首发60秒、赔率60秒；球队历史6小时并核查是否有新完赛、静态档案一天以上。
具体TTL写入计划，缓存校验哈希与查询时间；陈旧、未来或破损缓存不采用。
本程序不自动判定一份历史包是否已覆盖最新完赛，主代理须检查数据截止时间。
0值可能真实，不等于缺项；空列表、null、错误身份、缺失或陈旧报价时间均不能补齐字段。
伤停空列表不是健康证明，xG=0占位值和过程字段口径仍需独立核验。

结果的`api_evidence`仅表示取得可审查证据，状态始终是API_EVIDENCE_PENDING_REVIEW。
不自动确认官方首发、清洗入库、训练或改变模型输入。
`web_gap_tasks`只在整个适用接口阶段结束后产生，交给gpt-6-luna按字段查询；保存原始来源、抓取时间、信息时间及未解决原因。
若字段API不存在/不覆盖，应把已知失败入口和覆盖证据记入本批，不虚构请求以通过程序。
本入口要求至少一个适用API候选；确无候选的字段由主代理另记NO_APPLICABLE_API后交Luna，禁止编造候选。
缺项查不到就如实保留，不无限搜索、不猜阵容或盘口；禁止中国体彩及镜像，澳客允许。

此修改提供默认批采集入口和执行规范，不会自动迁移散落在历史报告里的临时脚本。
既有双模型数值入口仍为runtime/predict_active.py，核验后的同批输入才交给它。
未用真实比赛验证整个分析耗时，不能据单次接口耗时承诺整批几分钟完成。
