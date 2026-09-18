# Test contract decisions / 为测试固定的行为

These decisions precede implementation and resolve ambiguities against the PRD. They are not implementation evidence. The test suite must not silently narrow the PRD to fit an incomplete API.

| Reference | Fixed behavior / 固定行为 | Test consequence |
|---|---|---|
| C01 | 连线只决定顺序/激活，inputs独立决定传入对象；空inputs合法 | 不使用上游数据的节点仍受默认依赖约束，不继承上游输出 |
| C02 | 可选映射有独立optional开关与类型化default；missing与present-null不同 | missing时用显式默认值；null/false/0/空字符串/空数组不替换 |
| C03 | 默认edge.required=true；显式required=false才容忍该来源失败；条件不成立造成的正常skipped不是失败，不要求将边改为可选。引用该来源的字段仍须标optional并设置default，或完全不引用。join策略等待所有前驱终态后判定，不因any模式提早读未完成的输入 | 可选字段本身不能忽略必要前驱失败；被明确容忍的实际失败导致总体partial；正常未选中分支不降低成功状态；必需链路失败总体failed |
| C03b | all等待所有被激活的必要前驱成功；any要求至少一个激活前驱成功。所有前驱skipped则本节点skipped。没有入边的节点是根节点，不因输入为空而跳过 | 分支不选中时发skipped完成标记，合并不永久等；测试失败/跳过/成功组合 |
| C04 | PRD中的多个具名trigger不删减；trigger有id/name/kind/params/schedule/timezone/enabled/publication选择，occurrence去重键包括trigger_id | 单个schedule字段仅作兼容便利入口；多trigger场景必须独立实现再验收 |
| C05 | 运行环境检测、准备/安装、版本冻结是不同能力；发布记录runtime版本/平台/依赖摘要/产物校验值 | detected标识不能冒充已验证支持；环境变化触发新版本，不修改旧run |
| C06 | output schema先支持object/array/string/number/integer/boolean/null、properties/items/required；金额等格式用metadata说明。映射路径相对data，标准格式为键/非负数组索引的token列表，兼容无歧义的点路径简写；默认数组整传，显式索引可选单元素；无通配符或隐式逐行执行 | 数组元素计算由脚本完成；字段缺失/类型不符定位node_id和field；样例含run_id/version_id/produced_at，版本不一致标过期 |
| C07 | 复制节点生成新ID；源码/config/inputs深拷贝；保留对应入边，不复制出边；后续节点不自动改绑到副本 | 拷贝后改副本不影响原节点，原消费者仍指向原ID；重复/undo可确定验证 |
| C08 | HTTP错误统一detail:{code,message,node_id?,field?}；草稿validation_errors同样携带code/message/node_id?/field? | 可定位本节点字段，用户能看到原因；不吞成generic，也不暴露服务端秘密 |
| C09 | 整图测试、单节点测试、历史样例、重试与制品分页都是独立契约 | 未有对应接口不能显示可用操作；必须添加接口及测试，不能仅用整图测试宣布全部完成 |
| C10 | 各语言/方言支持声明需真实运行记录 | 没环境记BLOCKED_ENV；mock和图标不算验证 |
| C11 | 初始凭据卡只显示服务端bootstrap.local_account；改密后清理内存状态并重新bootstrap | 前端不猜默认值，不在旧会话中继续显示凭据；账号初始化不触碰旧用户 |
| C12 | 通知、连接编辑/密钥轮换、源码项目导入与工作流导出保留PRD范围 | 各操作补API契约与权限测试后才能交付，避免静态假按钮 |

## Join truth table / 合并状态表

All cases wait for every predecessor's terminal marker. A condition-false/unselected source is inactive; its skipped marker completes waiting without becoming an actual failure. Inputs bound to that skipped source must be optional/defaulted or omitted, independently of control edges.

| Activated/terminal predecessors | all | any | Final workflow outcome when remaining required work succeeds |
|---|---|---|---|
| A succeeded, B normal branch skipped | Run with A; missing B binding must use explicit fallback or be omitted | Same | succeeded |
| A and B both normal branch skipped | Skip join | Skip join | No failure introduced by these skips |
| A succeeded, B failed, B edge required | Block join | Block join; any cannot override required failure | failed |
| A succeeded, B failed, B edge explicitly optional | Run with A and declared fallback/no B binding | Same | partial |
| A and B both failed, optional edges | No successful activated input: do not run | Same | failed |
| A succeeded, B still running | Wait | Wait; no first-winner race | Not terminal yet |

## Additional data decisions

- Python/JavaScript `main(inputs)` helpers always wrap the returned plain data object. Business fields named `schemaVersion` remain business data. Authors who need the full envelope write `SLEEP_IN_OUTPUT_FILE` explicitly; the adapter never guesses an envelope from a single field name.
- SQL write nodes may declare an explicit list of statements and bound parameters, executed within that node's transaction. Do not split raw SQL strings on semicolons. Query mode remains database-enforced read-only. A dialect's implicit-commit operations cannot be presented as rollback-safe.
- Nullable JSON Schema uses a type union such as `["string","null"]`. Query SQL outputs omit `affectedRows`; write outputs include the driver-verified count (or explicit null when unknown), never invent zero from an unknown count.
- Conditions validate operator and operand types. `truthy` accepts a boolean; missing paths and wrong types are errors, not false conditions. Unknown JSON Schema keywords are rejected unless implemented; nullable type unions are supported explicitly.

## Execution controls

A cancelled/timed-out node is not a successful dependency. An optional failure can permit the configured consumer to continue, but cannot convert the failed node into success. Retry counts and attempts remain visible. Unknown external side effects are not automatically replayed merely because a connection is optional.

Pause scheduling and stop the background service are separate operations. AC/battery transition is not a stop event. A user-requested full stop during active work either drains or explicitly cancels; it must not show stopped while side effects continue.

## Follow-up work

Before coding an affected interface, add its exact route/schema from these rules to WORKFLOW-API.md and a failing automated test. The existing draft API is intentionally incomplete; tests are the constraint on completion, not permission to omit the remaining features.
