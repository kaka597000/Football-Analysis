# 公开网站与开放资料来源总表

更新：2026-11-02（北京时间）。本表汇总用户补充、docs/data-sources.json 和既有项目规范中的来源，登记不等于本轮已访问、字段免费、自动采集接通或已进入模型。历史连通性记录保留原日期，不代表当前可用。

## xG、xGA与比赛过程

|来源|用途与粒度|使用边界|
|---|---|---|
|[FootyStats](https://footystats.org/)|公开网站，球队统计及xG/xGA查询候选|公开网页与付费/API功能分开；具体赛季、字段免费范围需实查。此前首页请求失败不改变其公开网站属性|
|[Sofascore](https://www.sofascore.com/)|比赛过程、射门图及逐次射门xG；用户补充其支持实时射门xG、免费版有广告|按赛事和射门事件核查，不能保证所有比赛、所有射门均提供xG；广告与免费范围未在本次复验|
|[FotMob](https://www.fotmob.com/)|比赛卡片球队总xG、过程统计；部分赛事另有射门图/逐次射门资料|用户提示低级别联赛覆盖较弱；按联赛逐场核查，不能把全部能力限定为团队总xG|
|ScanGoal（官方域名待核实）|用户补充：进行中比赛列表显示团队级xG，便于多场扫描；称无广告|“所有进行中比赛”、免费范围、无广告及非逐次射门粒度均为用户提供线索，尚未独立核验；不猜域名或API地址|
|[Understat](https://understat.com/)|覆盖赛事的历史赛后xG、xGA、球队/球员与射门资料|主要作为之后赛事的近期历史证据；覆盖联赛、赛季及可访问性逐次确认|
|[FBref](https://fbref.com/)|历史赛后球队/球员统计，xG/xGA候选|需核查当前提供方、赛季和xG字段是否仍可获取；不将过去覆盖视为永久可用|
|[StatsBomb Open Data](https://github.com/statsbomb/open-data)|选定赛事的开放事件与射门资料|开放数据集，不是全赛事实时网站；按数据集覆盖与许可使用|
|Wyscout开放资料|既有规范登记的研究补充来源|仅使用确实公开且许可允许的具体资料；商业平台不整体视为免费源，不猜下载地址|

xGA采用同一提供方口径下对手的xG；明确单场/滚动窗口、主客场、是否含点球及统计截止时间。不同提供方的xG不静默混算。当前比赛赛后数据不能回填该场赛前特征；不存在的历史first_known_at不补造。

## 赛事身份、赛程、比分与官方消息

|来源|用途|
|---|---|
|UEFA、JFA及其他赛事/足协官方；联赛、俱乐部官网|赛程、规则、90分钟口径、首发、伤停、停赛和官方表态，优先作为L1证据|
|雷速体育、新球体育竞足板块、[澳客](https://www.okooo.com/)|赛事身份、竞彩编号、赛程和比分核实；页面、详情和验证限制分别记录|
|[Flashscore](https://www.flashscore.com/)、[ESPN](https://www.espn.com/soccer/)、[Sky Sports](https://www.skysports.com/football)|赛程、赛果和比赛资料交叉核验|
|FotMob、Sofascore|比赛身份、过程、阵容与近期表现交叉核验|
|[Transfermarkt](https://www.transfermarkt.com/navigation/statistiken)|伤停、停赛及球员线索，重要人员消息优先再核官方|
|[Sports Mole](https://www.sportsmole.co.uk/)、[Reuters](https://www.reuters.com/sports/)|前瞻、公开消息与报道；媒体预计首发不能冒充官方首发|

## 盘口与赔率公开参考

|来源|用途与限制|
|---|---|
|[AsianOdds.org](https://www.asianodds.org/)|BetInAsia单来源1X2、亚洲让球、大小球开盘/当前价格参考，不能当多机构共识|
|[Odds.Directory](https://odds.directory/en/football/markets/asian-handicap/)|聚合盘口、开盘/当前/高低点参考；原登记中具体机构未核实|
|[OddsPortal](https://www.oddsportal.com/)、Oddschecker、SportyTrader|赔率比较候选；网页可达与取得报价表分别核验|
|BetUS、PokerStars Sports|用户登记的公开报价参考；PokerStars三项让球不当亚洲盘|
|AgentBets|转引Pinnacle等报价，必须标二手来源|

公开可访问不保证所有地区、比赛或历史功能免费。报价需保存同机构、同盘线、真实quoted_at；价格变化不等于资金流。Betfair仅为尚未验证连接的交易数据候选，不能称已取得成交量、back/lay或正确比分交易数据。

## 其他开放历史与研究资料

- [football-data.co.uk](https://www.football-data.co.uk/)：历史CSV/赔率研究资料，字段与赛季逐份核查；与football-data.org账户API不同。
- [martj42/international_results](https://github.com/martj42/international_results)：国家队历史赛果；先核90分钟/加时口径，原始资料先入staging。
- openfootball/football.json：公开赛果归档；与OpenFootAPI及下述模拟器不同，具体覆盖与口径待核。
- [Open Football](https://open-football.org/zh)：足球世界模拟器，仅作模拟研究参考；生成球队/球员/赛果不能进入真实赛事标签或训练集。

BSD、OpenFootAPI、5DollarFootballAPI、football-data.org、API-Football/API-SPORTS、APIfootball.com、The Odds API、InferSports另属API服务清单。公开文档、免费套餐、公开网页和已鉴权API是不同状态，不将这些接口统称为无需密钥的公开网站数据源。

中国体育彩票官网及其镜像仍禁止访问。本次只更新来源文档，未进行新的逐站访问、自动采集、训练或模型切换。
