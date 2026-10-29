# 版本管理与交付规范

2026-10-29生效。完成一次可交付变更后必须测试、提交、推送并核验远端，不能把“本地保存”称为GitHub已同步。
不设置新定时任务；按每次任务交付执行。

## 三类身份分别登记

|对象|版本依据|保存位置|
|---|---|---|
|源码、配置、测试、规范|Git分支与完整commit；必要时检查点tag|本仓库|
|活动模型|活动注册表版本、每个工件实测SHA256、训练截止、依赖清单|WCPP运行目录；身份摘要放manifests/|
|数据库及冻结记录|实际文件哈希/大小、数据截止审计；冻结原哈希与追加链|WCPP数据目录及独立备份，Git仅记录身份|

源码更新不等于模型重新训练。代码commit、注册表版本、模型训练截止及数据库更新日不能互相替代。
以后活动入口的execution_audit.source_identity会保存实际Git commit及工作区是否有改动；没有Git时明确GIT_UNAVAILABLE，不伪造版本。原矩阵与选分规则不受此元数据影响。
当前模型/test独立；综合人工判断另轨。不得通过Git标签改名宣称模型晋级或准确率提高。

## 每次修改与同步

1. 查看`git status --short --branch`，读取执行规则，保护既有未提交修改。
2. `git fetch origin`，确认与远端关系。不得强推或覆盖远端新提交。
3. 较大改动先做本地受限备份或检查点提交；每个提交有明确目的，数值变更与工程整理分别记录。
4. 执行`python scripts/check_repository.py`和`python scripts/run_tests.py`；新增测试目录自动纳入。
5. 活动路由/工件/数据库发生变化时生成新的身份摘要：

```bash
python scripts/version_checkpoint.py --root /workspace/WCPP --out manifests/runtime-assets-新时间.json
```

6. 检查`git diff --check`和暂存清单，再提交与推送。安全的快进同步示例：

```bash
git add AGENTS.md README.md docs runtime scripts manifests .github .gitignore
git diff --cached --stat
git commit -m "fix: describe the actual change"
git push origin main
git rev-parse HEAD
git ls-remote origin refs/heads/main
git status --short --branch
```

本地与远端HEAD相同且无待提交改动，才报告同步完成。
如果远端已前进，先在独立分支整合、测试；存在未解决冲突时明确报告，不强行回退远端。
提交信息不能用“训练完成”掩盖仅有代码变更，不能用“全部通过”省略失败或跳过项。

## 密钥与运行资产

密钥只读环境，不进入源码、提交信息、日志或运行摘要；`.env.example`仅含变量名。
密钥检查会列文件、行号及类别，不打印原文；静态扫描不构成所有潜在秘密均已排除的保证。
忽略数据库、API快照、冻结输出、大型压缩包、二进制模型、缓存及检查点备份。
本次摘要会独立计算约1GB统一库与活动工件哈希，不能将旧登记值当实测。
摘要只登记文件身份，不是完整运行备份，也不证明数据库一致性或训练数据没有泄漏。
数据库在其他进程更新时应暂停写入或使用SQLite备份接口生成一致快照后另行登记；本任务未迁移数据库。

## 回滚

保留`checkpoint/cloud-20261029`源码检查点及`checkpoint-20261029`交付标签。
检查旧版本用独立目录/分支；已共享main的修复使用`git revert`追加逆向提交，再测试和推送。
不使用`reset --hard`或强推重写共享历史，不删除既有冻结预测。
恢复模型须使用当时注册表及其匹配工件，先核实SHA256；仅回退Git不能恢复仓库外资产。

GitHub Actions现覆盖全部测试目录及静态仓库检查。
没有外部数据的CI会明确跳过少量运行资产集成测试；云端具备资产时必须实际运行，跳过不能算通过。
CI结果以GitHub实际运行状态为准，本地通过不能冒称远端CI已通过。
