# 六阶段工程重构交付

## 范围与模型保护

不改 MAIN/Test 参数、注册表或历史冻结，不重新训练活动模型。旧模型目录仍是受工件身份约束的兼容适配器；不为目录外观而破坏来源身份。原 Champion 与未晋级实验没有进入新活动路由。

1. 基线：现场 DUAL-4，19队列、38项工件引用哈希匹配。21/42仅为其他快照。
2. 当前文档：CURRENT_STATE、ARCHITECTURE、OPERATIONS，历史状态显式标记。
3. 入口：numerical engine、service、diagnostics、publication 分离；原predict_active CLI兼容。
4. ML共享核心：fit_booster和ml_matrix共用，联赛/UCL的独立缺失值策略不合并。`ml-source-compatibility.json`列出原源码SHA和已验证重构SHA，以及共享依赖SHA。仅全部匹配才兼容旧工件；任何相关源码漂移都会使旧工件校验失败。新训练额外登记部署重拟合与诊断折关系，不把重拟合工件的拟合成绩当样本外成绩。
5. 配置/时点：显式registry > WCPP_ACTIVE_MODEL > WCPP_RUNTIME_ROOT/runtime/active-model.json，不搜索其他资产。历史特征可用时点审计缺first_known或边界时不认证，严格回放接口直接拒绝；既有缺失历史没有倒填。
6. 测试：all为完整套件，portable为跨平台发布/锁/共享数学契约，integration先核验全部活动工件并将任何缺资产跳过判为未通过。CI增加Ubuntu/Windows portable矩阵。

## 发布恢复

预测CLI的`--stage live`在推理前明确拒绝。滚球分析冻结工具仍可记录已经核验的独立滚球输入，并不意味着活动引擎支持滚球。

输出同目录先写 `OUT.publication.json`，状态PREPARED→FROZEN→DELIVERED。冻结幂等键受同一跨进程锁保护；同键不同payload拒绝。数据交付先写完整临时文件再排他链接，保留原有输出不覆盖。

```bash
python runtime/predict_active.py --out OUT.json --resume-publication
```

恢复不重新推理、不新增同一事务冻结。若从未完成冻结且已开球，仍拒绝新增赛前记录；已冻结事务可恢复原交付。日志保留原预测，和输出一样作为运行数据管理，不提交源码仓库。

## 验证与边界

真实工件七个路由的新旧完整输出除生成时间一致；合成未来时间仅作工程检查，没有发布。两个ML实现分别与原版比较，真实LightGBM训练出的model_to_string及残差矩阵一致。

跨平台锁使用POSIX flock / Windows msvcrt；Windows实际结果以GitHub矩阵为准。跨进程测试验证并发冻结链；故障注入验证冻结成功后交付失败的幂等恢复。

历史fixed-recipe集成检查依赖未恢复的三项历史证据，不能算通过。新integration命令遇到这类跳过会返回失败，而不是伪称完整生产复现。新代码未补造缺失历史，未证明命中率改善。

公开仓库备用unattended工作流移除schedule，只保留手动入口；数据仓库现有定时任务未修改。仓库Actions变量读取权限403，不能声称已读到变量值或已修改分支保护。
