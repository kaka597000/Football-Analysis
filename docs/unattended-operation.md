# 无人值守采集与候选训练

用户授权：2026-10-30，北京时间。此授权允许本方案的有界自动任务，覆盖旧“不新增定时任务”的约束。

## 当前状态

用户已创建真实私有仓库`kaka597000/kaka597000-Football-Analysis-Data`并授权云端安装。原先期待的`Football-Analysis-Data`不是实际名称；不能根据该旧地址404认定用户没有创建仓库。
现连接具有私有仓库内容和workflow权限，仍无Secrets/Deploy keys管理权限。因此改用私有仓库自身的Actions/GITHUB_TOKEN，不需要安装包、本地电脑、个人PAT或专用Secret。远端采集、训练及持久化验证结果以部署报告为准，未完成不得登记DEPLOYED。

## 计划执行频率和资源

|任务|北京时间|限制|
|---|---|---|
|公开数据增量采集|每日02:17、08:17、14:17、20:17|单轮100次HTTP上限、一次总重试、150秒采集预算、单线程低频，整job5分钟上限|
|候选训练检查|每日10:37|只重训数据/代码有变化的队列，单CPU、最多21队列，整job5分钟上限|

运行使用GitHub账户自带的私有仓库Actions额度；每天5轮、31天按每轮5分钟计算上限775分钟，不含手动加跑与其他项目。账户剩余额度和计费设置本连接无权读取，不能承诺共享额度始终充足或基础设施绝无账单。未购买服务、设置付费预算、调用收费API或启用付费/GPU runner。

GitHub实际调度可能延迟。GitHub公共仓库长期无活动可能停用schedule；本方案记录每轮成功时间，但不能承诺永不停止。原云端停止不会影响已正确安装的GitHub任务；本方案也不会让当前聊天或Luna在后台持续运行。
本版训练是固定参数的统计模型研究，不是无人值守GPT调用、深度学习或GPU训练。模型晋级保持关闭。

## 存储和数据范围

源码和workflow模板位于公开Football-Analysis；真实workflow安装到私有数据仓库，执行器为标准ubuntu-latest。公开源码固定到完整commit，避免后台运行内容无法追溯。模板位于`.github/workflow-templates/unattended-private.yml`，更新代码需验证后同步固定commit。
采集原文、赛果增量、事件索引、训练种子及候选工件只写私有`kaka597000/kaka597000-Football-Analysis-Data`，不上传公开Actions artifact或公开训练数据。当前workflow用其自身的短期GITHUB_TOKEN写回本仓库，不授予其他仓库权限，不需要Secrets管理或SSH密钥。公开仓库原`unattended.yml`仅作停用备用。
私有数据Git提交提供跨任务恢复历史；它不是独立异地备份。当前1GB统一库、3GB历史模型仍在原处，种子不是完整数据库备份。

种子从21个已登记队列的当前工件及数据清单核验后导出，9127条、压缩约268KB。每条标为SEED_RETROSPECTIVE，缺first_known_at不反补，导出时间仅表示此次接收。历史中明确的SQLite neutral=0/1转换为布尔值；未知场地仍保留未知。
公开增量只使用现有ESPN/AsianOdds入口；严格分赛事、阶段、90分钟，取不到证据隔离；ESPN部分赛事覆盖不足时有逐源报告。盘口原文不是训练特征，未知报价时间不填抓取时间。
当前没有自动拉取所有xG、首发/伤停，没有对33项数据库赛事声称全覆盖；默认从活动21队列开始。分钟事件仅保存原始索引，未认证覆盖前不训练状态效应。

## 本地验证

本轮全目录302项测试通过，源码/凭据与diff检查通过。远端CI状态以实际GitHub运行结果为准，不能把本地通过等同于已部署。

导出9127条；首轮真实候选训练20队列完成，J1仅82条低于120门槛而跳过。全流程不修改active-model.json。
一次真实采集烟雾：50请求、47成功、新增14条90分钟结果；45秒试验预算下约20秒结束（为下一次25秒timeout留余量）。部分赛事未扫描，盘口未取得，coverage_complete=false；不把这次报告称全量覆盖。
文件位于`/workspace/WCPP/handoffs/unattended-seed-20261030-r2`；包含原始响应、request logs、结果、候选和run状态。原Canonical未写入。

## 云端安装与验证

代理核查仓库为private，将已独立验证SHA256的seed和明确scopes上传；不覆盖已有不同种子。将模板的`__WCPP_CODE_REF__`替换为已推送的完整源码commit，安装为私有`.github/workflows/unattended.yml`。
先关闭旧公开仓库的unattended workflow，避免重复定时；不影响公开Python测试CI。手动dispatch collect、确认远端成功并在私有runs查到状态；再dispatch train，核查候选、诊断和私有提交。两者成功后才能登记DEPLOYED。
`scripts/setup_unattended.py`和旧zip保留为原公开执行/部署密钥方案的历史备用，当前方案不使用它们，不要求用户在任何电脑登录或安装。

## 停止、失败和恢复

- 停止：禁用私有数据仓库的`unattended.yml`，必要时取消正在运行的job。旧公开仓库的Variable不控制当前私有方案。
- 所有运行共用并发组，避免私有状态多写。结果即使失败也尝试提交私有状态；若超时导致无法提交，会在Actions失败记录中可见，不能当成功采集。
- source失败、未知阶段、90分钟不明确或缺训练量都显式记录。没有新数据则SKIPPED_UNCHANGED，不每天盲目调参。
- 重跑使用workflow_dispatch选择collect/train；保留旧快照、候选和诊断，不覆盖原活动工件或冻结记录。
- 当前方案无额外Deploy key/Secret，撤销workflow运行即可停止自动读写。升级固定源码commit后仍需验证一次真实运行。
