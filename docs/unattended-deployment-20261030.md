# 无人值守云端部署验证

北京时间2026-10-30。状态：**DEPLOYED_PRIVATE_ACTIONS_VERIFIED**。用户电脑无需安装、下载或保持开机。

## 执行身份

- 公开源码：`kaka597000/Football-Analysis`。
- 实际私有状态：`kaka597000/kaka597000-Football-Analysis-Data`，已独立核查`private=true`。
- 后台固定源码commit：`0741bfbb5e53f2173a4b26a58146cbf5f95f9d68`。本报告之后的文档提交不改变该计算版本。
- 私有workflow：`.github/workflows/unattended.yml`，GitHub报告`active`；私有状态checkout明确`ref: main`，读取排队前驱已保存的最新状态。
- 私有训练持久化提交：`53858045760148181fcab8347efc6394a6154a70`，已从远端拉取验证。
- 旧公开仓库unattended workflow已设`disabled_manually`，Python测试CI保留。
- 无专用Secret、部署密钥或用户PAT。仅用私有工作流自身的短期GITHUB_TOKEN保存本仓库；没有复制当前云端注入凭据。

## 实际远端运行

|运行|北京时间|实际结果|证据|
|---|---|---|---|
|首次采集|11:01:34–11:02:16|100请求、96成功，42条新增赛果观察，2条隔离；达到请求上限，非全覆盖|[Actions 40994999961](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/actions/runs/40994999961)|
|修正后采集|11:04:34–11:05:10|85请求、81成功，再新增18条赛果观察；盘口原文快照已保存，覆盖仍非全量|[Actions 40995289325](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/actions/runs/40995289325)|
|修正后训练|11:05:31–11:05:34|9181条合格记录，0训练行隔离；20候选完成，1队列样本不足跳过；保存步骤通过|[Actions 40995292638](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/actions/runs/40995292638)|

以上区间是程序记录的计算开始/完成时间，不包含runner启动、依赖安装和git保存。三份运行状态已在私有`runs/`实际取得，不是仅查看绿色标记。

第一次训练[40995069900](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/actions/runs/40995069900)计算完成但推送失败：排队dispatch携带旧event SHA，默认checkout读取75b5c20，而采集已写入新的main。改为显式读取`ref: main`后重新同时排队采集和训练，两次均成功保存；未force push，未把失败运行冒充已持久化。

## 数据和模型边界

- 9127条初始追溯种子，21队列；种子实测SHA256：`897ca1edf19633db8f300a98f487b2c4c08b826f6a06ebdb0a1863d91bb3d652`，与导出及远端字节一致。
- 两轮合计60条新增结果观察，经去重及时间排除后，训练总量9181，净增54；当日/未来日期排除1条，不是把60条都称为新增训练比赛。
- 日职联该种子仅82条，低于120条训练门槛；这是本次后台导出子集的缺口，不代表整个项目日职联数据库仅82场。
- 20份候选工件均独立重算SHA256，与私有索引一致。训练固定参数统计候选，诊断是追溯时间切分，不是未来冻结业绩。
- 后台未训练分钟状态效应、深度学习或真实五角色LLM；没有自动收齐所有xG/xGA、伤停、首发。盘口快照报价时间未知，未把抓取时间冒充报价时间，也未用作训练特征。
- 当前活动注册表SHA256仍为`7c53ac2f7b3b5c856beb6590c274b006f2cdc8d4c49625fe6beef16b2e9a1590`，与导出时一致；没有修改当前/test或原冻结记录。Canonical原库未被后台写入。
- 启动后台不证明比分、胜平负或大小球准确率提高。候选不自动晋级。

## 定时、资源与版本管理

采集每天北京时间02:17、08:17、14:17、20:17；训练检查每天10:37。每job最多5分钟，单轮采集150秒/100HTTP上限，免费公开来源，CPU单线程。GitHub调度可能延迟，额度或仓库设置也可能导致任务停止。

使用账户私有Actions额度，31天5轮/天的超时上限775分钟，额外手动运行与其他项目另计。当前连接无法读取账户余额或计费设置；未购买订阅、增加付费预算、启用付费/GPU runner或收费API调用。其余操作与停止方法见[运行规范](unattended-operation.md)。

306项本地测试通过；[源码CI 40994952980](https://github.com/kaka597000/Football-Analysis/actions/runs/40994952980)成功。新增检查覆盖预算实际传入、种子哈希失败禁止采集、无有效响应不可标成功、异常正文不进运行记录；workflow YAML也已解析核查。

数据与候选按私有git追加保存；源码、模板及规范同步公开git。升级后台计算版本必须先测试和推送，再更新私有workflow固定commit并真实验证。停止时禁用私有仓库unattended workflow；无需更改用户电脑。
