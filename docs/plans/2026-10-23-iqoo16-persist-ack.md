# 2026-10-23 主机持久 ACK 最小合同

首版只覆盖后续接入的 D12 购买提交边界。主机使用标准库 JSON 小文件；不改变 USB session/heartbeat，不增加 HTTP 服务或数据库。当前 Android/Python 消费者尚未接入，不能据主机测试宣称端到端或全部消费已守护。

## 通道与身份

`android_audit.py collect --case-record <已有文件> --serial <USB序列号> --package <包名>` 只读已有 active、audit=true 的 session。只在 `.dev-session/` 写 `audit-request.json`，读 `audit-state.json` / `audit-permit.json`；与原 `request.json` / heartbeat 独立。另固定只读 `/proc/uptime` 取得同手机 CLOCK_BOOTTIME 毫秒，不将 host monotonic 与手机时钟相减。每次读写仍核对 USB；退出仅关闭自己启动的 logcat，并尽力发独立 revoke，不 close 共享 session。

所有 JSON `version=1`，拒绝重复键/额外键、浮点金额/数量和 bool 冒充整数。身份 token 为 32 位小写十六进制；`execution_id` 是 1—128 个 ASCII `[A-Za-z0-9_.:-]`。App 每个实际 run 生成独立 `run_nonce`，经本次 Pipeline attach 传到 Python；execution ID 不能塞进常驻 Agent 环境。App 不解析 stdout。

App `audit-state.json` 固定字段：`version, package, app_instance, session_id, execution_id, run_nonce, phase, started_boottime_ms, observed_boottime_ms, audit_persist_supported`。支持标记必须 true；只有 `phase=running` 可建立通道。idle 不可授权。App 在 run 启停时更新生命周期，失去 lease 时撤销；旧磁盘状态不能替代实际 run 校验。

请求公共字段 B：`version, package, app_instance, session_id, execution_id, run_nonce, collector_id, source_challenge, request_id, operation`。前三个 nonce/ID（run_nonce、collector_id、source_challenge）及 request_id 都为随机 token；session/app 身份继承当前会话。App 逐项匹配当前 run；collector/challenge 只属于该 run，不继承到下一 run。

| operation | B 之外的字段 | App 行为 |
|---|---|---|
| `source_challenge` | `expires_boottime_ms` | 期限不得晚于手机当前时间 + 30000ms，发布 challenge；同一 run 同时只接受一个未撤销且未过期的 collector，不能覆盖其他 collector。 |
| `persist_ack` | `stream_id, sequence, source_nonce, intent_sha256, source_boottime_ms, expires_boottime_ms` | 检查当前 run、collector/challenge、source 时间非未来且年龄 < 5000ms、expiry = source + 5000ms；拒绝重复 source_nonce。发布一次性短 permit，expiry 取请求 expiry 与手机当前时间 + 2000ms 的较小值。 |
| `revoke` | 无 | 只撤销精确匹配的 collector/challenge；不能停止/关闭其他 session 或 run。 |

`audit-permit.json` 为原请求所有字段，加 `phase, observed_boottime_ms, permit_expires_boottime_ms`。phase 分别为 `challenge` / `permitted` / `revoked`；拒绝不发布新许可。Python 只消费本次等待的身份、stream、sequence、source_nonce、intent hash 全部匹配且手机 boottime 未过期的许可；每个 source_nonce 最多提交一次。退出时 revoke 失败仍由短 permit 自行过期。源动作不能把 controller_completed 当作游戏效果确认。

## 日志、连续性和预算

主机启动真实 `logcat -v threadtime -T 1 MaaFw:I *:S` 子进程，读取 `MaaFw : agent| {JSON}`。沿用现有外层 `maa_punish_audit` envelope：session_id、stream_id、sequence、monotonic_ns、level、logger、message；stream/sequence 随常驻 Agent，跨 run 递增，**不要求从 1 起步**。message 是 JSON 字符串，边界事件如下：

- `source_anchor`：`audit_event, execution_id, run_nonce, collector_id, source_challenge, source_boottime_ms`。Python 读取本 run challenge 后有界回显；主机从匹配 challenge 的新 anchor 建立 stream/sequence 基线。anchor 手机年龄须 < 5000ms。
- `prepare_commit`：同上公共字段，加 `case_id, source_nonce, intent, intent_sha256`。source_nonce 每次准备随机生成；外层 sequence 是本事件的 ACK 绑定值。

anchor 之后同 session/stream 的**每条** audit envelope（含普通日志）必须严格 +1；重复、gap、换流、旧 run、旧 challenge、重复 nonce、晚到事件立即失败且不给 ACK。非 audit 的普通 logcat 行不占序号。host 记录收行 monotonic，仅用作本地处理预算；手机年龄来自 `/proc/uptime`，读之前记录 host monotonic，把读取耗时保守加到手机时间，并留 250ms 余量。创建 challenge 时则从该上界扣除本次完整读取（含 USB 核验）的时钟不确定性，以手机时间下界 + 30000ms 生成期限，不放宽 App 的 `expiry <= now + 30000ms` 合同。提交前再读会话/run/手机时间，把源事件手机年龄、本地处理年龄、手机 challenge 期限、本地 `challenge_started + 30000ms` 期限四者扣除 250ms 余量后的最短剩余预算传给 USB 验证与发布；USB 核验中耗尽预算后不再启动 ACK 发布，已经落盘的 pending 不回退。手机最终复核期限，OS 调用不承诺硬中断。collector 的 30 秒总期限不被普通日志刷新，到期须显式重新启动并生成新 challenge。

