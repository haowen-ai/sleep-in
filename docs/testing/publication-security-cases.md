# Publication, isolation and recovery cases / 发布、权限与恢复

Status: **DESIGNED — not executed**. Use synthetic data, two local test accounts (administrator/operator), isolated app state and a local stub endpoint that counts calls. Never use production database writes or real notification recipients.

| ID / Priority | 前置条件 / 数据 | 操作 | 预期结果 | 层级 |
|---|---|---|---|---|
| PUB-001 P0 | 节点尚无连接/必需参数 | 保存草稿，再发布 | 草稿可保存并列出错误，发布失败并定位节点 | API/UI |
| PUB-002 P0 | v1已发布，run在队列；修改源代码/绑定/参数 | 保存并发布v2，执行原run | 原run仍使用v1全部快照，不能混用v2字段 | Integration |
| PUB-003 P0 | 草稿与发布内容不同 | 点击“试运行草稿”再正式运行 | 两条run标识明确、分别执行各自快照；草稿测试不改变发布版本 | API/E2E |
| PUB-004 P0 | 有SQL写入上游，选择下游单节点测试 | 输入显式样例后测试下游 | 只运行选中节点，不重跑上游写操作；记录样例来源 | API/UI |
| PUB-005 P0 | 输出示例来自旧版本 | 编辑上游后打开映射 | 样例标记过期；不能自动把历史值当本次实际输入 | UI/Integration |
| PUB-006 P0 | A→B→A或自环 | 尝试连线/发布 | 阻止环并指出边，发布不能绕过前端校验 | Unit/API/UI |
| PUB-007 P0 | 缺失Java编译器/SQL驱动 | 发布对应节点 | 指出缺失环境，状态不可用，不能仅图标存在就发布成功 | API/Runtime |
| PUB-008 P0 | C/Java编译错误 | 发布再尝试运行 | 编译日志可见、发布失败；旧已发布版本仍可运行 | Runtime/API |
| PUB-009 P0 | 已发布节点的依赖版本更新 | 更新环境，运行旧版本，再重新发布 | 旧版本引用保持；新版本重建后才使用更新依赖 | Runtime integration |
| PUB-010 P0 | 运行中取消，脚本会每秒追加本地计数 | 取消并等最终状态 | 确認子进程组终止，计数不再增加，下游不启动，再显示cancelled | Real subprocess |
| PUB-011 P0 | 节点超时2秒，源代码等待10秒 | 运行 | 节点timed_out；终止子孙进程；必需下游不执行；不得仅UI改状态 | Real subprocess |
| PUB-012 P0 | 已知失败、配置最多2次重试 | 使前两次失败第三次成功 | 总尝试3次，有每次记录与退避；下游仅消费最后成功结果一次 | Fault injection |
| PUB-013 P0 | 网络写操作结果未知 | 让请求提交后失去确认 | 默认不盲目重放；记录需检查；人工重试也提示可能副作用 | Integration |
| PUB-014 P0 | 重复n8n启动、重复节点回调 | 同时请求同run/node/attempt | 只有一个执行租约、一次真实调用，其余返回同记录 | DB concurrency |
| PUB-015 P0 | succeeded节点迟到running事件 | 依次提交终态后旧事件 | 终态不回退；事件顺序和去重有证据 | Unit/API |
| PUB-016 P1 | 失败通知配置指向本地收件stub | 正式run最终失败；中间重试失败；草稿测试失败 | 正式最终失败发一次；中间重试不刷屏；测试默认不发送 | Integration |
| PUB-017 P1 | 业务成功，通知stub返回500 | 运行及重试通知 | 业务仍成功；通知单独失败，不重新执行业务节点 | Integration |
| SEC-001 P0 | 未登录或过期会话 | 请求流程、日志、结果下载与连接API | 401，无数据或路径泄漏 | API |
| SEC-002 P0 | operator账号 | 尝试改源码、连接、环境、发布或执行草稿 | 拒绝；允许被授权的已发布运行与结果读取 | API |
| SEC-003 P0 | 缺少/错误CSRF，跨Origin POST | 修改流程、触发运行 | 403，无状态变更或副作用 | API |
| SEC-004 P0 | 本地默认模式 Host为外部域、Forwarded头、非loopback peer | 请求bootstrap/内部触发 | 拒绝；初始凭据不经代理或非本机返回 | API |
| SEC-005 P0 | 连接秘密为随机synthetic token | 列表、详情、导出、日志、失败栈、前端网络响应 | 不含明文秘密；运行所需值仅在限定执行路径解密 | API/Integration |
| SEC-006 P0 | SQL参数值为 "x'; DROP TABLE orders;--" | 使用绑定查询 | 按普通值查询，表仍存在；不拼接执行攻击字符串 | SQL integration |
| SEC-007 P0 | SQL默认只读连接，DELETE/CTE写入/多语句 | 执行每种操作 | 驱动/会话/权限拒绝写入，原数据不变，不能靠SELECT前缀判断 | SQL integration |
| SEC-008 P0 | 写入模式且有写权限，事务第2句失败 | 执行事务再读取数据 | 该节点可回滚事务无部分提交；方言隐式提交另行说明，不承诺跨节点回滚 | Real dialect DB |
| SEC-009 P0 | artifact路径为 ../secret 或指向外部的软链接 | 产出、预览、下载 | 拒绝越界，不读宿主外部文件 | Filesystem/API |
| SEC-010 P0 | 将runA路径与runB的artifact id拼接，另准备合法runA制品作对照 | 登录同一有权限用户，访问错误组合与合法组合 | 错误归属组合拒绝，合法组合可下载；另一个run本身不等于未授权 | API |
| SEC-011 P0 | 前端节点名为HTML/script payload，日志含HTML | 打开画布、列表、日志 | 作为文本呈现，不执行HTML/脚本 | Browser |
| SEC-012 P0 | 初始本地账号、改密后旧会话 | 改密，刷新登录页，以旧会话访问 | 初始卡消失，旧会话失效；现有安装不重置账户 | API/UI |
| SEC-013 P0 | 使用只读JSON文件/安全argv的Shell样例，源码不调用eval或拼接执行 | 参数含空格、引号、分号、$() | 字符原样到达脚本；框架不额外执行命令替换，不宣称阻止用户自己编写的危险源码 | Runtime |
| OPS-001 P1 | 导出有连接引用的工作流 | 导出后在空工作区导入 | 图/映射保持、触发默认关闭，要求重绑连接，不包含秘密和运行数据 | API/Integration |
| OPS-002 P0 | v1 Python任务与简单每日计划 | 执行迁移后检查并手动启用 | 源码/参数/时区/历史关联保留，变为单Python流程，旧调度与新调度不双跑 | Migration |
| OPS-003 P0 | v1无法表单表达的Cron计划 | 迁移 | 新触发器保持禁用并请求重新选择；不静默改执行时间 | Migration |
| OPS-004 P1 | 超保留期日志、运行中制品、重试仍依赖制品 | 执行清理 | 只清符合条件数据；运行/重试依赖保留；清理结果可审计 | Integration |
| OPS-005 P0 | 备份含工作流/环境/连接引用 | 备份、恢复到独立目录 | 校验图和版本，默认计划关闭；秘密重填；旧目录不被覆盖 | Recovery |
| OPS-006 P1 | 10/25/50节点含分支图，固定机器配置 | 重复运行和测量编辑响应、内存、耗时 | 结果正确、无死锁，记录环境与实际数值；不凭用例存在宣称性能通过 | Measured E2E |
