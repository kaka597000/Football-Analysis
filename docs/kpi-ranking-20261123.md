# 精确比分排名和同场净增验证

本轮继续使用第一比分20%、三选40%、胜平负65%、实际盘口大小球全赢率70%的阶段目标。选型按照第一比分、三选、胜平负、严格全赢率的顺序。比较使用相同的比赛、常规时间赛果与盘口；未报出、观望及待结算在全批登记中可查。

## 独立研究入口

- `runtime/research_program/market_score_ranker.py` 学习25个联合比分类别。每边4球以上的组内比分由盘口模型分配，再用固定比例将预测概率与盘口矩阵融合。
- `scripts/train_market_score_ranker.py` 支持单机构基线、多机构同线去水平均、仅盘口的联合排名、盘口加近期真实xG的联合排名。另有归档开收盘与亚洲让球特征只用于日期代理研究。
- 执行训练时单独评价单机构原生可用的方案。以一组已核实的双市场报价组成基础对照。同一事件、同次响应、同盘线的其他机构报价可供已登记的共识分支使用。价格结构、球队、赛程、时点和报价源均参与核验。资料缺失情况下，近况特征可退回盘口排名。带盘口时钟的实战评估从可用输入重新计算独立概率。
- `scripts/forecast_kpi_candidate.py` 接收上一轮工件与本轮排名工件。相同的赛前登记与首版校验规则继续适用。
- `runtime/research_program/paired_kpi_inference.py` 报告四项的同场改对、改错、净增和分母，按整比赛日聚类计算区间。历史和真实赛前记录的证据等级各自保留。

## 时间切分

现代样本用2019/20至2023/24拟合，2024/25选型，2025/26及2026/27做既往日期复核。后两个时期在上一轮均已检阅。

另起一个旧年代实验，只用2015/16和2016/17拟合、2017/18选型，再预测2018/19。该来源早期大小球为市场平均价，胜平负取Pinnacle，均为日期代理。市场基线与候选共享来源。任何缺盘场次仍登记于原始总体。这个实验评价跨年代稳定性。

## 复算

```bash
python scripts/train_market_score_ranker.py MODERN.jsonl --protocol PROTOCOL.json --out MODERN_RUN
python scripts/train_market_score_ranker.py LEGACY.jsonl --protocol PROTOCOL.json --out LEGACY_RUN --legacy
python runtime/research_program/paired_kpi_inference.py replay --input MATCHES.jsonl --baseline market --candidate TRACK --out PAIRED.json
```

每个时期的比分排名工件、源数据哈希和逐场预测在数据仓库。先从较早选型段确定比较方案，再读取评价段结果。合理的改进证据应同时包含第一比分净增、区间、其他指标及真实赛前覆盖。
