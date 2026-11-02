# Egidi blend 研究候选（WCPP-EGIDI-BLEND-0.1）

这是独立研究轨道，**不是**活动 Champion，也**不**接入 `runtime/predict_active.py`、双模型 Primary 或 v4–v7 现有引擎。回测数字不能当作已晋级成绩，也不能改写已冻结预测。

比分矩阵在本目录内重写：对角膨胀的二元泊松，再乘 Dixon–Coles 低比分修正 τ。历史 λ 与 Shin 去水后的盘口按联赛、按时代分别拟合混合权重。混合矩阵的前 10 个比分交给 LightGBM `lambdarank`（LambdaMART）重排，目标是 Top1/Top3。`aus.1`、`jpn.1`、`kor.1`、`usa.1` 只做各自的分布外评估：每条用本联赛更早的比赛拟合 λ，混合权重和排序模型只用欧洲五大联赛的样本，不把这四条放进训练，也不互相合并后训练。

## 数据

按以下顺序合并，同一场（联赛、日期、主队、客队）先到的来源优先，后到的只有在赔率更全时才替换：

1. `--jsonl`，可重复。
2. 只读 sqlite，默认若存在则使用 `/workspace/WCPP/data/unified/WCPP_统一赛事库.sqlite`。
3. `--football-data-dir` 里的 football-data.co.uk CSV。

公开 CSV 只下载欧洲五大联赛：英超 `E0`、西甲 `SP1`、意甲 `I1`、德甲 `D1`、法甲 `F1`。入口**不会**请求 ESPN，也**不会**编造韩职联或美职联 slug。`aus.1` / `jpn.1` / `kor.1` / `usa.1` 只有 JSONL 或 sqlite 里真有这些行时才做分布外评估；没有就在报告里写明缺哪一家，不用欧洲比赛或其他 OOD 联赛冒充。

赔率优先级：收盘 1X2（`PSCH/PSCD/PSCA`，其次 `B365CH`、`AvgCH`、`MaxCH`），否则开盘（`PSH`、`B365H` 等）。大小球只用 2.5 线的收盘列（`PC>2.5` / `PC<2.5` 等），没有收盘再用开盘。正确比分只有行内真有价格才读；football-data.co.uk 主 CSV 通常没有正确比分，此时市场基线改为：用 Shin 后的胜平负（以及有则用的 2.5 大小球）去匹配一个**不含历史**的独立泊松网格。xG、首发缺失就记缺失标记，不补造。

JSONL 一行一场，至少包含 `league,date,home,away,home_goals,away_goals`。可选 `odds_1x2`（三项欧赔）、`odds_1x2_closing`、`odds_ou`（大/小）、`odds_ou_closing`、`odds_cs`（`{"1-0": 7.5}`）、`home_xg`、`away_xg`、`lineup_known`、`neutral`。

日本 J1 在 2026 年 1–6 月按库内规则视为未分类过渡，本轨道直接排除，不并进 2026/27。

## 验证设计

- 欧洲按赛季滚动起点。某季开赛日为截止日 D：Dixon–Coles 只用该联赛 D 之前的比赛；混合权重用欧洲偶数赛季的出折预测；排序模型用欧洲奇数赛季的出折预测。出折拟合本身也只用更早的比赛。
- 前 `min_prior_seasons`（默认 3）个赛季只用于拟合，不进命中率分母。之后**每一个有足够历史的赛季都评估**，不是只看最近几季。
- 时代切分事先写死：`pre_2008`、`2008_2015`、`2016_2021`、`2022_plus`。样本少于门槛时退到该联赛汇总、该时代的欧洲汇总，再退到欧洲总汇。分布外比赛不允许使用自己联赛的权重格子。
- 半衰期固定 730 天，不在测试季上挑选。
- 基线：（a）纯 Dixon–Coles 矩阵；（b）纯市场 Shin 网格；（c）永远 1-1。永远 1-1 的名单只有一个比分，所以它的 Top3 等于 Top1。
- 指标：Top1、Top3、胜平负 RPS（有序三项，除以 2）。McNemar 为配对精确二项检验，市场缺赔率的场次不进入对市场的配对分母。
- 排序模型改变前 10 名的次序，并把这 10 个格子原有的概率质量按 softmax 重分配后再算 RPS。样本不够时不训练排序器，候选退回混合矩阵的原次序，报告里 `ranker_applied` 为假。

## 运行

```bash
python -m pip install -r requirements.txt -r requirements-test.txt
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python runtime/research/egidi_blend/backtest.py \
  --download-football-data /tmp/wcpp-egidi-fd \
  --out /tmp/egidi-blend-report.json
```

已有 CSV 或数据库时不必再下载：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python runtime/research/egidi_blend/backtest.py \
  --football-data-dir /path/to/csvs \
  --sqlite /workspace/WCPP/data/unified/WCPP_统一赛事库.sqlite \
  --jsonl /path/to/asia.jsonl \
  --out /tmp/egidi-blend-report.json \
  --save-rows /tmp/egidi-blend-rows.jsonl
```

没有可用比赛时退出码为 2，JSON 里写 `blocker: NO_MATCH_ROWS` 以及期望输入。单元测试：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python -m unittest discover -s runtime/research/egidi_blend -p 'test_*.py'
```

## 明确不做

不改 `predict_active.py`、活动注册表、Champion、永久 λ、v4–v7 / `weighted_model` / `ml_model` 的现有拟合。本目录数字是研究候选，不是 Primary Top1 的正式成绩。

## 第一次回测（2026-11-01 17:51 北京时间）

云端没有 Canonical sqlite。本次用公开 football-data.co.uk 五大联赛 CSV（1993/94 至 2026/27 已赛部分，载入 59483 场）跑完全部可评估赛季。正确比分赔率 0 场，大小球和 1X2 按上面的退化规则使用；xG 只有 2026 年文件中的 `HxG/AxG` 404 场，且只作为更早比赛的先验。`aus.1`、`jpn.1`、`kor.1` 本次没有行，分布外汇总为 0，没有用欧洲比赛或 ESPN 韩职联补齐。该次回测也没有 `usa.1` 行；此后美职联与这三条一样只作各自的 OOD 评估，不进入欧洲混合权重或排序训练。数字不变，仍不能晋级。

评估分母 53981 场（1996 至 2026 共 31 个赛季起点；前三个赛季只拟合）。候选没有超过基线，**不能晋级**：

| | 场次 | Top1 | Top3 | 胜平负 RPS |
|---|---:|---:|---:|---:|
| 候选（混合 + LambdaMART） | 53981 | 11.96% | 32.22% | 0.1994 |
| 纯 Dixon–Coles | 53981 | 12.58% | 33.30% | 0.2045 |
| 纯市场 Shin 网格 | 47097 | 13.12% | 34.30% | 0.1969 |
| 永远 1-1 | 53981 | 12.01% | 12.01% | 0.3694 |

Top1 的精确 McNemar：对 Dixon–Coles，候选独中 2805、基线独中 3139，p=1.6e-5；对市场，2627 对 3212，p=2.0e-14；对永远 1-1，3407 对 3436，p=0.74。排序器在奇数赛季出折样本够用后才训练（31 个起点里 26 个）；未训练的 1996–2000 候选与纯 Dixon–Coles 相同。2019/20 赛季在 2020 年 7 月续赛的场次会按 7 月赛季键进入下一季起点，训练仍只用更早日期，没有把未来赛果放进拟合。

复现：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python runtime/research/egidi_blend/backtest.py \
  --download-football-data /tmp/wcpp-egidi-fd \
  --out /tmp/egidi-blend-report.json
```

