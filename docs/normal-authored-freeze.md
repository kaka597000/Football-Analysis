# 正常批次的综合比分冻结交接

11 月 23 日晚批的作者脚本 `early.py`、`issue_slate.py` 调用 `research.analysis_freeze.save`。它们把作者选定的比分写入 `primary`、`alternates`、列表 `displayed_scores` 和带比赛 ID 的 `user_facing_row`。打包器随后核对作者输入与冻结原文完全相同，列表与表格的比分一一对应，再写入逐场 Markdown 表和工作簿。该路径结束于本地表格，没有调用聊天交付方。31 条归档索引中含 15 个综合首冻，14 场在正式阶段，另 1 场为 MLS 季后赛观察。[原批次的证据包和逐场表](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/e958e8e09e062d147e98714a466d69ff6d35ca56/workspace-updates/20261123-evening-slate)

第一个断点发生在作者保存综合行时。上述两份脚本保存的是列表，缺少明确的正式阶段签核和交付用的比分段。草稿 #51 的当时版本在另一条 `publication.publish` 路径上生成结构化比分块。从真实批次改走该函数会把 `displayed_scores` 转为字典，原打包器的列表断言随即失败。作者脚本、打包器和最终聊天发送方都在主仓源码以外。[#51 固定版本](https://github.com/kaka597000/Football-Analysis/blob/d905fe4d02d06a7d1a0ed4df3d70b16f020305b8/docs/shared-display-delivery.md)

## 原负责人如何接入下一批

只在正常流程中，对确认处于授权阶段、已经选好三个不同比分的未来比赛使用。按原路径加载此版本的 `runtime`。批准的阶段行取自本批已审定的比赛范围，包含 `competition`、`cohort`、`stages`。原应用的赛事清单覆盖 20 项，Data 常规采集阶段表仅覆盖其中一部分。须使用对目标确有授权的阶段行，缺项继续留待核对。身份与阶段核对成功后，作者明确填写 `selection_scope='active_reference'`。

原作者可将保存完整比赛表的调用和本地写文件替换为以下调用。沿用这一笔签发的稳定键，发生中断时在同一原始输入上重试。

```python
from research.analysis_freeze import save_authored_row

pay["selection_scope"] = "active_reference"
issued = save_authored_row(pay, fixture, scope=approved_scope_row, idempotency_key=original_issue_key, out=out)
```

若原作者使用现有 CLI，以完整 `fixture` 和 `payload` 为输入。本命令会创建一份赛前冻结，并在本批私有目录原子生成完整交接文件，终端仅输出冻结回执。内容相同的重试复用原版，目标存在其他内容时报冲突。

```sh
python runtime/research/analysis_freeze.py "$INPUT" --stage prematch --track comprehensive \
  --root "$FREEZE_ROOT" --authored-table-key "$ISSUE_KEY" --authored-scopes "$APPROVED_SCOPES" \
  --authored-out "$PRIVATE_DIR/new-issued.json"
```

返回原打包器使用的 `fixture`、`payload`、`receipt`，并给出可放入最终正文的 `display_block`。冻结保留原表和原列表，新增 `authored_display` 记录既定顺序与文字，`authored_stage_scope` 记录批准的赛事及阶段。两者都在同一原文哈希内。实际范围校验使用现有赛事清单，要求 `identity_verified=true`，比赛所属 cohort、阶段与批准行一致。有年代前缀的阶段须同时匹配完整年代 cohort 及阶段。已开赛、明确身份或阶段冲突、缺少签核、错场、比分次序冲突或非三个互异比分均拒绝创建这次交接。

开赛前重复使用同一键与相同输入，读取同一份冻结。输入有改动会报冲突。到达开球边界后关闭新交接和交接正文重建，已保存的冻结、独立的原回执仍可只读核验。隐藏占位不会获得可计分状态。原生合同中的明确弃权保留原展示文字，同一场从弃权到后续完整版本使用同一首次展示槽；渠道仍须确认哪一版首先对用户可见。三比分表入口只接收完整的作者行。

## 在哪里仍然要接发送方

读回 `receipt.path` 并验证哈希后，负责人可把 `display_block` 放入正常的最终消息。归档中的 Markdown 表本身并无此段。固定旧 #51 对新记录仍会拒绝列表。将本分支的同名共享模块 `research/display_delivery.py` 一并使用后，#51 原有的 `blocks`、`stage` 可以原样读取新记录，直接绑定最终正文及同一冻结。合流时应保留共享模块对原生字典和带作者意向的列表两种输入的校验，发送/结算仍由 #51 管理。本补丁只移用其纯展示合同及必要的兼容读取。

正式结算仍需原渠道的首版可见正文、消息号、服务发送时钟及同场实际开球等既有证据。此次没有发送比赛预测，实际新增可评分交付为 0。生产配置、定时任务、模型和历史冻结不依赖这个可选入口。

## 已执行的验证

- 对原归档 15 场完整隔离重打包，只重定位本地输入/输出和模块路径。证据包、冻结索引、README 的字节相同，工作簿两个表的单元格与顺序相同。15 个原综合冻结都没有此次交接字段，不能签入展示回执。证据包 SHA-256 为 `446f929fc17484de087f389f4b1617d1792e26d8af4574fcc12ac9bec27f942a`，冻结索引为 `d4c874d4699aa042061ca05a00c4b8e0d9e7de0c51672665603b7d5c96bb6779`。
- 原作者脚本在已有 15 份本地输出上重启，调用保存函数 0 次。在隔离副本中，仅切换未来作者调用点，用虚构球队、时间和资料走原逐场循环，返回一份综合冻结及可兼容的表行。另用虚构比赛执行原打包器第 25–29 行全部五条原断言，涵盖冻结与双模型身份、哈希、顺序、时间及方向。
- 对同一虚构记录，固定 #51 CLI 与本分支的共享模块通过了 `blocks` 及 `stage`，准备文件中的正文 SHA 和冻结 SHA 均匹配。改动拟发正文的首选后拒绝准备。测试没有调用任何发送方或真实聊天回执。原作者脚本和打包脚本仅在原工作窗口留存；可移植的回归测试在当前仓库内。

```sh
PYTHONPATH=runtime python -m unittest discover -s runtime/research -p test_authored_freeze.py -v
python scripts/check_repository.py
python scripts/run_tests.py --layer integration
```

回滚新批次时可恢复原作者的 `save` 调用和原三个字段的打包输出。已经保存的消息、冻结及收据继续按其原版读取。仓库回滚仅需还原此提交，不涉及 Data 或线上工作流。
