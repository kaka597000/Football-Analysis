# 统一赛事数据运行层

Python 3.12标准库，Linux。主数据库默认 `/workspace/WCPP/data/unified/WCPP_统一赛事库.sqlite`；历史9月29日Canonical只读保留。使用已有checkout，无需新建worktree。

```bash
cd /workspace/Football-Analysis
python runtime/database/cli.py migrate --root /workspace/WCPP
python runtime/database/cli.py refresh --root /workspace/WCPP
python runtime/database/cli.py audit --root /workspace/WCPP
python runtime/database/cli.py export --root /workspace/WCPP
python -m unittest discover -s runtime/database -p 'test_*.py' -v
```

`migrate`导入原主库、JFA扩展库、识别的历史JSON/CSV与赛果快照，再补采。`bootstrap`无需原始附件，直接从公开来源建立新库；它不能恢复用户特有档案或预测。`repair`用已保存来源重解析，保留原查询时间。命令使用写锁，重复运行不重复增加比赛；未变化赛果不反复新增观察。

## 统一规则

- 赛事显式映射；赛季保留适用口径。日本2026百年构想与2026/27常规赛事分库内不同赛事族；韩国常规、分组及附加赛按stage区分。
- 球队优先ESPN、K League、5Dollar稳定ID；NFKC标准化仅处理空格与大小写。禁用模糊匹配。同名不同provider ID保留独立身份；歧义简称要求ID，不猜。
- 旧别名可通过至少两次同赛事、日期、已确认对手和相同主客顺序证明；韩国跨语言另要求至少八个日期／主客指纹完整匹配且目标唯一。不使用比分做身份匹配。
- match键由赛事、日期、球队ID及列示顺序构成。未核实时区的旧日期不猜移一天；错误日期、行政比分及90分钟口径不明记录隔离。
- sources、observations不可覆盖；current matches为可重建投影。进行中或未确认赛果保持NULL，不填0。加时、点球不当作90分钟；JFA列示第一队不当作主场。
- 同源修订使用最新真实观察；跨来源冲突阻止训练。ESPN和Football-Data一致、仅与旧Canonical冲突时生成明确修正登记；K League官方明确终场与旧记录冲突时采用官方赛果。保留原值与规则。两源一致不宣称底层供应商独立。
- `training_90`只表示比分及身份基础门槛，不等于已完成时间留出验证。历史first_known_at保留未知；不能用于泄漏认证或追认赛前冻结。

## 历史补齐与覆盖

Football-Data补采2020/21至当前赛季的英超、英冠、德甲、德乙、西甲、意甲、法甲、葡超、荷甲。CARDINALITY_COMPLETE仅表示独立比赛数达到已知赛制数量，不冒充逐场官方认证。ESPN按年度补采其他支持的赛事；韩K／韩K2使用官方月度接口。日本乙级及日本／韩国杯赛补读Betexplorer各阶段页面。

API可选密钥仅读取环境变量`WCPP_5DOLLAR_API_KEY`，先验证/status，再使用官方文档的联赛历史接口获取日乙及百年构想赛程。API明示终场才能结算，百年构想的显示最终比分缺少90分钟证明时隔离。禁止记录密钥，数据库/原始API响应不上传Git。5Dollar数据仅内部研究使用，不再分发为数据服务。

覆盖表会保留GAP、IN_PROGRESS和SOURCE_LIST_IMPORTED。公开来源未返回的当前杯赛不编造样本；未核实加时赛果不会强行转90分钟。新增赛季在下一次更新自动补查。

## 定时更新

```bash
python runtime/database/scheduler.py --root /workspace/WCPP --once
nohup python runtime/database/scheduler.py --root /workspace/WCPP > /workspace/WCPP/data/unified/scheduler-process.log 2>&1 &
```

本机进程按北京时间04:00、08:00、18:00运行refresh。state在data/unified/scheduler，保存PID、下一次时间、真实执行状态和日志。单实例锁避免多进程；环境停止后进程停止，重启时需要依照云环境startup instructions重新启动。未到执行时刻不能宣称定时任务已执行成功。

在Codex托管命令执行器中，普通后台shell子进程可能被清理。应使用执行器的持续PTY会话运行`python runtime/database/scheduler.py --root /workspace/WCPP`并保存会话ID，再从另一个命令核对状态与进程；仅保存nohup命令不代表进程持续运行。

数据输出位于data/、reports/，均被Git忽略。默认通过audit/coverage及读库接口消费，不能继续把旧源文件统计当作统一库库存。模型参数与正式KPI不由数据库更新自动改变。

## 5Dollar七联赛历史补采

`five_dollar_history.py` 覆盖英超、西甲、德甲、意甲、法甲、日职联、日职乙。供应商ID由真实联赛列表核实。使用现有密钥，先检查/status，再固定采集时间窗口，以50条分页附带events与stats，检查跨页重复、联赛与时间一致性、分页终点，并按Retry-After处理限流。缓存仅用于同一固定窗口续传。

```bash
python runtime/database/five_dollar_history.py harvest --directory /workspace/WCPP/reports/NEW_HISTORY_RUN
python runtime/database/five_dollar_history.py import --directory /workspace/WCPP/reports/NEW_HISTORY_RUN
# 后续增量选择新的目录，以最近七天窗口采集
python runtime/database/five_dollar_history.py harvest --lookback-days 7 --directory /workspace/WCPP/reports/NEW_INCREMENTAL_RUN
```

import要求已有统一数据库，取得writer.lock并用SQLite backup保存修改前副本。比赛比分观察保留原始来源、不覆盖历史；`api_fixture_payloads`独立保存每个完整响应记录，使比分未变化时新增的统计与事件仍能入库。`provider_team_ids`保留供应商ID到规范身份的映射；不同拼写只有至少两个日期、同赛事和已锚定对手的主客顺序证据且目标唯一时才桥接，不用比分或模糊名称。抽查的单场赔率和积分榜另存provider_payloads。

只有finished/full及合理整数比分可进入90分钟基础门槛；源内终场与事件线矛盾标记unknown，不强选某值。跨来源结果冲突阻止训练。日本2026上半年过渡赛事完整payload进入隔离区，未独立确认赛事类型与90分钟口径前不混入普通联赛。所有历史first_known_at继续NULL，不将本次赛后取数改称当时赛前可得。

2026-10-10实际Community密钥拒绝2014窗口，错误明确限制最近12个月；批量include=odds也被拒绝，单场Bet365赔率可读。完成分页仅表示当前权限/固定窗口的返回集全部取得，不代表服务商2014年以来总量或完整赛季。要访问2014历史或完整逐笔盘口历史，需实际可用的Ultra权限；不通过更换参数绕过权限。

本轮2175条完整payload已保存，全库41359→41383（净24条比赛）。详细接入数量、隔离与完整性见云端reports/5dollar-history-20261010。历史赔率没有逐场全量采集；七个联赛各保存一场赔率权限抽查和积分榜快照。没有新增无人值守任务或自动改模型参数。
