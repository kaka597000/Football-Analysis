# 日职联 12 月第一阶段赛前窗口

日职联常规赛的两个窗口为北京时间 12 月 4–6 日和 12–13 日，各十场。登记保留原年度赛历、当次 ESPN 日历与补核摘要，并锁定适用的 Current/Test 工件。比赛延期、未出单、弃权、待赛和证据缺失均保留原席位。[登记协议](protocol.json)

两窗分别核算首选 20%、三选 40%、胜平负 65%、实际大小球非走盘全赢率 70% 和精选三场首选全中。每窗延用原十场登记的覆盖要求，确认首版比分和胜平负各十场，严格实盘至少八场，已结算非走盘至少七场。首发及可核盘口随实际赛前时点补采，并随首版保存来源。

登记、原文锁定和复盘共用同一套入口。新登记的比赛与窗口名从冻结记录读取。按开球排序的原文以全文哈希绑定，逐场保留首版回执、独立渠道回读及原证据。两个窗口各自达到同一阶段才记通过。[项目目标](../../docs/project-goals.md)

```bash
PYTHONPATH=runtime python -m research_program.display_window_review \
  --registration PATH_TO_ORIGINAL_REGISTRATION --batch-root ARCHIVED_BATCHES \
  --results-state NORMALIZED_RESULTS --result-evidence-root RAW_RESULTS \
  --readbacks FIRST_DELIVERY_EVIDENCE --actual-kickoffs VERIFIED_STARTS \
  --approved-scopes ORIGINAL_SCOPES
```
