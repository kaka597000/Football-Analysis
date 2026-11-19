# 共享复盘根路径回归修复

数据提交 `69821e4cb6db23ac51cc81159ed4a1a0058ca39e` 已有6场巴甲认证结果，但市场共识review仍全部报告 `result evidence outside retained state`。`_certify`解析了原响应路径为绝对路径，却将它与未解析的相对state比较，导致相对CLI参数 `--state-dir .` 误判；绝对根正常。

最小修复只在原校验器加入 `state = Path(state).resolve()`。原响应路径仍resolve，并仍需位于解析后的根内；SHA、赛前身份、比赛/主客/开球、常规90分钟及捕获时间检查不变，不改变模型或正式统计。

## 已归档真实样本的隔离复现

在独立快照中读取归档字节，并调用同一个consensus_forward.review逻辑。保存函数被禁用，原report写入被捕获到内存，未重新生成任何赛前冻结、未修改归档报告。只验证已保存预测如何读取已保存结果，没有网络采集。

| 输入根 | 修复前 | 修复后 |
|---|---|---|
| 相对 `.` | settled 0 / pending 6 | settled 6 / pending 0 |
| 同目录绝对路径 | settled 6 / pending 0 | settled 6 / pending 0 |

修复后相对/绝对路径的完整内存报告完全一致。六场分别为401841310 2–0、312 0–0、313 2–1、314 2–1、315 0–1、316 4–0。额外英超espn:401879248（归档2–0）在修复前相对根失败、绝对根通过，修复后均通过。44份归档输入指纹保持不变，见[逐场来源与复现证据](validation/relative-state-root-20261119.json)。这不是正式结算发布，也不把模型矩阵Top3当用户实际展示Top3。

新增回归检查相对/绝对根等价，并对两种根逐一拒绝 `../outside.json.gz` 和绝对根外路径。复盘模块11项测试已通过，其中2项为本次新增。

## 复现

准备数据仓上述commit的独立只读快照（不可将输出指向生产checkout），在快照目录执行以下Python，SOURCE指向本修复源码的runtime目录：

```sh
PYTHONPATH="$SOURCE/runtime" python - <<'PY'
from pathlib import Path
from unittest.mock import patch
from research_program import consensus_forward as c
reports=[]
for state in (Path('.'), Path.cwd()):
    captured={}
    with patch.object(c,'save',lambda *args,**kwargs:None), patch.object(c,'atomic_json',lambda path,value:captured.update(value)):
        print(c.review(state,state/c.EXPERIMENT/'consensus/forward'))
    reports.append(captured)
assert reports[0]==reports[1]
PY
```

这会按正常review逻辑认证原始结果，但不触发网络或报告/冻结写入。请勿改用普通review CLI覆盖原归档review.json。

修复只在源码草稿PR #51；数据主分支、历史报告、原冻结、定时任务、正式权重和英超RSI均未改。现行workflow仍固定旧源码，推送草稿不代表已经上线，后续正式定时复盘仍需经过独立部署决定与真实运行验证。
