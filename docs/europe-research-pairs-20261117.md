# 欧联/欧协独立双轨研究入口

本次只新增两项 **league-phase** 研究路线，不扩展到资格赛、旧分组赛或淘汰赛，不修改活动注册表、Champion 或既有冻结。

- Current 为本赛事已核验历史的主客均值 Poisson 基线；Test 为本赛事独立球队攻防 Poisson，固定半衰期 365 天、ridge 20。两者实训，非借用其他联赛或空壳。
- 数据来自数据仓库提交 `0e06c14e8338c7bca9b9f07ee68345dfe6930e41` 的 `normalized-results.jsonl` 和逐行 `raw_reference.path`。该文件 SHA256 为 `97fa429c6a08b7e0f2f198d76173019b2dc085bc5b1f9c00d4cfd72efd60f1a7`。逐原文检查 SHA、赛事、双方 ID、日期、明确 League Phase、完赛状态和90分钟分段比分。
- 欧联 162 场，欧协 234 场，训练截止均为 2026-11-05。数据是现有归档，并不声称全历史已补齐。
- 日期切分 75% 日期训练、其后日期诊断，不随机切分、不调参。沿用既有 Poisson 最少 80 训练行、至少 120 总行与 20 留出行标准。正式研究工件用全部合格行重拟合；这不是降低 Champion 准入门槛。
- 欧联 108 训练/54 诊断，双轨40场可预测（14场未见球队保留弃权），比分命中 Current 4、Test 5；欧协162训练/72诊断，双轨55场可预测（17场弃权），命中4、10。
- 历史原始 first-known 不完整，以上仅历史开发诊断，**不是 as-of 回测或未来改善证明**。未进行大小球实盘效果验收，不自动晋级。

新增 `europe_pair_adapter.predict_pair(fixture, route)` 供独立研究服务使用。它验证 index、训练行文件、双工件及实际数值源码哈希、轨道角色、赛事阶段、已知球队与时间；拒绝目标赛果、赛后/未来截止、工件创建前截止。两个矩阵独立计算，保留实际哈希和输入身份审计。缺条件明确拒绝。

构建：`PYTHONPATH=runtime python -m continental.build_europe_pairs --rows incremental.json --raw-root RETAINED --out NEW_DIRECTORY`。输出必须为新目录，拟合失败不写 ready index。`runtime/test_europe_pair_adapter.py` 为合成合同测试，不冻结虚构赛事。

数据源对象清单、压缩/解压 SHA、构建输入、训练行、协议、逐场留出结果及最终工件随独立研究数据归档保存。部署时用新 research registry 的 `engine: europe`，不要覆盖旧 active registry。尚未新增赛事自动发现、比赛预登记或定时预测。

## 独立审查修正

研究输入必须明确 `phase=PreMatch`，不能只凭未来开球时间接受 Live 或缺阶段。读取 index 和工件时使用同一份已校验字节；两轨计算后再次核验 index、双工件、训练行及实际源码哈希，并复核仍未开赛。运行中变化或跨过开球时间整次拒绝，不发布部分结果。新增阶段、运行中篡改、源码变化和时钟跨界回归；15 项合同测试通过。重新构建的 release-r2 绑定修正后源码，旧包只保留为历史记录，不可拿旧哈希冒充新包。

训练原文场地还要求 `neutralSite` 本身为严格布尔值，并与标准化行一致，缺失或冲突隔离。396 行逐条复核全部满足，数量与诊断未变；合同测试共16项。推理端只执行调用方已预核资料的合同检查，不冒称重新完成了真实未来赛事来源验证。最终重建包 release-r2-final 同时绑定构建器源码。
