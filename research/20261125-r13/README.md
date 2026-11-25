# 国内赛程与洲际比赛比分

R13 按亚冠精英、欧冠、欧联、欧协联分别拟合。每场洲际比赛用 ESPN 的赛事实体和双方球队 ID 连接此前的本国顶级联赛。进失球与该国内联赛此前的主客队平均值比较，另记录国内赛事距今的天数和过去两周场数。国内杯赛、国家队和训练的负荷没有进入这四个特征。未取得国内历史的球队使用零调整，所有原赛事仍参与验收。

训练早于选权重的验证季，验证季早于测试季。原始 R7、同一运行环境重建的 R7、原始 R10 和可用的市场预测分别作为对照。分母以逐场首次预测提供的方向和实际盘口为准。赛前回执只使用在签发日之前完赛的国内数据。收集于赛后的历史来源保留实际取源时间，历史对比按来源赛日隔离。

## 复算

使用 Python 3.12、项目 requirements.txt 和 LightGBM 4.6.0。DATA 指向数据仓库的 workspace-updates。先用 R13 数据目录中的 verify.py 解出已校验的原件和数据，然后执行以下命令。输出路径使用新目录。

```sh
export PYTHONPATH=runtime OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
R13="$DATA/20261125-r13-domestic-form"
python "$R13/verify.py" --extract inputs-r13
python -m research_program.domestic_form_study \
  --r5 "$DATA/20261123-all-scope-kpi" --r7 "$DATA/20261123-prematch-literature" \
  --r10 "$DATA/20261125-r10-shot-conversion" \
  --domestic inputs-r13/domestic/domestic.jsonl --identities inputs-r13/continental/identities.jsonl \
  --extension inputs-r13/continental/extension.jsonl --out replay-r13
python "$R13/verify.py" --compare replay-r13
```

新研究预测使用 research_program.domestic_form_forward。它从 R5 的首次价格记录和 R10 的首次逐场注册中读取同一对阵、主客、开球时间和盘口。输入与权重绑定到新的 R13 回执。research_program.ranked_score_study 可从这批回执冻结各赛事最多三场的精选，并用已有的常规时间证据逐场结算。
