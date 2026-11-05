# 历史 closing 市场增量研究

独立入口 `python -m runtime.research_program.closing_market_ablation --data-root ROOT --manifest ROOT/reports/manifest.json --out NEW_DIRECTORY`。不连接严格 quote-time trainer，不修改活动工件或旧协议。

固定五联赛 E0/SP1/D1/I1/F1，2019/20–2023/24初训；唯一评价季2024/25，按联赛每月扩窗，较早目标月赛果可进入后续月份训练。2025/26在读取CSV前排除。基线与增量组训练、评价均使用完全相同的完整 Pinnacle closing 报价子集；历史GF/GA可使用该联赛全部合格赛果。赛果特征取前10场、至少3场；同联赛同日先生成全block特征再更新历史。

两边共享Poisson GLM，基础 ownGF/ownGA/oppGF/oppGA，增量 log(ownWin/oppWin)、log(draw)、logit(over2.5)。比例去水，缺失或无效closing不使用开盘替代。不输入is_home或场地参数，但市场价格间接含主场等信息，因此估计的是市场整体信息增量，不是去场地收益。neutral原值缺失保留null、venue_status UNKNOWN；Date/Time是来源原始时间信息，不构造quoted_at或first-known。

训练使用numpy/scipy、训练样本均值和总体标准差（零方差置1），L-BFGS-B优化平均Poisson NLL加0.1/2系数平方惩罚，截距不惩罚。系数安全界为±20/(1+sum(max(abs(z))))，截距±10，不截断eta；要求优化器成功。模型工件保留系数、均值、尺度和训练比赛ID hash；预测保留双方lambda，通过独立Poisson PMF可恢复任意精确比分概率，无需截断矩阵。

新目录先写protocol.json再训练，保留输入SHA/URL/抓取时间/原始行号、比赛身份hash与实体namespace。输出models.json、predictions.json、row_exclusions.json和report.json。只在2024/25目标内报告各联赛、赛季、联赛赛季和整体；比分Top1采用lower-mode平局规则，同时报告精确比分logloss、WDL accuracy/Brier/logloss/RPS、固定2.5 OU accuracy/Brier与4+总进球尾部。未训练市场WDL/OU单列。按联赛/日期block配对bootstrap 2000次，固定seed20261105，区间为净命中率增量百分位区间。

这是历史收盘重建，没有历史first-known/报价时间认证，不能称作as-of回测、正式综合Primary或活动Current/Test比较。所有输出均为研究，不自动激活；不后验调整超参数。

本轮真实执行与未过验收边界见[续办结果](remaining-nine-progress-20261105.md)。
