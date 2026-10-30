# 免费数据额度与跨进程限速

`runtime/data_sources/quota.py`提供标准库SQLite账本，只有明确配置的预算才允许自动回源；不读取环境密钥，不请求网络，不创建定时任务。数据库应在被所有采集进程共用的仓库外目录，例如`/workspace/WCPP/cache/quota.sqlite`。

```python
from runtime.data_sources.quota import QuotaLedger, run_with_quota

ledger = QuotaLedger('/workspace/WCPP/cache/quota.sqlite')
result = run_with_quota(
    ledger, provider='the-odds-api', account='WCPP_THE_ODDS_API_KEY',
    request=lambda: safe_client_fetch(),
    limit=verified_free_limit, period='month',
    estimated_units=verified_request_cost, min_interval_seconds=verified_interval,
)
```

调用者应先完成缓存读取和请求去重，仅真正回源才预留额度。`request`须是现有安全客户端的零参数回调。本文变量是接入示意，不是已取得免费权限或实测额度。

未配置`limit`时返回`QUOTA_UNKNOWN`，不会执行回调，不假定500credits或无限额度。预算从UTC日或UTC月开始计算，由`period='day'|'month'`明确选择。不同调用者必须使用相同已核实的预算周期和限额配置，不能以改周期或限额规避共享限制。本模块没有自动探测账号套餐；费用与响应头配额可能不属于同一周期，接入者须核对。

同provider/account的首个非空预算会锁定`period`和`limit`，后续提高/降低限额或切换日/月周期均返回`POLICY_CONFLICT`且不回源；已有旧账本会从最早显式预算记录回填策略锁。暂不提供原地策略迁移，变更须使用明确版本的新账本并由操作者衔接既有消耗，不能将新账本当免费额度重置。预留`estimated_units`必须为正整数，0不能充当无限免费请求；真正无需计额的请求由调用层明确处理，不走此计费预留。

账户参数只能是`clients.PROVIDERS`登记的环境变量名称；公共无密钥服务使用`PUBLIC`。同一服务的变量别名归入标准变量对应的同一账号，避免轮换别名重复享有额度。密钥值、自由文本错误、HTTP正文、URL或任意头字段均不会写入账本；未知账户名、非数值quota头、含凭据的头字段直接拒绝。

`QuotaLedger.reserve(...)`在`BEGIN IMMEDIATE`事务里检查预算、上一次预留的最早下次时间及429熔断后占位。多进程争抢最后一个额度时仅一个成功，拒绝不睡眠也不发网，调用者按返回`retry_after_seconds`自行调度。进程崩溃留下的预留额度仍占用；不能据“没有响应”推定请求未收费。

`settle(reservation_id, http_status=..., quota_headers=..., actual_units=..., outcome=...)`只追加结果事件。实际cost比预估高时增加账本占用；低于预估时保守不退款。接受`x-requests-last/remaining/used`、数值型`retry-after`、部分已明确命名的rate-limit剩余头；不把任意`x-ratelimit-*`当同一种单位。存在实际剩余额度时采用保守最小值，避免乱序并发响应提高已知剩余。429无有效Retry-After则熔断至当前UTC周期结束；有效重试间隔跨午夜仍有效。

`events`表仅追加，SQLite触发器禁止UPDATE/DELETE；`accounts`表是事务维护的当前状态缓存。每次记录包含UTC时间、阶段、状态、预留ID和数值费用。`history()`可检查全部事件。这是本地账本，不是防管理员篡改的外部证明，不支持跨不同机器共享本地SQLite。

软件测试涵盖多进程竞争、别名共享、限速、未知预算、实际费用补记、网络失败不退款、429及跨午夜熔断、月边界、秘密字段拒绝和历史不可改写。测试不调用真实服务，也不证明任何账号目前有额度。
