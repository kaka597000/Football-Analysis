# R11 同机构亚洲让球与比分分布

六个联赛分别在固定的 2024/25 验证季选择权重。实验组和对照均把原 R7 配方的目标场输入重建为同一份 Bet365 非收盘 1X2 和 2.5 大小球。按净赔率概率调整矩阵，保留六个胜平负与大小球交叉格的概率。实验组另用主队让球及双边价格调整格内的比分。让四分之一球时分别结算两半投注额。

```sh
export PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
python -m research_program.handicap_study --intake intake --extension extension --r7-dir "$R7" --r10-dir "$R10" --out fresh-output
python -m unittest research_program.test_handicap_margin
```

intake 与 extension 从配套数据版解包。R7 和 R10 为相应独立版本目录。输出含每赛事验证赛季的训练结果、完整留出逐场预测、命中数、实盘全赢率、配对差异和按日期聚类区间。完整日历与同簿三市场齐备的子样本分开报告。各赛事另保存截至新日期的 R7 球队状态、固定 R11 权重和工件指纹。

普通和收盘字段属于不同报价档。CSV 没有给出机构的逐笔报价时钟。对于非四分之一球倍数的盘口保留来源行，排除该项让球输入。2015–19 的六联赛 CSV 没有鉴定到同机构让球双边价格。