intent 精确字段：`cycle_id, target, currency, unit_price, quantity, budget_amount, budget_quantity`。前三项为 1—128 位 ASCII `[A-Za-z0-9_.:-]`；其余为严格整数，unit_price/预算允许 0，quantity 必须 >0，值和金额乘积均不超过 2^53-1。hash 为该对象 **UTF-8、sort_keys=true、compact separators=(',', ':')、ensure_ascii=false** 的 SHA256 小写十六进制。不得把浮点价格或自然语言商品名直接放进合同。

显式 `init-case` 以独占创建初始化 case 文件，必须提供 case_id/cycle_id/target/currency/unit_price/两项累计预算，禁止覆盖。collect 遇缺文件/损坏/未知版本直接停止，不生成零预算。记录包含固定 policy、revision、reservations；每笔 pending_unconfirmed 保存完整身份、sequence、nonce、intent/hash及时间，金额和数量占用累计预算。新 session、重启、重复执行均不能释放旧 pending；controller_completed 不核销。首版没有自动结清/回滚命令；后续凭真实游戏效果和人工证据对账，不从结果未知推断未消费。

collect 全程独占锁定同一 case；预留写到同目录随机文件，flush/fsync 后 `os.replace`，再打开目标 fsync 并读回核对，全部成功后才发送 ACK。任何写入、同步、替换、读回或发送失败都不回退预留；保守保留 pending。本工具会把接受的原始 audit envelope一并保存在预留证据中，不另建常驻日志平台。

## 验证边界

主机 [android_audit.py](../../tools/android_audit.py) 与 [54 项聚焦测试](../../tools/tests/test_android_audit.py) 已落地，覆盖真实格式解析、跨 run 序号锚、gap/重复/旧 run、严格金额与 hash、缺失/损坏账本、跨 session pending 预算、重复 nonce、磁盘故障、late ACK、USB/邮箱 allowlist 和只关闭自有进程。新增回归覆盖初次 uptime 读取耗时 1 秒、在本地 29.7 秒接收 prepare、USB 核验跨过 challenge 期限时禁止 ACK 且保留 pending，以及 uptime 采样前/后延迟下的 App 严格 challenge 上限；同时覆盖手机和 host 两种独立时钟的期限差异。修复前新增用例结果为 [3 failed, 1 passed, 50 deselected](../../build-debug/persist-ack-host-deadline-3258e8a97df746b1b8cf47f321c1f721/red-pytest.log)，修复后在 PowerShell 7、Python **3.12.10** 下原 50 项与新增 4 项合计 **54 passed in 1.99s**。日志与 JUnit 位于 [本轮测试目录](../../build-debug/persist-ack-host-deadline-3258e8a97df746b1b8cf47f321c1f721/green-pytest.log)。消息合同和公共 adapter 接口未变，本轮未扩跑完整 tools 测试。实际通过命令如下；TEMP/TMP 仅影响本测试进程，未修改系统设置或既有缓存。

```powershell
$auditTestRoot = Join-Path (Get-Location) 'build-debug/persist-ack-host-deadline-3258e8a97df746b1b8cf47f321c1f721'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = $auditTestRoot
$env:TMP = $auditTestRoot
& './build-debug/venv/Scripts/python.exe' -m pytest tools/tests/test_android_audit.py -q -p no:cacheprovider --basetemp (Join-Path $auditTestRoot 'green-pytest') --junitxml (Join-Path $auditTestRoot 'green-results.xml')
```

测试使用模拟 ADB/手机/时钟，没有执行真实 collect、连接或操作手机，也未修改 `android_debug.py`、Android 或 agent 源码。Android/Python 消费者尚待接入；设备、端到端、真实购买、人工效果结清与后续清理均未验收。文件 fsync/原子替换的测试不能证明硬件断电耐久性；初版 D12 后续接入不代表其他消费入口已守护。

## Python D12 消费端增量（2026-10-23）

本节更新上文主机阶段的 Python 状态；不改主机及 Android 消息字段。真实入口 `Buy_Item.jsonc` 的 `购买.action` 已改为注册的 `AuditPurchase`，原识别与 `next` 保留；其他采购节点未纳入。包装每次读取 `context.get_node_data(argv.node_name)`，专项进程的 `AUDIT_CONFIG.session_id` 必须匹配本次 `attach.audit_run`。execution/run 身份只从当前节点读取，不加入常驻进程环境。

