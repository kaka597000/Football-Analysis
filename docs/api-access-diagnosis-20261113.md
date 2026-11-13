# API-Football / TheRundown 403 实查（2026-11-13）

结论：两家目前仍未恢复获取数据。已确认收到带 Cloudflare 品牌的 HTML 403，具体触发规则未知；不能据此判定密钥失效、套餐不足、固定出口 IP 被封或代理凭据注入错误。以下时间均为北京时间。

| 服务 | 实际 API 探针 | 实测时间 | 结果 |
|---|---|---|---|
| API-Football | 官方主机 `GET /status`，`x-apisports-key`，当前 `apifootball` 绑定 | 10:08:05 | 403 HTML，`Attention Required! \| Cloudflare`；Ray `a59a3a02986e2cc8-DFW` |
| TheRundown | `GET /api/v2/markets`，`X-TheRundown-Key`，当前 `therundown` 绑定 | 10:02:43 | 403 HTML；Ray `a59a32243e0e1c16-DFW` |
| TheRundown 无密钥对照 | 同端点 | 10:02:48 | 同类 403 HTML；Ray `a59a32455f661c16-DFW` |
| TheRundown 固定无效值对照 | 同端点 | 10:02:53 | 同类 403 HTML；Ray `a59a326689f81c16-DFW` |

API-Football 的单次 API 探针触发保守挑战检测，另两项控制未发出。检测标志并不是已经人工确认存在可交互验证码。此前官网 FAQ 的 403 属于另一主机，单独保存，不充当 API 测试。所有真实探针使用 Python、正常 CA/TLS 和继承代理，不跟随重定向；没有保存错误 HTML 或密钥。

环境 revision 4 显示两项凭据 ready、实际 API 目标主机已在各自绑定的 allowed_domains 中、网络策略 unrestricted/enforced。此为配置观察，不验证注入后的凭据内容，也不能替代远端账户和安全日志。

## 查到的方案与适用边界

1. [API-Sports 官方 v3 规范](https://api-sports.io/public/documentations/football-v3.yaml)确认主机、GET 和 `x-apisports-key`。按官方契约实测仍为 HTML 403；换 Python 库没有已证实的修复依据。旧资料中的 IP/domain 限制出自 widget 密钥保护段，不能认定本次服务端调用就是该设置导致。
2. [TheRundown 鉴权文档](https://docs.therundown.io/authentication.md)和[官方 Python SDK](https://github.com/TheRundown/therundown-python)确认 `therundown.io`、`X-TheRundown-Key`、不自动重试和拒绝重定向。已用推荐的 V2 受保护端点实测，仍受阻。`/api/v2/sports`、`/api/v2/affiliates` 为公共目录，成功也不证明密钥有效。
3. [TheRundown 错误说明](https://docs.therundown.io/errors.md)描述 JSON 401 为鉴权错误、JSON 403 为权限/历史窗口等限制。本次未取得这些应用错误。公开 GitHub 检索未找到匹配且验证有效的 Cloudflare 403 修复；搜索无结果不证明不存在其他方案。
4. [Cloudflare 官方 403 说明](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-403/)列出 WAF、Security Level、DDoS、Browser Integrity Check 等可能来源，不能从通用阻断页区分具体一项。官方站点阻断页建议联系站点所有者并附 Ray ID。已整理支持材料，未向任何人发送。

## 已执行的仓库修正

- 统一客户端新增安全 `failure_kind` 分类；保留原 `HTTP_ERROR` 契约，区分 Cloudflare HTML、普通 HTML、JSON 鉴权、JSON 套餐/权限和 429。仅输出固定分类，不持久化错误正文。单有 CF-Ray 不判定 Cloudflare 阻断。
- 将 TheRundown 官方 V2 接入现有只读客户端、密钥脱敏和参数检查；默认受保护探针为 markets，公共目录不冒称鉴权成功。原批处理 401/403/429 停止逻辑保持。未新增定时任务或自动启用服务。
- 更新数据源登记，将 API-Football 10 月 12 日的成功及 Free 套餐信息保留为带日期的历史观察；当前状态改为受阻、鉴权及套餐未验证。补充 TheRundown 当前实测状态。活动模型注册表未改变。

这些是诊断与接入修正，不是解除远端 Cloudflare 限制。没有关闭 TLS、轮换出口、伪装浏览器、转发密钥或部署挑战绕过代码。

## 尚缺的条件与恢复验收

需要服务商根据上表请求时间、路径和 Ray ID 查明拦截规则，并确认当前账户是否另有调用来源限制。当前会话没有可用的已登录服务商后台或修改其防火墙规则的接口，无法执行供应商侧解除。尚未确认稳定出口 IP，不填写猜测的 IP/网段。

只有完成明确的服务方修正后再做单次受保护端点验收；收到无应用错误的 JSON 后，进一步实测目标赛事、赛季与盘口权限。成功取得 markets/status 目录也不证明获得足球比赛、首发、xG 或真实新鲜盘口。未成功前继续标记缺失，由已验收的其他来源提供数据，不制造可用状态。

完整逐次日志、搜索记录、来源摘录、测试回执和未发送支持材料长期保存于[数据仓库](https://github.com/kaka597000/kaka597000-Football-Analysis-Data/tree/main/workspace-updates/20261113-api-403-diagnosis)。恢复不依赖短期 Actions 下载链接。
