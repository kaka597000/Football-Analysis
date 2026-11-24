# 共用90分钟赛果与ESPN原生阶段补验

本轮在原草稿PR #51隔离分支继续修复，未采集新比赛、调用付费API、写入数据主分支、更新历史报告/冻结或部署。前一轮相对根路径修复保留。

## football-data.org的90分钟字段

[官方v4说明](https://docs.football-data.org/general/v4/overtime.html)明确区分duration和regularTime。已结束REGULAR用fullTime；已结束EXTRA_TIME或PENALTY_SHOOTOUT只用显式regularTime，不从fullTime、extraTime或penalties推导90分钟比分。支持实际v4常见home/away及官方示例homeTeam/awayTeam字段；同时出现却矛盾时拒绝。缺失、null、不合法比分、非FINISHED、身份/赛季阶段/原开球冲突均拒绝。REGULAR分支的空regularTime占位不影响合法fullTime。

**实证限制：未找到真实加时或点球的football-data.org归档。** 限定搜索包含已保存39场与46场responses、4个逐场detail、欧冠赛前响应及探测文件，均没有可用的特殊duration/regularTime。官方德国点球示例不是本仓实证。本轮对加时/点球只完成明确标注的合成正反例测试，不声称已通过真实样本验收。

另用4份真实FINISHED/REGULAR detail核验兼容：fdorg:558571为1–3、560607为0–1、565825为6–1、564713为2–5，均认证成功。读取保留source-captures中的实际捕获时间与saved_file_sha256，未将canonical JSON hash冒充原保存文件hash。

## ESPN配对原生阶段

原读取器仅从summary重建event，而已部署标准化器的欧联/欧协/世界杯阶段依据来自scoreboard的season.slug；summary常常没有该键。现以raw_reference.scoreboard_reference绑定已保存scoreboard的路径、SHA、source_url和captured_at，核对唯一同场事件、主客ID、原开球、competition及native phase。若summary同时给出slug/year/type，必须与scoreboard一致；不借summary缺失放宽阶段规则。

collector新观察只把**本轮已经取得**的scoreboard引用附到summary raw_reference，不增加请求、不修改旧观察，也不改变阶段分类算法。共用读取器继续调用regulation_row及原认证器。不支持的阶段、缺少配对证据、原始字节被改、根外引用、错误赛事/主客/时间均拒绝。

真实归档重放：

| 赛事与ID | 原scoreboard阶段 | summary缺slug时 | 提供认证配对后 |
|---|---|---|---|
| 欧联espn:720473 | league-phase，2024 | 拒绝 | 3–3 |
| 欧协espn:757965 | league-phase，2025 | 拒绝 | 4–1 |

使用数据仓已存20261104-data-supplement中的2025 scoreboard及根raw目录的同场summary；manifest时间、文件hash和逐场输出在[验收JSON](validation/shared-score-format-20261119.json)。旧标准化行没有kickoff_at，本次仅为**赛果适配器审计**从配对原始event.date读取真实身份，不创建或追认任何历史赛前预测。提及的20261117文档不在当前本地快照，未假称读取；实际用的是上述更早归档。

世界杯原生阶段链有合成group-stage验证，未完成本轮真实世界杯配对重放。ESPN加时/点球仍沿用原认证器边界，不因本次football-data.org修复自动扩大；其他20项范围的生产覆盖不由这些局部样本推定。

## 回归及复现

```sh
PYTHONPATH=runtime python -m unittest discover -s runtime/research -p test_display_review.py -v
PYTHONPATH=runtime python -m unittest discover -s runtime/automation -p test_collector.py -v
PYTHONPATH=runtime python -m unittest discover -s runtime/research_program -p test_three_arm_review.py -v
python scripts/run_tests.py --layer integration
```

展示适配器12项（含新增4项）、采集器12项、原路径复盘模块11项的针对验证与完整套件重叠，不能累计。反例包括：缺/空/布尔/负数/冲突regularTime、未完赛、主客冲突、缺阶段、错误联赛/同场ID/主客/开球、重复事件、header/native冲突及根外引用。全套结果和精确源码HEAD CI在PR验收更新中另列，不拿旧HEAD成功冒充新HEAD。

实证只验证已保存的赛果读取，不结算任何新推荐，不把矩阵Top3或候选比较替换成实际展示Top3。正式权重、活动路由、任务配置及英超RSI原冻结均不动。仍需真实football-data.org加时/点球样本及后续受控生产接入验证；此草稿不代表全20项已上线完成。
