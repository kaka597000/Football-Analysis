# 按月滚动的历史重建数值对照

`runtime/research_program/walk_forward.py`以每个自然月为训练边界，扩展使用严格早于该月1日的同赛事赛果；整月预测完成后才在下一月训练中使用该月标签。固定最少120场训练，未达门槛整月记录为warmup，不悄悄删除。目标场次不参与模型或分布选择。两个不变性测试覆盖更改目标标签不影响预测、同月标签不得进入训练及实体重复拒绝。

四个固定对照为：活动Current锚定的baseline配方重建、已有动态泊松、固定参数5的负二项，以及已有固定节奏混合。未根据本轮结果挑选权重、超参数或分布。Current对照只支持已验证活动工件与`anchor.execution_recipe=baseline`的路由，读取工件是为确认配方和SHA；每折只从历史前缀重拟合配方，不使用活动工件内的未来训练权重。否则拒绝执行，不静默换配方。

因此结果是**历史赛果日期重建研究**，不是实际Current历史部署回放，也不认证当年赛前可获得的捕获时间。既有算法或数据曾用于开发，不能把重跑称为新未见验证。actual Current与候选的未来首次冻结比较仍是独立要求。所有输入场地仍依赖所载历史布尔值，程序不重新证明源端场地正确；缺失状态拒绝，未使用本月或未来结果来推断场地。

程序在任何拟合前保存配置、输入SHA、活动工件身份、运行算法源码哈希；每折保存训练完整行SHA、训练截止、目标数、矩阵NPZ及文件哈希。逐场预测JSON对应同一矩阵文件与索引，附原始实际比分用于独立结算。统计Primary为矩阵众数，不是人工综合推荐；概率指标、WDL、尾部Brier和校准分箱来自共享评价器。分联赛给出净增命中与按日期聚类bootstrap描述性区间，不作为重复试验的晋级门槛。未取得真实历史盘口时，实盘大小球n=0，禁止默认2.5球代替。未训练xG或盘口增量模型。

```bash
python scripts/index_history_availability.py --histories /absolute/cohort-parent --out /absolute/NEW-index.json
python runtime/research_program/walk_forward.py \
  --histories /absolute/cohort-parent \
  --registry /workspace/WCPP/runtime/active-model.json \
  --out /absolute/NEW-run-directory
```

每个`cohort-parent/<cohort>/merged-history.json`保存同赛事独立记录；现有首批仅整合7个国内联赛。输出`scope18`明确其他11项为本批未运行，而非造模型或宣布完成18项。可用时间索引记录各条已有来源的最晚capture边界，不推定first_known_at，也不独立认证原始响应；此索引不会混入历史日期重建模式作为虚假的严格时点认证。

2026-11-04本地实际执行7个联赛，2,734条、1,741条预测、993条预热排除，四模型6,964份矩阵已读回核对概率有效性和首选一致性。首轮与最终轮数值结果一致，属于复现，不是两批独立验证。动态泊松比分净增为正但整体Log Loss/Brier和WDL较弱，故Current/Test不调整；全部工件、报告与分项结果保存私有数据仓库，不发布原始训练数据。