普通模式保留 MaaFramework 原生点击：Custom 原生解析 `target/target_offset` 后把完整矩形传入回调，包装以 `tuple(argv.box)` 交给克隆 Context 的 `run_action_direct(JActionType.Click, JClick(...))`。随机点仍由框架计算，不改为中心点，也不添加可从 Pipeline 直接进入的裸 Click 节点。已用实际安装的 **MaaFramework 5.14.2**、内存 CustomController 验证识别目标、显式矩形、点目标、偏移只应用一次、随机点分布与 Controller 失败返回。这个离线控制器没有系统输入或手机连接。

专项必须具有显式 `attach.audit_intent = {case_id: "D12", intent: <上述精确 intent 对象>}`。此对象只是计划，不能授权点击。动作另外检查本次真实 `AndRecognitionResult.sub_results` 中命名 `识别D12购买意图` 的 Custom 子识别：父子 `reco_id` 链、`hit`、节点定义中的 `custom_recognition="D12PurchaseObservation"` 均须有效；其 `CustomRecognitionResult.detail` 必须精确包含本 run 的五项身份，以及 `observed_boottime_ms, frame_sha256, target, currency, unit_price, quantity`。时间须属于当前 boottime 的最近 5 秒，实际值与 intent 完全相同；框架本来提供 raw_image 时额外交叉核对 hash，不主动开启 debug、存图或新增截图。

**目前没有注册 `D12PurchaseObservation`，也没有增加对应生产识别节点。现有 And 只有商品与购买按钮，缺少价格、货币、数量现场证据，所以实际专项购买始终拒绝。** 测试中的结构化识别详情是模拟数据，不是实图标定。后续最小接入点是在同一 And 下实现经过真实确认页标定的 Custom 识别器，由它从本次传入图像生成上述观察值、帧 hash、boottime 和运行身份；禁止从 attach 或 `confirmed=true` 拼造观察。未知价格/数量没有默认值。

在本场观察齐备时，消费者最多等待 5 秒读取匹配 run 的 challenge，使用 `CLOCK_BOOTTIME` 发出 `source_anchor` 与带新 nonce、canonical hash 的 `prepare_commit`。不支持该时钟时拒绝专项输入。`LoggerComponent.emit_audit()` 复用已有 stdout handler、锁、stream 与连续 sequence，返回本次事件的 sequence 回执；普通日志同样占序号。stdout 写入失败不能获得回执。等待 ACK 有 5 秒上限，同时受 challenge 期限限制；身份、collector/challenge、stream/sequence、nonce/hash、source 时间及短 permit 期限逐项匹配。停止、损坏文件或超时使包装 `post_stop()` 并返回 False，不在回调中等待停止 Job。

本地未确认标记固定放在已安装 `files/PI/agent/audit_commit.py` 推导出的 `files/.audit-commit`，位于 PI 资源目录之外。普通 Windows/Linux 使用代码运行根下既有 `debug/audit-commit`。路径不由 attach 指定；规范路径、链接、所在文件系统及可信父目录的 owner 必须相符。Android external FUSE 的 owner 可能不同于特权 child UID，因此不把两者相等当作前提。两种模式共用进程与文件锁；发现 pending、损坏文件、残留临时文件均停止该步。

收到许可后，先独占创建永久 `pending.claim`，再将完整身份、intent、观察、nonce/hash/sequence 写入同目录临时文件，flush/fsync、原子替换、目标 fsync/读回（POSIX 再同步目录）。任一步失败都不删除已产生的 blocker。真实输入前再次检查 stop、当前节点、run 状态、观察年龄和许可期限；日志阻塞后也重新检查。即使 Controller 成功，只记录 `requested` / `controller_completed`，`effect` 始终为 `unconfirmed`，不自动核销。跨进程重启、换 session 或切普通模式都不能绕过已有本地 pending。第一版没有清理、对账或重试释放入口。

Python 验证使用 PowerShell 7、Python **3.12.10**，独立 TEMP/TMP、pytest basetemp 与 `-p no:cacheprovider`。验证证据在 [本轮目录](../../build-debug/purchase-guard-python-20261023/)：`unit-third.log/xml` 为修改权限兼容前的 62 项通过（含 16 项现有/新增日志测试）；后续 `purchase-final.log/xml` 的 52 项购买边界测试通过，另 3 项 native 测试曾因把 `post_stop` 的整体 Job 状态误当作失败而断言失败。改为检查真实回调 False 与零点击后，`native-final.log` 又记录到停止尚未完成时直接析构 Tasker 的 native access violation；在测试的外层 owner 等待停止结束后，`native-joined.log/xml` 为 **3 passed in 13.67s**。生产回调继续只发 post_stop，不在自己内部等待。失败记录保留，不能把异常进程当作通过。`schema.log` 显示 base/en_US/ja_JP/zh_TW 四份 Buy_Item Schema 通过；同名语言覆盖未覆盖 action，Custom 注册引用已核对。

未进行手机操作、真实 collect、APK 构建/安装或游戏购买；没有验证 Android external 存储在实际特权 child 下的权限/同步、真实观测识别、原生输入送达、硬件断电持久性或人工效果对账。专项实际行为仍是缺观察即拒绝，不能把 Python/本地 native 测试称为 D12 实购通过。
