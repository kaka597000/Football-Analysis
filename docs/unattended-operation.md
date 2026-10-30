# 无人值守采集与候选训练

用户授权：2026-10-30，北京时间。此授权允许本方案的有界自动任务，覆盖旧“不新增定时任务”的约束。

## 当前状态

代码、数据导出及本地实跑已完成，远端尚未启用。当前GitHub连接对createRepository返回`Resource not accessible by integration`，访问代码仓库Actions Secrets公钥端点也返回403。仓库push权限不等于仓库创建/Secrets权限，不能声称后台已经工作。
没有把私有数据、密钥或模型原件推到公开仓库。必须先完成下述私有存储和专用密钥安装，再手动验证一次远端采集和训练，才能标记为DEPLOYED。

## 计划执行频率和资源

|任务|北京时间|限制|
|---|---|---|
|公开数据增量采集|每日02:17、08:17、14:17、20:17|单轮100次HTTP上限、一次总重试、300秒墙钟预算、单线程低频|
|候选训练检查|每日10:37|只重训数据/代码有变化的队列，单CPU、最多21队列，整job20分钟上限|

GitHub实际调度可能延迟。GitHub公共仓库长期无活动可能停用schedule；本方案记录每轮成功时间，但不能承诺永不停止。原云端停止不会影响已正确安装的GitHub任务；本方案也不会让当前聊天或Luna在后台持续运行。
本版训练是固定参数的统计模型研究，不是无人值守GPT调用、深度学习或GPU训练。模型晋级保持关闭。

## 存储和数据范围

代码与workflow位于公开Football-Analysis，执行器用标准ubuntu-latest，公共仓库标准Actions计算不依赖购买服务器。
采集原文、赛果增量、事件索引、训练种子及候选工件只写私有`kaka597000/Football-Analysis-Data`。该仓库不执行自身Actions，不产生私有runner分钟消耗。本方案没有外部付费API调用。
通过部署密钥仅授予数据仓库读写权，私钥放代码仓库Actions Secret `WCPP_DATA_DEPLOY_KEY`。不得把个人PAT、聊天密钥或当前云端注入凭据复制进去。
私有数据Git提交提供跨任务恢复历史；它不是独立异地备份。当前1GB统一库、3GB历史模型仍在原处，种子不是完整数据库备份。

种子从21个已登记队列的当前工件及数据清单核验后导出，9127条、压缩约268KB。每条标为SEED_RETROSPECTIVE，缺first_known_at不反补，导出时间仅表示此次接收。历史中明确的SQLite neutral=0/1转换为布尔值；未知场地仍保留未知。
公开增量只使用现有ESPN/AsianOdds入口；严格分赛事、阶段、90分钟，取不到证据隔离；ESPN部分赛事覆盖不足时有逐源报告。盘口原文不是训练特征，未知报价时间不填抓取时间。
当前没有自动拉取所有xG、首发/伤停，没有对33项数据库赛事声称全覆盖；默认从活动21队列开始。分钟事件仅保存原始索引，未认证覆盖前不训练状态效应。

## 本地验证

本轮全目录302项测试通过，源码/凭据与diff检查通过。远端CI状态以实际GitHub运行结果为准，不能把本地通过等同于已部署。

导出9127条；首轮真实候选训练20队列完成，J1仅82条低于120门槛而跳过。全流程不修改active-model.json。
一次真实采集烟雾：50请求、47成功、新增14条90分钟结果；45秒试验预算下约20秒结束（为下一次25秒timeout留余量）。部分赛事未扫描，盘口未取得，coverage_complete=false；不把这次报告称全量覆盖。
文件位于`/workspace/WCPP/handoffs/unattended-seed-20261030-r2`；包含原始响应、request logs、结果、候选和run状态。原Canonical未写入。

## 一次性安装

当前连接权限不足，以下安装应由具有仓库创建、Deploy keys、Secrets和Variables权限的GitHub账号执行。无需购买API订阅，也无需把密码/密钥交给聊天。

1. 安装Python、Git、GitHub CLI、OpenSSH；运行`gh auth login`在GitHub官方页面登录自己的账号。
2. 下载项目交付的`WCPP_unattended_setup_20261030.zip`，解压。
3. 在解压目录运行：

```bash
python setup_unattended.py --seed-dir private-seed
```

安装器会创建/核查私有数据仓库、上传seed/config、生成仅限数据仓库的SSH密钥、设置Secret与`WCPP_UNATTENDED_ENABLED=true`，再触发一次collect。不会显示密钥内容。已有seed时应避免重复初始化；安装失败关闭开关并清理本次部署密钥。
执行后必须在GitHub Actions查看collect是否通过、私有仓库是否出现runs记录；再手动运行train检查候选是否持久化。未完成这两项验证，不登记已部署。
如果当前GitHub连接未来具备所需权限，代理可以执行同样步骤，无需用户重做环境数据配置。

## 停止、失败和恢复

- 停止：代码仓库Actions Variables设`WCPP_UNATTENDED_ENABLED=false`，或禁用该workflow；必要时取消正在运行的job。
- 所有运行共用并发组，避免私有状态多写。结果即使失败也尝试提交私有状态；若超时导致无法提交，会在Actions失败记录中可见，不能当成功采集。
- source失败、未知阶段、90分钟不明确或缺训练量都显式记录。没有新数据则SKIPPED_UNCHANGED，不每天盲目调参。
- 重跑使用workflow_dispatch选择collect/train；保留旧快照、候选和诊断，不覆盖原活动工件或冻结记录。
- 删除数据仓库Deploy key及代码仓库Secret可撤销自动读写。安装器不授予其他仓库权限。
