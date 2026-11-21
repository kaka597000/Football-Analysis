# 澳超男子常规赛阶段边界验收

共享草稿PR #51，保持aus.1 cohort及现有regular/regular-season配置。旧pin6639867及修复前草稿6c30bda的泛用denylist遗漏elimination-finals、semifinals、grand-final。本次只为aus.1显式接受provider-native regular-season或name末段Regular Season；任一已提供字段矛盾/未知即拒绝，不把未知阶段泛化为其他赛事的regular。复用欧冠/亚冠配对校验：scoreboard和summary阶段分别验证，year/type与来源联赛身份不得冲突，决赛原生slug不可被summary覆盖。

## 全归档真实重放

数据仓workspace-updates/20261104-data-supplement/aus.1-2025.json，Git blob `5c086b3c5065ee5e64c97e505d4047390884d8fd`，SHA256 `ba0facca3ccc82cd7f1c4fa9b1e6a60972acf5fdde20550679fc712c1b9ee71b`。175场均有aus-summary-raw中的真实配对文件，逐个核对aus-summary-manifest.json的SHA、原URL和实际捕获时间，不以合成摘要替代。

| 原生阶段 | 数量 | 旧pin标准化/临时加载 | 新标准化/共享认证 |
|---|---:|---:|---:|
| regular-season |168|168|168通过|
| elimination-finals |2|2误入|2拒绝|
| semifinals |4|4误入|4拒绝|
| grand-final |1|1误入|1拒绝|

修复前草稿的共享认证同样误放7场。修复后临时训练只读_load接受168、隔离0、排除0（7场已在上游拒绝，没有送入加载器）；没有运行train/fit或建立正式训练数据。常规样本分别为year2024/type12851共108场、year2025/type13720共60场，本归档常规阶段未新增误挡；不能推定所有年份或来源均验证。

- 正例718066 Perth Glory–Adelaide United，90分钟4–1；summary SHA `4d9f4709c37b533c54be86feb7c407a5e9c593a8a550221ae5f87ed7dceabe55`，捕获北京时间2026-11-04 15:08:47。
- 负例721521 Melbourne City–Melbourne Victory，90分钟1–0、grand-final；summary SHA `a9c8e70e32044a23ca6cd8b881e3c2fdf700e1cdf82cdea4ad8319e2a0f2392e`，捕获北京时间2026-11-04 15:09:04。拒绝依据为原生阶段，并非比分。
- 决赛七ID：721515、721516、721517、721518、721519、721520、721521。已检查本地根normalized-results、训练种子及与a88c1d5训练回执hash一致的normalized-results快照，均无这些ID；这是可复现准入缺陷，不称既成污染，也未修改任何历史记录。

官方比赛页 https://aleagues.com.au/match/a-league-men/melbourne-city-vs-melbourne-victory-31-05-2025/ 本轮网页抽取只有比赛身份、无可用比分/阶段字段；官方2025-26 Season Guide PDF因读取工具体积限制未读取成功。本轮实证依据是以上完整保留的ESPN配对，不冒称官方页面数值独立核验。

## 复现与交付范围

本地脚本 `/tmp/aus-phase-validation/replay.py RUNTIME_DIRECTORY`，pin-before.log、draft-before.log、after.log保留前后输出，逐场认证后只写临时目录并调用trainer._load。新增两个合成回归测试覆盖配置别名、缺失/未知、决赛覆盖及year/type冲突；采集器共18测试（包含在全套内）。运行 `python -m unittest discover -s runtime/automation -p test_collector.py` 及 `python scripts/run_tests.py --layer integration`，精确HEAD结果与CI在PR追加。

共用草稿已提供实际展示第一比分+两个不同备选的独立Top1/Top3契约、仅赛果采集选项、路径安全修复、保留证据认证与多个赛事阶段兼容。仍为离线/隔离交付：未接入正式发布或更新生产pin，历史缺实际展示不得补填；FotMob/K1适配、真实football-data.org加时/点球实证及全范围线上验收仍有缺口。此次没有新采集、正式训练、模型权重、数据主分支、范围、RSI或工作流修改，未合并部署。
