# 赛前滚动验证就绪检查与场地证据补充

## 结论

本轮建立可复现的保守就绪检查，尚未完成18赛事walk-forward、未做Current对Challenger的性能对照，也没有训练、切换或晋级活动模型。预测最高目标仍是Primary Top1；不能以概率指标或少量胜平负命中替代。

对固定私有增量commit所载273条、7联赛的标准化ESPN历史，用实际保留的抓取时间筛选。每个目标的假设cutoff定为开球前1秒，目的是给同批历史尽可能宽松的时点上界，不是声称当时真的有预测、赛前赛程身份或快照。调用`research_program.replay.eligible_labels`逐目标排除未到达标签、未来目标、非90分钟和缺少原始证据，273个目标中0个能从这273条找到任意一条合格的同批先前标签。七联赛目标数英40、西40、法36、德36、意50、挪32、瑞39。

本轮只检查同一增量中的273条，其他既有历史或许有更早抓取，不因此断定全项目无可用样本。7联赛合并研究历史2734条均有observed_at、first_known_at均为空，采集日期晚于各自比赛日；这表明它们是赛后留存，仍可能用于留存时点之后的未来预测，不能用于证明自身比赛时已知。不同schema的来源证据须逐条核验，不能按日期填造首次知悉时间。

输入为私有档案`workspace-updates/20261103-scope18-research/research-results.tar.gz`，归档SHA256 `313da28dcb51abd7bdc9ccdf6fe5b04400e3832ac89d598d541a630beee04319`，617文件逐一核验后解到独立目录；273条输入SHA256 `fce523d2b022548bf0945a0186d47813d376ce546b56c84d2fd316f8ed357f27`。诊断命令：

```bash
python scripts/audit_forward_readiness.py --rows /absolute/normalized-forward-only.json --evidence-root /absolute/its-parent --out /absolute/NEW-readiness.json
```

输出逐联赛目标、合格标签上限和排除原因；可读源码及单元验证复用replay模块。真实预测用真实赛前cutoff和首次冻结、同场Current、可用标签、真实盘口与预先固定方法；不能把假设cutoff当成预测时间。待完成剩余数据源的时点、身份和hash核验，再按赛事阶段生成可比样本和完整覆盖率。单次结果不足以宣布算法改进。

## 密钥别名

`Apifootballcom_APIKEY`的真实提供商身份未经认证：历史上曾被请求至API-Sports但始终是Cloudflare 403，该结果不能证明该密钥属于任一供应商。两个服务分别是API-Sports `v3.football.api-sports.io` 与 APIfootball.com `apiv3.apifootball.com`。此歧义别名现不再自动或显式选给任一服务，但仍参与敏感值脱敏和参数防泄漏检查；已明确的API-Sports `APIFOOTBALL`、`_apifootball`、`WCPP_API_FOOTBALL_KEY`及APIfootball.com `WCPP_APIFOOTBALLCOM_API_KEY`等各自保持。若用户确认旧密钥真实来源，应在安全环境中绑定到相应服务明确变量，不在聊天、代码、日志粘贴密钥。此修复没有实测证明旧密钥身份，也没有证明API-Sports的Cloudflare 403已恢复。

## 场地证据

新增`runtime/data_sources/venue.py`读取已保存的ESPN scoreboard或summary，检查URL、比赛/双方/开球、查询时间和SHA256，仅接受明确的neutralSite布尔值；缺失或冲突返回UNKNOWN/CONFLICT，场馆名称不是自动判断依据。当前只支持ESPN，未自动连接预测入口，也未建立其他来源或宣称找回六场。需要更多来源时应在同一比赛身份、赛前捕获时点与冲突处理下独立添加适配器。

实际用昨夜7场的留存ESPN scoreboard验证，7场该字段均缺失，均返回UNKNOWN。既有冻结的输入曾填写布尔值，单凭这份scoreboard不能验证；这不等于证明每个值错误，其他赛前证据仍待逐项独立查证。原结果不重跑/回改，已追加私有复盘勘误；该7场不能当已完成场地证据核验的晋级样本。两场更早冻结不属于这项7场文件检查范围。
