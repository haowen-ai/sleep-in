# Scheduling test cases / 定时测试用例

Status: **DESIGNED — not executed**. Tests use a controllable UTC clock; timezone conversion uses the configured IANA zone, never the browser's implicit timezone. Unless overridden, use a published SQL → Python → JavaScript workflow, enabled trigger, healthy services, and no active run. Each admitted occurrence has a unique `(trigger_id, scheduled_at)` key and node-level evidence. A simulated clock test does not establish real overnight uptime.

| ID / Priority | 前置条件与具体数据 | 操作步骤 | 必须观察到的结果 | 层级 |
|---|---|---|---|---|
| SCH-001 P0 | manual 模式 | 预览未来时间；等待调度 tick；再手动运行 | 预览为空、不自动触发；手动仅创建一条运行 | Unit/API |
| SCH-002 P0 | 每 15 分钟，anchor=2026-09-21T08:00Z；now=08:07Z | 请求未来 5 次 | 08:15、08:30、08:45、09:00、09:15，锚点不漂移 | Unit/API |
| SCH-003 P0 | 同上，now=08:15Z | 请求未来时间，再把调度时钟推进到下一 due | 预览第一条为08:30；到达due时受理一次，不多跑或漏跑 | Unit/Integration |
| SCH-004 P0 | 每 2 小时，anchor=08:00Z；now=08:01Z | 预览并tick至10:00Z | 下一次10:00；不能当成2分钟 | Unit/API |
| SCH-005 P0 | 每天07:00 UTC；now=2026-09-20T22:00Z | 预览、跨到21日和22日07:00 | 两天各运行一次，首日完成不关闭次日触发器 | Unit/Real E2E |
| SCH-006 P0 | 每天07:00、18:30 UTC | 预览并跨越两个时间点 | 同日两次，各自独立occurrence，不只保存最后一个时间 | Unit/API |
| SCH-007 P1 | times含重复07:00且顺序18:30、07:00、07:00 | 保存及预览 | 去重且按时间排序，同一时刻不重复运行 | Unit/API |
| SCH-008 P0 | 工作日07:00；now=2026-09-25周五08:00 UTC | 预览下一次 | 周一09-28 07:00；跳过周六日，未启用节假日日历不擅自跳工作日 | Unit |
| SCH-009 P0 | 每周一/三07:00；weekday协议周一=0 | 预览09-20之后时间 | 09-21、09-23、09-28；UI周一和服务端0保持一致 | Unit/UI |
| SCH-010 P0 | 每月31日07:00 UTC；after=2026-04-01 | 预览下一次 | 05-31 07:00；不改成04-30，也不失败死循环 | Unit |
| SCH-011 P0 | 月末07:00 UTC；after=2026-02-01 | 预览两次 | 02-28、03-31各07:00 | Unit |
| SCH-012 P1 | 月末07:00 UTC；after=2028-02-01 | 预览 | 2028-02-29 07:00，覆盖闰年 | Unit |
| SCH-013 P0 | 一次性2026-09-21 07:00 UTC | 连续两个tick跨过到期时刻 | 仅受理一次，触发器完成；流程本身仍可手动运行 | Unit/Integration |
| SCH-014 P0 | 一次性已过期；补跑默认关闭 | 保存启用，随后tick | 提示已过期，不立即无声执行；不产生未来下一次 | API/UI |
| SCH-015 P0 | 每日07:00，start=09-21T07:00Z，end=09-22T07:00Z | 预览并运行至09-23 | 两端包含：21/22日各一次，23日不运行 | Unit/API |
| SCH-016 P0 | end早于start | 保存计划/发布启用 | 可读校验错误，不触发任何任务 | API/UI |
| SCH-017 P0 | 同一当地07:00，America/Chicago与Asia/Shanghai | 以2026-09-20T00:00Z请求预览 | Chicago 09-20T12:00Z；Shanghai 下一次09-20T23:00Z（当地21日） | Unit/API |
| SCH-018 P0 | Chicago 每天02:30；after=2026-03-08T00:00Z | 预览跨DST春季跳时 | 跳过不存在的03-08 02:30；下一次03-09T07:30Z | Unit |
| SCH-019 P0 | Chicago 每天01:30；after=2026-10-31T12:00Z | 预览并tick跨回拨两次01:30 | 11-01只在06:30Z执行一次；07:30Z不重复；次日07:30Z执行 | Unit/Integration |
| SCH-020 P0 | UI语言英文、时区Chicago、已保存任务 | 切中文、刷新页面、再切英文 | UTC next_run、当地时间和schedule内容完全不变 | UI/API |
| SCH-021 P0 | 两个工作进程同时tick到同一due | 同时提交调度请求 | 只有一条权威run，节点副作用计数为1 | DB concurrency |
| SCH-022 P0 | due前1秒/正好due/晚1秒 | 分别tick，重复调用 | 之前0条，正好due受理1条，之后重复tick不增加同occurrence | Unit/Integration |
| SCH-023 P0 | 上次运行持续90秒，每分钟一次，默认跳过重叠 | 跨过第二次触发 | 记录一次skipped及overlap原因；不创建第二个并行写操作 | Integration |
| SCH-024 P1 | bounded queue容量2、已有运行+2待执行 | 再来一个触发，随后释放前序 | 不无限排队；超限记录原因；待执行按顺序且保留受理快照 | Integration |
| SCH-025 P0 | 暂停触发器，后台仍常驻 | 跨due，再恢复并查看下一次 | 暂停期间不执行；恢复默认只安排未来，不爆发补跑 | API/Integration |
| SCH-026 P0 | 已受理v1运行，之后发布v2 | 执行队列、等下个触发 | 旧run用v1；选跟随最新的下次用v2；固定版本触发器继续v1 | Integration |
| SCH-027 P0 | 离线错过07:00/08:00/09:00，09:10恢复，默认跳过 | 恢复调度 | 不连跑三次；有错过记录，下一未来occurrence有效 | Integration |
| SCH-028 P0 | 同上，启用只补最近一次，宽限2小时 | 两个进程同时恢复 | 只补09:00一次，不补07/08；使用原定occurrence键 | DB concurrency |
| SCH-029 P0 | 09:00错过，11:00:01恢复，宽限2小时 | 请求自动补跑 | 已超宽限，记录missed，不执行 | Unit/Integration |
| SCH-030 P0 | 进程在受理提交后崩溃，尚未执行 | 重启服务并重复tick | 恢复同一run，不重新受理同occurrence | Integration |
| SCH-031 P0 | SQL写入已提交但成功回调丢失 | 重启，重试恢复 | 标记结果不确定/需检查，不自动重复写入 | Fault injection |
| SCH-032 P0 | n8n或运行环境不可用 | 启用/到期执行 | 阻止就绪或记录明确失败；不能因HTTP202而显示成功 | API/Integration |
| SCH-033 P0 | 流程草稿未发布 | 尝试启用每日计划 | 校验拒绝，草稿保留，无调度受理 | API/UI |
| SCH-034 P0 | 定时运行执行中，用户保存新时间 | 等待当前执行结束并再次tick | 当前运行参数/版本不变；未来按新时间执行，不残留旧due重复触发 | Integration |
| SCH-035 P0 | 系统时钟向前/向后跳10分钟 | 连续tick与去重检查 | 已执行occurrence不重演；向前错过按统一补跑策略；不产生负等待死循环 | Unit/Integration |
| SCH-036 P0 | 切换Mac系统时区，流程明确使用Chicago | 查看预览/执行 | 流程时区仍Chicago；不能跟随系统而静默改时间 | Unit/Manual Mac |
| SCH-037 P0 | invalid时区、every=0/-1/1.5/true、25:00、空weekday、monthday32 | 每项单独发API再通过UI复测 | 每项返回具体校验错误且无500/无任务触发 | Parameterized API/UI |
| SCH-038 P0 | 新API schedule.kind=cron | 保存/预览/发布 | 明确拒绝；旧v1迁移记录可保留原值但不提供新Cron编辑器 | API |
| SCH-039 P1 | start/end为无时区且落在DST空洞/回拨 | 从表单提交并检查转换 | UI根据选定时区按跳过/首次规则解析并预览；API持久化明确时刻，不用宿主时区 | API/UI |
| SCH-040 P0 | 同流程两个独立触发器各有参数/版本选择，时间错开且第一条完成后第二条才到期 | 两者依次到期，检查run | 记录正确trigger_id、scheduled_at、参数快照；不得误去重为同一个触发器 | Integration |
| SCH-041 P0 | 真正n8n可用，隔离的1分钟测试计划 | 开始前记录run数，真实等待到due，查看各节点及下载结果 | 自动生成一次SQL→Python→JS执行；完整3行输入、count=3、total=30.75；真实n8n证据 | Real E2E |
| SCH-042 P0 | Mac插电过夜/电池短测分别准备相同任务 | 实际锁屏并等两次触发，记录墙上时间、电源与节点时间 | 两次分别执行；禁止把改数据库due时间或模拟时钟测试写成过夜通过 | Manual Mac |

## Evidence rules

- Unit tests use explicit instants; integration tests prove transaction/state transitions; real n8n tests prove actual orchestration. Report all three separately.
- Preview verification must compare expected fixed timestamps, not call the same production calculator to construct expected values.
- Exactly-once admission is distinct from exactly-once external side effects. Unknown external outcomes require reconciliation, never an unsupported guarantee.
- A published version is a graph/code/runtime snapshot. Schedule changes affect future admissions; they do not rewrite history.
