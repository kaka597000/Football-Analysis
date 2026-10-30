# 定时结果邮件

2026-10-30用户授权：向此前指定的本人邮箱发送定时任务结果。无需用户电脑运行。

**状态：代码/工作流模板已准备，未远端安装，未实际发信。** 当前没有SMTP授权码；当前云端GitHub GH_TOKEN也返回HTTP401，不能将本地提交冒称已推送。采集训练的独立GitHub工作流仍沿用原部署，不因本模块准备而改变。

## 默认内容和频率

- 每日训练检查完成后发送一封汇总：时间、源码commit、合格训练行数、队列训练/未变更/不足/失败状态、逐队列历史Top1候选/基线对照，附私有结果链接。
- 该汇总包含过去24小时成功采集轮数和新增赛果观察量；观察量不冒充训练净增比赛数。
- 采集或训练失败、取消、超时、成功但本次程序记录缺失时发送异常通知；不把上一轮成功结果放到失败通知里充当本轮结果。
- 成功采集不每6小时单独发信，避免一天多封常规邮件。候选不自动晋级，历史诊断不称未来实盘准确率。
- 邮件不附原始数据、完整矩阵、模型或凭据，仅必要汇总和需登录的私有链接。

## 凭据和配置

仅支持QQ邮箱作为发件人，使用`smtp.qq.com:465`、SMTP_SSL及系统CA验证；收件人可以与发件人相同。QQ邮箱设置中启用SMTP并生成专用授权码；**不要使用QQ登录密码，不要把授权码发到聊天或提交到git**。

私有数据仓库Actions Secret名称：`WCPP_SMTP_PASSWORD`。收发地址和策略只写私有`config/notifications.json`，示意：

```json
{
  "enabled": true,
  "repository": "kaka597000/kaka597000-Football-Analysis-Data",
  "sender": "your-account@qq.com",
  "recipient": "your-account@qq.com"
}
```

没有授权码时返回`NOT_CONFIGURED_SMTP_PASSWORD`，不尝试登录、不发信。缺失或失败时不会将授权码、SMTP提供方错误正文写进日志。

## 云端安装及验证

1. 恢复本任务GitHub连接；测试并推送公开代码，取得完整commit。
2. 私有数据仓库安装`.github/workflow-templates/email-private.yml`为`.github/workflows/email.yml`，将`__WCPP_CODE_REF__`替换为已推送commit。邮件通知用`contents: read`权限，无需写仓库。
3. 原私有采集/训练workflow也更新到该已验证commit，以便程序状态附真实`github_run_id`和`github_run_attempt`；不修改训练参数。旧运行记录保持，不补写ID。
4. 写入私有地址配置，用户通过GitHub网页设置上述Secret；当前连接没有Secrets管理能力时不得自行复制其他凭据替代。
5. 手动dispatch邮件workflow，只发送明确的连通测试邮件，不宣称发生了新训练。SMTP返回接受只记`SMTP_ACCEPTED_INBOX_UNCONFIRMED`；收件箱实际收到还需用户确认。
6. 真实执行一轮训练或采集异常后核验自动通知。未验证前不登记已启用。

主工作流超时可能无法执行自身末尾步骤，因此通知是独立`workflow_run: completed`触发，监听指定workflow、main分支和同一仓库；源码固定commit，不执行其他分支任意脚本。只有本次ID和attempt匹配的状态可进入结果摘要。手动重跑通知可能重复发送，不承诺SMTP的exactly-once投递。

通知不新增定时采集频率或付费邮件服务。每次通知job最多2分钟，会额外使用GitHub Actions额度；每天5次主任务完成最多触发5次通知检查，31天按超时上限增加310分钟，常规采集成功时脚本不连接SMTP。

关闭方式：私有`config/notifications.json`设`enabled=false`，或禁用私有`email.yml`。删除SMTP Secret可撤销发信凭据，不影响采集/训练。

## 本地预览与测试

`python -m runtime.automation.email_notify --state-dir PRIVATE_STATE --config PRIVATE_CONFIG --event EVENT.json --preview-out NEW.txt`仅生成预览，不发送。`--test`生成明确的连通测试内容；预览文件不可覆盖。

模块测试验证北京时间、逐队列指标、运行/attempt对应、超时无状态时不套旧数据、单一收件人、头部注入阻止、缺授权码不建连接、SMTP错误不泄露提供方正文。mock发送测试不是实际QQ送达证明。

本次全目录19套、315项本地测试通过；邮件模块9项真实逻辑测试使用模拟SMTP，无实际QQ登录或发信。源码/凭据扫描和workflow模板解析通过。远端部署与收件测试尚未完成。
