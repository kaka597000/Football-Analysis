# 近期角球能否改进 Current 的第一比分

**结论：停止这组配方。** 用同一比赛的主客队最近六场角球，调整 Current 给不同比分的概率。五大联赛历史后段首选净增 6 场，但按比赛日重抽的 95% 净差区间跨零，胜平负少中 4 场。能核回原 Current 首版矩阵的 9 场中，改变了两个首选，补中 0、丢失 0。研究到本次计分结束，正式权重及前瞻队列均沿用原版。

[单项规则和验收条件](https://github.com/kaka597000/Football-Analysis/commit/1c25d264cf44cc2345d6913fe4d4ce1cf2822c8a)先于拟合登记，随后在读取这次后段标签前保存[两组完整分布的指纹](https://github.com/kaka597000/Football-Analysis/commit/c265e580526f26831136b30f5b6bda44db764948)。此前的冷门分类器曾读角球。此次比较针对精确比分的 Current 基线，只有一个新系数。它衡量对方常给出、本方常获得多少角球对进球率的补充作用。角球次数本身不能识别落点、射门或机会质量。[现行 Current](https://github.com/kaka597000/Football-Analysis/blob/baac2b8342f557ac7913e0c45ad3ea2ed4861e5b/runtime/v4/main_model.py) · [既有冷门分类特征](https://github.com/kaka597000/Football-Analysis/blob/baac2b8342f557ac7913e0c45ad3ea2ed4861e5b/runtime/upset_v2/features.py) · [已结束的比分研究](https://github.com/kaka597000/Football-Analysis/blob/baac2b8342f557ac7913e0c45ad3ea2ed4861e5b/docs/research-archive-index.md)

## 原始复盘里，漏在哪里

北京时间 11 月 24 日晚场的 12 场已揭盲。按原作者正文回读，首选中 1 场、只中第二或第三比分 2 场（三个比分合计中 3 场）、三个都漏 9 场。原 Current 矩阵相应为 0、1、11 场，原 Test 为 1、2、9 场。作者漏掉的九场里，四场总计只进一球，另有一场进三球、四场进至少四球。这个批次只用于定位错误。项目逐场首次可见的严格回执及独立实发候选比较分母均为零。原正文、渠道信息和供应商原件保留在原工作区。

## 时间隔离对照

在每个赛季的 2 月 1 日按相同截点锁定当月所有比赛，两组只用截至 1 月 30 日的同赛季已完赛记录。主客角球按本联赛同场地角色的平均角球数标准化。任一队六场不齐、最早一场超过 90 天或最近六场有缺值便沿用 Current。早段在 2020、2021、2022 三个 2 月合计 636/636 场、1,272 个球队侧进球观测上拟合唯一系数 0.2675140368760652。留出一个赛季间隔。后段为 2024、2025、2026 年各自整个 2 月，共 66 个比赛日。两个版本共同使用主线的 WCPP-MAIN-1.2 baseline 公式与相同的日期限定样本重建当时参数。历史赛季文件在后来归档，每场首次可用时钟未知，计为历史开发回放。

| 588 场共同分母 | Current | 加入角球 | 改对 / 改错 | 按日期重抽的净差 95% 区间，百分点 |
| --- | ---: | ---: | ---: | ---: |
| 矩阵首选 | 66/588（11.22%） | 72/588（12.24%） | 12 / 6 | −0.45 至 +2.53 |
| 三个互异矩阵比分任一命中 | 180/588（30.61%） | 182/588（30.95%） | 7 / 5 | −0.60 至 +1.30 |
| 胜平负 | 303/588（51.53%） | 299/588（50.85%） | 2 / 6 | −1.59 至 +0.18 |
| 存档 Bet365 大小 2.5 全赢 | 318/588（54.08%） | 319/588（54.25%） | 12 / 11 | −1.58 至 +1.91 |
| 精确比分对数损失，越低越好 | 2.950031 | 2.945659 | | |

580 场启用角球，8 场回退，全部保留在分母。Bet365 对应 588 场均留有真实大小 2.5 两边价格，场次级报价时钟未知，故只按历史盘线结算。具体比分排位上，Current 的实际比分分别有 66 场排第一、114 场排第二或第三、408 场在前三外。角球方案改变 68 个首选，12 次改对全部来自原第二、第三位，6 次改错均将原本正确的首选退到备选。原前三以外的 408 场中，7 场进入备选，401 场仍然漏掉。

| 赛事 | 场数 | 首选 Current → 候选 | 改对 / 改错 | 矩阵前三 Current → 候选 |
| --- | ---: | ---: | ---: | ---: |
| 英超 | 126 | 15 → 13 | 0 / 2 | 28 → 30 |
| 西甲 | 120 | 14 → 16 | 3 / 1 | 44 → 45 |
| 德甲 | 108 | 16 → 17 | 2 / 1 | 37 → 36 |
| 意甲 | 126 | 12 → 14 | 3 / 1 | 42 → 41 |
| 法甲 | 108 | 9 → 12 | 4 / 1 | 29 → 30 |

分年份首选分别为 24 → 24（199 场）、18 → 19（196 场）、24 → 29（193 场）。仅有这五个赛事进入历史检验。逐赛事、年度、日期及实际盘口的完整分母可在[机器结果](results.json)核对。

## 按原赛前截止回放

北京时间 2026 年 11 月 20–21 日固定名单 33 场。15 场在本研究五联赛范围外。18 场范围内，6 场只定位到后续修订，3 场缺首版双模型，9 场在 11 月 21 日核回首版 Current、原矩阵排名、工件及其截止。德甲 5 场、意甲 2 场有双方完整角球输入，西甲 2 场依原规则回退。赛季原件在北京时间 11 月 18 日抓取、11 月 19 日已存入固定数据提交，早于每场模型截止。载有 9 份双模型原件的汇总包在 11 月 22 日提交；赛前生成和记录时间来自各自校验通过的首冻，汇总包时间不作为独立赛前送达证明。[赛季来源元数据](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/a5ecf0d0f1148a7acdb5675d45c347c0d5fb9d12/workspace-updates/20261118-transformer/transfer/raw/source-metadata.json) · [首版归档清单](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/blob/f7143128733067d568167d726f850a182de165c0/workspace-updates/20261122-score-form/manifest.json)

| 原矩阵可配对的 9 场 | Current | 加入角球 | 改对 / 改错 |
| --- | ---: | ---: | ---: |
| 矩阵首选 | 0/9 | 0/9 | 0 / 0 |
| 矩阵前三 | 2/9 | 2/9 | 0 / 0 |
| 胜平负 | 7/9 | 7/9 | 0 / 0 |
| 原赛前实盘全赢，扣除走盘 | 4/8 | 4/8 | 1 / 1 |
| 比分对数损失 | 3.021351 | 2.999627 | |

两臂都有 4 全赢、0 半赢、1 走、1 半输、3 全输。实际比分在两臂均为 2 场排在备选、7 场在前三外。各场均核对原报价两边、报价与查询时钟，以及原 ESPN 终场字节、比赛身份、开球和常规时间。只有一个比赛日，无法给出按比赛日估计的不确定区间。两组实际向用户发送的首选、展示三比分、精选三场全部命中的配对分母均为零。按事前规则停止，保留正式模型及 R7 和第三比分影子试验的原登记。

## 离线复算

使用项目 Python 3.12 环境。需要带指定提交的两个 Git 对象库、原 WCPP 运行资产和本地临时输出目录。归档证据及逐场预测留在本地。将数据放入自己的可读目录后，从本草稿源码根目录运行。旧审计器可从源码仓的归档提交创建只读 worktree。

```bash
P=research/20261125-corner-opportunities
export PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export PYTHONPATH="$PWD/runtime"
mkdir -p "$OUT"
git worktree add --detach "$OUT/source-69" 54ec6d70fdff7145c1889ffe6071b2188663bef2
AUDIT="$OUT/source-69/research/20261124-prior-dismissals:$OUT/source-69/runtime"
git -C "$DATA_REPO" show a6d45024cf6cf622f05e4b378ab53cfe2d6ae329:workspace-updates/20261123-kpi-optimization/source-assets.tar.gz > "$OUT/assets.tar.gz"
PYTHONPATH="$AUDIT" python "$P/retained.py" prepare --data "$DATA_REPO" --wcpp "$WCPP" --out "$OUT/retained.json"
python "$P/run_study.py" prepare --asset "$OUT/assets.tar.gz" --retained "$OUT/retained.json" --forecasts "$OUT/sealed"
cmp "$OUT/sealed/forecast-lock.json" "$P/forecast-lock.json"
PYTHONPATH="$AUDIT" python "$P/retained.py" labels --data "$DATA_REPO" --prepared "$OUT/retained.json" --expected-sha 6dbd4834a52fa2bf94c1dd417525313c4255067dd5c68569135f674f10a50765 --out "$OUT/labels.json"
python "$P/run_study.py" grade --asset "$OUT/assets.tar.gz" --retained "$OUT/retained.json" --forecasts "$OUT/sealed" --lock "$P/forecast-lock.json" --labels "$OUT/labels.json" --out "$OUT/graded"
cmp "$P/results.json" "$OUT/graded/results.json"
python -m unittest discover -s "$P" -v
python scripts/check_repository.py
python scripts/run_tests.py --layer integration
```

可单独撤下本研究目录和一个离线测试发现入口。默认预测、正式发布和训练均不导入此项研究。后续优先在正常授权材料中定位带时间戳的定位球射门与机会质量，先核可得性，再决定是否值得研究；这次成绩不支持在相同样本上重新调角球系数。
