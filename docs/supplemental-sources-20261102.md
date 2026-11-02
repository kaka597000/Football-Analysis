# 2026-11-02 用户补充数据源登记

公开网站完整分类清单见[公开网站与开放资料来源总表](public-data-sources.md)。2026-11-02再次补充：FootyStats为公开网站；Sofascore逐射门xG、FotMob球队总xG、ScanGoal实时列表团队xG，以及Understat/FBref赛后历史统计按该表登记。免费范围、广告情况和赛事覆盖以实际页面为准，本次文档更新未重新访问这些站点。

密钥不写入公开源码或私有备份，本文只存变量名称、鉴权规则和状态。用户给出的BSD密钥已在聊天中收到但未落盘、未调用；OpenFootAPI最初值带省略号，后续已收到完整替换值；新值同样未落盘、未安全绑定或调用。当前工具只支持声明安全绑定需求，不能代填聊天密钥值。两项需求已保存至环境配置草稿的HTTP密钥：WCPP_BSD_API_KEY（sports.bzzoiro.com）、WCPP_OPENFOOT_API_KEY（openfootapi.com）。保存不等于填值/发布/当前进程已绑定；环境发布仍有既有失败，不能称已接通。

|来源|本次核查与用途|实际状态|
|---|---|---|
|[BSD](https://sports.bzzoiro.com/docs/football/)|文档HTTP200；/api/v2/，Authorization: Token；文档宣称足球免费、30+联赛、xG/shotmap/阵容/赛前多机构赔率|只读客户端已适配；实际账户鉴权、日期/联赛字段覆盖、额度未验证|
|[OpenFootAPI](https://openfootapi.com/docs)|文档HTTP200；/v1，Authorization: Bearer；Starter文档为赛程/赛事/积分/搜索，Developer增加事件、首发、xG射门图、赔率|只读客户端已适配；完整替换密钥已收到但未安全绑定；账户订阅和高级字段未验证|
|[FootyStats](https://footystats.org/)|公开统计网页候选；不能假定所有xG字段均免费|本次主页HTTP请求失败；不能称实际取得字段|
|[Sofascore](https://www.sofascore.com/)|逐次射门xG候选|不是所有赛事/每次射门都保证有xG，按比赛ID、射门事件和时间实取|
|[FotMob](https://www.fotmob.com/)|团队总xG与shotmap候选；本项目此前已实际使用部分比赛|不按“团队级”限制其全部能力；逐场核查字段和低级别联赛覆盖|
|ScanGoal|用户提供的实时列表团队xG候选|官方域名、免费覆盖、页面字段待核实；不采用猜测域名作已确认来源|
|[Understat](https://understat.com/)|其覆盖联赛的历史赛后xG、xGA与射门过程|赛后数据只能用于之后赛事的历史特征；历史first-known时间不能补造|
|[FBref](https://fbref.com/)|候选历史球队/球员统计|当前赛季、提供方与xG字段可用性需实测，不将过去覆盖永久视为现状|

OpenFootAPI文档注明FotMob、ESPN、OpenLigaDB等上游。不能把同一FotMob快照经不同API转发当两份独立证据。它与此前指定open-football.org模拟器、openfootball/football.json归档分别登记，来源与许可不可混淆。

## 接入与时点规则

新客户端只发送GET到固定域名允许的路径；密钥只从运行时安全绑定读取，缺绑定返回ENV_BINDING_MISSING，不发带空密钥请求。文档公开示例/demo钥匙不当成用户账户鉴权，不用截断值碰运气。未启动轮询或消耗付费套餐。

采集仍走API优先流程，原始响应先存staging，核验比赛身份、90分钟口径、提供方/许可、known_at与query_time、xG定义和盘口机构/盘线/quoted_at后才进入分析。xGA使用同提供方对手xG；不同提供方尺度不静默混算。当前比赛赛后xG不得回填赛前特征。

来源可用、成功鉴权、字段覆盖、历史as-of合格、模型已训练为五种独立状态，不能互相替代。本站模型预测/AI概率不当成真实标签，也不未经比较直接合入主模型。

验证：359项本地测试无失败，1组历史私有资产集成检查缺文件跳过；新服务鉴权头/缺绑定拒绝/凭据脱敏均使用合成值测试。未使用聊天密钥执行真实鉴权，不能以测试通过冒充账户权限通过。
