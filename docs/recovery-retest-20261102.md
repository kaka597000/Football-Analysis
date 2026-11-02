# 2026-11-02 云端恢复后复测

北京时间11月2日。运行环境实际恢复为running/connected；这不等于历史发布操作状态已独立核实。详细API响应、数据库检查、浏览器DOM和截图留在云端私有运行目录 `/workspace/WCPP/reports/full-retest-20261102/`。

## 已验证

- 原工作区代码、活动注册表及研究候选仍在。统一库1,031,159,808字节、Canonical库56,631,296字节，均以mode=ro/query_only执行quick_check并返回ok；未修改数据库，未据此宣称赛事标签完整。
- 21队列42份登记工件均存在且实际SHA256匹配。英超真实未来赛程通过默认入口执行current/test，分别输出51×51非负归一矩阵和单队进球分布。冻结读写在独立测试目录验证，不发布分析、不进入业务KPI；其他队列本轮没有逐场真实未来推理。
- 恢复前源码为366cd94且比旧远端多3提交；远端已另有3提交。保留双方改动，在checkpoint/recovery-retest-20261102合并，处理AGENTS/CHANGELOG/project-goals冲突。20组376项本地测试通过、0跳过；270源码文件静态检查0发现。合并提交62b7405已推main并核对远端同SHA，GitHub CI两次实际success（41367405596、41367403966）。保留研究隔离规则，不更换活动工件。
- GitHub身份、两个仓库读取恢复。源码main实际推送成功；私有仓库建立checkpoint/cloud-recovery-20261102引用并读回相同SHA，验证写入权限，私有main未改变。
- football-data.org赛程实测HTTP200/91条比赛；ESPN/FotMob公共JSON可访问。具体字段覆盖仍按逐接口返回核验，HTTP200不等于全字段可用。

## 浏览器实际结果

三个GitHub工具已在 `/workspace/WCPP/tools/browser-retest-20261102/` 隔离安装：agent-browser 0.41.1、browser-use 0.13.14、Playwright MCP 0.0.84，复用系统Chromium。三者均能完成example.com导航；MCP实际完成stdio initialize/tools-list/browser-navigate/browser-snapshot；Browser Use使用BrowserSession.navigate_to公开接口。完整LLM驱动Agent未验证、未调用付费模型。代理与TLS验证保留；本地CDP仅进程范围允许环回直连。

雷速旧路径404是无效路由；官方live.leisu.com一次普通Playwright导航读到比赛ID、对阵、比分及分钟，详情触发滑动验证，首发和盘口未取得。随后三种工具同站对照：agent-browser代理超时，MCP HTTP503/代理超时，Browser Use滑动验证；三者该轮均未读到比赛字段，不能把普通Playwright的一次成功归给三个工具。新球官网经代理隧道失败；Understat隧道403、Transfermarkt405；AsianOdds事件端点404；OddsPortal虽HTTP200，实际DOM无赛事赔率。没有绕过验证，也没有证明新浏览器已解决关键数据缺口。

## 未恢复或有条件可用

- 当前执行进程未绑定5Dollar、API-Football、apifootball.com、TheOddsAPI、InferSports所需凭据，因此本轮NOT_CONFIGURED，未发鉴权请求；不能沿用旧测试结论或认定密钥失效。个人凭据需在当前环境安全设置中重新确认绑定，不在聊天提供。
- 私有Actions已启用，但最近run#13/#14在韩K采集步骤失败：API-Football联赛解析未确认K League 1，进程exit2、0场入库；不能把错误身份当韩K训练数据或屏蔽错误让任务变绿。本轮只读诊断、未改任务或触发新运行。
- 最近成功训练为北京时间11月1日17:37:53，9262条accepted rows，21队列中14训练候选、6未变、1样本不足；候选未自动晋级，非当前活动模型已刷新。
- 邮件workflow未部署，当前SMTP授权变量未绑定，未发信。本轮未新建定时任务，既有09:00复盘不能追认成功。

## 使用边界

可运行项目和现有双模型，实际分析仍需逐场核对赛程、首发、xG/xGA和真实盘口；缺项保留待定。工件可加载、测试通过、浏览器能启动均不证明未来命中率提高。
