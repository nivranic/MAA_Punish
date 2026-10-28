# 2026-10-23 主机持久 ACK 最小合同

> 当前增量见文末。原文中的“未注册／未取得标定”是历史截面；2026-10-27 已取得 D12—D14 国服详情页并接入离线 SDK 回放。真实购买、许可链、设备 fsync 和新 APK 验收仍未完成，不能据标定直接实购。

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

## 三类购买观察接入（2026-10-26，离线阶段）

`PurchaseCommit` 现在对三类购买使用不同的提交点及命名观察生产者，继续共用既有短许可、主机持久预留、手机 sticky pending 和最终原生 Click；消息金额、数量和内部时限合同未变。

| case | 唯一提交节点 | 同帧子节点 / Custom 注册名 |
| --- | --- | --- |
| D12 | `购买` | `识别D12购买意图` / `D12PurchaseObservation` |
| D13 | `点击购买_宿舍` | `识别D13购买意图` / `D13PurchaseObservation` |
| D14 | `点击购买_宿舍`（第三option覆盖） | `识别D14购买意图` / `D14PurchaseObservation` |

D14 实图已确认 `购买_宿舍` 的原坐标仅是最大数量控制，真正消费在后继 `点击购买_宿舍`；第三option为该共享节点选择D14观察，D13保持自己的观察。另一个 case 的完整观察、仅改节点名或沿用 D12 计划均不能准备提交。App 的最后 runtime patch 仍覆盖两个消费节点及早期数量节点的当前 `audit_run`，继续压过用户/选项中的旧身份。

三生产者位于 `agent/recognition/exclusives/PurchaseObservation.py`。它们只用本次 `argv.image` 调用 recognition-only API，核同一 run、完整商品标题、币种文字、单位价格、实际选择份数、可购态及图像布局；数字歧义、缺字段、多个 OCR 结果、源图改变、停止、节点改变、超龄均拒绝。观察值不从 `audit_intent`、默认数量、旧帧或缓存补齐。币种和目标 token 只能由已标定源内映射得到。`observed_boottime_ms` 是该 callback 开始读取本图的时刻，不是独立测得的截图采集时刻。

**当前源内 `CALIBRATIONS` 为空。** 未取得并独立接受真实 S0/P1/N1 图集前，不编造 ROI、币种、价款或购买份数，专项生产者不命中。普通模式返回仅用于保留原 And 的中性子结果，不提供合规观察对象、不额外截图/OCR/读取时钟；原按钮框和原生随机点语义保持。D13 的按钮 OCR 已移到 `识别宿舍购买按钮`，繁中/日语/英语同名覆盖同步迁移，尚未做这些语言的真实游戏验收。

真实图集至少需要每 case 的列表 S0、稳定确认页 P1 和其它商品 N1。P1 必须同帧看到完整商品、币种、价格含义、实际购买份数和可购状态；余额/库存/限购前态另作效果对账依据。只见总价而不能确认数量及换算关系时继续拒绝。当前没有公开消费计划输入或真实页标定；人工对账已有下述离线实现，但未部署验收，不能因此直接启动三项任务实购。

### 显式人工效果对账（同日离线增量）

现有普通 Click、Controller 成功和框架 COMPLETED 都不触发对账。独立 `AuditPurchaseReconcile` Custom 只接受固定节点 `审计购买对账`，不调用截图、识别、游戏输入或 StartApp；它在本次 audit 维护 run 内等至多30秒，消费独立邮箱 `purchase-reconcile.json` 的显式 host 请求。普通模式拒绝此动作，App 必须在用户覆盖后注入该节点当前 `audit_run`；旧APK没有这项绑定，不能假装已支持。

严格共享合同见 `agent/purchase_reconciliation_contract.py`。请求绑定原 pending/claim SHA、原 case/run/channel/nonce、完整 host reservation 和原 host case 快照 SHA；当前维护 run 另与同 session 的 source manifest、实际 loaded ACK、native/agent generation 和 display 绑定。source-state 文件存在不独立证明 loaded。请求最长30秒，失效、停止、节点或运行时变化均不能完成归档。

人工效果只接受 `confirmed_consumed`。操作者必须先逐张查看真实前后PNG，再逐字记录商品、币种、采图时刻、余额/库存/限购数值及框。host 检查实际PNG结构/CRC、SHA和尺寸，合同检查时序、标注框/数值原文、余额精确扣款，并至少要求库存按每份实际道具数增加或限购按实际份数减少。仅文件hash、任意正文、只扣钱、`购买成功`文案或一句确认都不能通过。该步骤是人工语义核验加机械一致性检查；不会声称已经由机器理解原图。UNKNOWN、缺证据或“不确定是否消费”不产生放行请求。当前不实现“未消费退款/释放额度”。

手机 marker 路径仍由安装位置固定推导。原 `pending.json` 和 `pending.claim` 永久保留。维护动作在同一边界锁内独占追加 `reconcile.claim`、`reconciled.json`、`reconciled.done`；三个见证只表达准备完成，不解除购买阻塞。只有后述真实原生终态核验后发布的 `accepted.json` 才能放行。任一残片、未知文件、坏hash、链接或缺见证继续拒绝。下一笔追加到固定 `commits/<新source_nonce>/`；不覆盖旧件，不挪目录，不以删除pending“重置”。同一当前请求可以幂等读回，不同请求不能覆盖原件。

host `finish-reconciliation` 独立读回手机原件及三见证，从设备固定日志目录两次读取相同的真实单任务 JSONL 并核验实际原生终态，先持久保存 `<receipt>.acceptance.json`，再在原pending槽位独占发布 `accepted.json`，最后追加 `<case>.reconciliations.json` 索引。原 reservation、金额、数量、原 `pending_unconfirmed` 事实及累计预算原封保留；索引单独表达后来确认消费的事实。成功归档也不返还额度，不因新run/session/case文件重建刷新旧周期预算。

### 复用既有短任务的实际调用

以下是后续唯一设备owner在新APK/源冻结及独审接受后使用的维护流程，**本节不是已执行设备收据**。不增加公开产品任务或调度器。

1. 保留原购买scope、case文件、pending及完整前后效果证据，由操作者审阅 `effect.json`。字段必须满足共享合同，不拷贝测试中的合成价格或图片hash。
2. 在独立 ignored 源候选中复用 `tasks/Screencap.json`，仅将它原有 `Screencap` 任务的 `entry` 改为 `审计购买对账`；不更改生产文件、不接回原截图循环、不设置其它游戏节点。冻结该候选manifest及唯一差异并独审后，按现有 `sync` 和当前已接受恢复基线绑定新的 loaded/revision/generation。配置副本仅启用这个短任务，沿正常助手入口派发一次。
3. 该维护run发布后30秒内，用已审真实记录显式发布请求；参数均为实际值和独占新证据文件：

   ```text
   python tools/android_audit.py prepare-reconciliation --case-record <原case.json> --pending-slot legacy --effect-record <已审effect.json> --before-image <真实before.png> --after-image <真实after.png> --request-record <独占request.json> --serial <已确认USB序列号> --package <已确认包名> --adb <现有adb>
   ```

   首笔兼容槽位为 `legacy`；后续槽位只能是实际新pending的32位nonce。host先持久保存精确请求、原case快照及原pending，再写固定邮箱，不写手机marker。对外任务/工具状态仍10秒一探，不把内部短许可处理改成10秒。
4. 等同一次维护任务终态并核零游戏输入，再显式读回完成见证：

   ```text
   python tools/android_audit.py finish-reconciliation --case-record <同一原case.json> --request-record <同一request.json> --receipt-record <独占receipt.json> --run-log <设备实际run_YYYYMMDD_HHMMSS_1.jsonl文件名> --serial <同一序列号> --package <同一包名> --adb <现有adb>
   ```

5. 独立核对源/运行时/原件/对账索引与预算，按受审恢复源撤去临时短任务适配并恢复配置选择。任何步骤未知、超时或残片都保留原件并停在拒绝态，不删除文件重试。持续设备策略和最终恢复责任仍按交接手册第7节。

本阶段的 synthetic 回归与 MaaFramework 5.14.2 内存控制器分别验证白名单、同帧字段、跨 case 拒绝、原生 And 按钮框、显式维护零游戏输入、对账幂等/故障/停止/并发及预算不返还；它们不证明真实 OCR、APK 编译/实装、许可设备链、真实扣款/到账、手机FUSE持久性或人工效果对账。验证记录存于 ignored `build-debug/pg27/`，不与历史批次数相加。首次对账测试有两项因Windows测试没有向新guard注入模拟BOOTTIME而失败，记录保留在 `rc1.xml`；修正测试注入后 `rc2.xml` 24项、追加实际native零游戏输入后 `rc3.xml` 25项分别通过，不能相加。生产没有增加CLOCK_MONOTONIC回退。

一次组合重跑的 `freeze.log` 保留真实 `0xC0000005`：native第三个矩形测试结束时，外层Stop任务200000086已终态，callback并发追加的200000087仍在队列；主线程已进入 `MaaTaskerDestroy/AsyncRunner.release`，随后87才开始。只等待外层自己的Stop Job不足以证明队列排空。修正仅在测试：`agent/test_support/native_lifecycle.py` 在join后等待公开 `running/stopping` 均false，并给首个native测试加finally；fake sleep仅替换动作模块对象，避免污染其它native测试的全局time。生产回调仍只发Stop、不会自等待，原native失败不改成通过。随后 `drained.xml` 组合218项通过；增加后排队Stop的确定性回归后，最终 `accepted.xml` 为219 passed、failure/error/skip均0，Python3.12.10、native与SDK均v5.14.2。另有Android patch8项、四语言Schema283文件及累计四资源目录加载通过；批次重叠不相加，未据此宣称穷尽所有native竞态。精确文件SHA及边界见 `build-debug/pg27/implementation-receipt.json`。

首个真实消费提交前，仍须在唯一设备owner的独占scope，利用已受审零游戏输入维护路径检验实际目录的写入、fsync、目录fsync与读回能力。不得在全局pending命名空间创建假真实购买。新nonce槽位已要求同步叶目录、commits父目录和marker根目录；Windows/模拟目录顺序回归不能代替Android external FUSE/权限或断电持久性证据。若设备存储能力不支持，保持fail-closed并记录具体失败，不能省略同步来放行。

### 独审故障修复：最终接受与固定授权预算

独审原始故障保留在 `build-debug/pg27-review/`：最终 `reconciled.done` 已可读而目录 fsync 抛错后，旧读侧曾误放行；同商品耗尽后重建 case 文件曾刷新额度；最终见证写入期间 Stop／请求过期曾返回成功。本人复跑四条 probe 的原始失败为 `phase2-original-red-actual.xml`，首次默认临时目录不可写的 setup error 另存 `phase2-original-red.xml`，两者不互相覆盖。

当前实现把三个准备见证与最终接受分开。动作重新同步见证和目录，在所有 I/O 后重新核对 Stop、当前run、请求期限、节点及 loaded/runtime，再返回真实 Custom 结果。尾段失败先清进程内准备票据，并追加 `reconcile.failed`；即使故障文件不能落盘，旧三个见证也不会独立放行。进程内票据只服务同请求幂等，不授权购买，不能跨重启补事实。

Agent 的 `purchase_reconciliation_ready` 仅绑定准备事实；它不是成功。原生生产者须以实际 TaskWait 和稳定 TaskDetail／NodeDetail 证明本次唯一任务为 `Screencap → 审计购买对账`，唯一同名节点 `completed=true`、两种状态均3000且无停止，再由同execution的 App 终态生产 `purchase_maintenance_terminal`。host 严格要求设备实际 JSONL 中唯一 ready → 唯一 native terminal → 唯一成功 task_result → 最后 COMPLETED footer，并绑定完整维护身份、原request、receipt/pending/claim SHA和runtime。字段错位、异步迟到、重复、缺footer、取消或未知一律拒绝；不声称 stdout 与 footer 总能排空，不接受本地手写success文件。此生产者和后述接链仍需独立审查，不能据 Python fixture 宣称 Android 已可达。

最终 `accepted.json` 保存实际原始 JSONL 及其SHA、原receipt/pending/claim SHA和本次维护请求。购买读侧逐次独立校验并重新 fsync 原见证、最终文件及目录；确认后它可跨 Agent／App 重启继续使用，普通 No 无需反复连接host。源码回切或重启不退款、不重记原pending，也不依赖旧进程票据。软件最后状态检查之后发生的异步Stop不承诺与Python／磁盘原子；真正持久放行另以返回后的原生成功和host最终见证为界。

如果只留下完整准备见证、尚无最终接受，可在当前维护run沿同一短任务入口显式使用 `prepare-reconciliation --revalidate`，其新request绑定旧receipt SHA及完全相同的原pending/claim/reservation/效果，并使用当前run/source/generation及新request nonce；旧请求不能代入新run。动作只重新核验和同步，不改写原件；host仍须读取这次实际原生成功终态才能补最终接受。坏／残缺原件、效果改变或缺原reservation不提供恢复捷径；原失败文件永久保留。

host 固定授权账本位于源码工具推导的 `debug/audit-purchase-authorizations/<case+target的SHA>.json`，不接受 case路径、attach、环境变量或cycle字符串选择其它根。`init-case` 首次冻结该商品完整policy；后续case文件可作为新证据容器，但同商品cycle、币种、单价和两项额度必须与原授权完全一致。reserve 在商品账本锁内累计所有已确认／未知预留，先持久预留固定授权账本，再更新case及发送ACK；后续case写入或发送失败不返还占用。手机同时累计全部原pending，独立冻结首次同case／商品的完整policy。当前没有换周期或扩额命令，真实下一周期须另行设计显式授权流程，不能靠改cycle或budget字段绕过。跨机器接手须保留原授权账本；缺失／损坏不自动从空预算继续。

本增量的定向回归包含完整 synthetic 最终接受之后的真实新Python进程读回、普通No、跨case文件、冻结policy、耗尽额度、未知写入、并发锁、原始故障probe及原D12守卫。synthetic 日志明确标为fixture，不是真实App终态；本增量未部署、未实购，`CALIBRATIONS` 仍为空。最终定向收据和文件SHA见本批 `build-debug/pg27/`，不把重叠批次数字相加。

### 2026-10-27 D12—D14 实图标定与单次计划候选

本节更新前节“CALIBRATIONS为空”的历史状态。现仅标定已取得的国服 1280×720 详情页：D12 必须为 `d12-P1-details.png` 的完整“逆元碎片-清商”，非未打开详情的列表图；D13 为 `d13-P1.png` 的“涂装设计草稿”；D14为 `d14-P1.png` 的“武器涂装设计图”。D14货币“遣测凭证”由 `d14-currency-info.png` 的真实标题确认，不从商店名猜测。原全屏、账号／余额、配置和日志仅留在 ignored 收据目录；源码只加入中央商品／花费／底部控制的实际 ROI 裁图、去余额的币名裁图及31×31货币图标模板，来源SHA和坐标见 `agent/tests/fixtures/purchase_observation/provenance.json`。

生产者对同一个 `argv.image` 做实际 SDK OCR／模板识别：完整商品名、包内x1或x5、花费数字、购买份数、限购原文、花费标签和购买按钮均须可读；螺母／宿舍币来自花费旁图标的 TemplateMatch，模板名、ROI、实际分数另行核对。只在实际份数严格读到1时把花费解释为单价，D13包内x5不表示买5份。其它商品、遮挡、坏布局、非匹配语言、错货币或缺份数继续拒绝，不从intent、默认值或缓存补数。最初宽份数ROI把背景混读成 `po/31` 的失败探测记录保留，收紧后的真实OCR读到1；没有用默认1绕过。

本Goal内的可审执行者计划是每case最多1份：D12上限20000螺母，D13上限3宿舍币，D14上限4500遣测凭证。这是执行者在用户已授权购买验收目标内选择的最小实价预算，不表述为用户曾给过该金额；不包含多份、最大、黑卡或现金。实际效果应结合精确扣款及限购变化；D12/D13预期1→0，D14为10→9。D13如用库存佐证，每份含5张，40→45只证明买1份。

独立候选位于 `build-debug/pg27/calibration4/once/{D12,D13,D14}/`，包含可审 `audit-intent.json`、`review-plan.json`、未执行的host `init-case`命令模板及10文件白名单source overlay（含本轮严格终态合同）；它不是完整managed source manifest，不可把overlay目录直接当全量源码sync。候选复用现有 `Screencap/ScreenCaplimit`，将任务可见标签改为明确的购买单次验收，并由已稳定P1进入原guard提交节点。完整固定 `attach.audit_intent`、一次命中、空后继／错误路径及收紧后的原按钮ROI均放在必经的 `task.pipeline_override`；可选 `ScreenCaplimit.pipeline_override` 仅重复相同计划。缺省或未声明选项时仍保留计划，不能丢失审计标志而回退普通点击。D14共享最终按钮的识别覆盖直接取原第三option的D14子观察定义。候选未携带`audit_run`，实际App在选项后最后注入当前身份。旧截图循环的延迟占位符不属于这条单次链，已从候选覆盖删除。

`audit_intent`键同时标识审计专用提交：只要该键存在而审计进程未启用，guard直接拒绝，临时“截图”入口不会继承普通No点击回退。原普通任务没有这个键，保持既有原生Click。当前run禁用、lease关闭、旧session、idle状态、过期许可或同帧字段不符均不输入。一次候选只验证确认／提交核心；导航、最大数量流程、成功弹窗处理、自动返回和原完整购买任务端到端仍未验。

D14实图已确认早期固定坐标(643,671)为“最大”，旧“早期已消费”的归类不成立。D13/D14现在共用真正的 `点击购买_宿舍` 消费guard，由各自实际option选择唯一D13或D14同帧子观察。原D14早期节点以对象 `attach.audit_quantity_role={case_id:D14,role:skip_max_in_audit}` 明确角色：普通No仍用原native最大Click，审计模式在当前binding/state/节点/Stop复核后零输入跳过最大，最终guard仍必须实际读到份数1才可能提交；该跳过不预留、不退款、不产生消费证据。原始第三option合并后的SDK输入轨迹另验，不能只移动临时probe坐标。真实S0-nearstart的原“本月”选择节点只有一处OCR命中，box=(952,414,102,27)位于武器图卡内；该帧不支持误选角色碎片的结论，也不证明所有滚动位置或完整导航。

原生终态消费合同另已收紧来源：ready须为既有Agent stdout，terminal／task_result必须是App自产Info；同一task_result中的七字段 `purchase_maintenance_native` JSON为必需，须与terminal逐项一致，不能仅凭同前缀文本或Agent伪造成功。原kind缺口和后缀矛盾红例均保留。native worker现已通过实际MaaRunner／App callback／Recorder的 `onAgentOutput` 路径生成4份stdout／stderr伪terminal或task_result日志；共享parser逐份因Agent／AgentError来源拒绝。另3份后缀矛盾明确是对真实正例的单字段synthetic修改，不冒充生产者能自产矛盾字段。对已冻结JVM产物独立只读回放10项，正例1项接受、其余9项拒绝，所有原件SHA与native worker的phase2收据一致；结果见 `build-debug/pg27/calibration4/native-provenance-replay.json`。本场pending／请求／效果为合成数据，JNA端点由MockK脚本提供；不以这些JVM证据代替手机终态。

实际App接链证据位于 `build-debug/native-purchase-terminal-20261027/evidence/option-compiler/`。真实 `PiParser/ProjectLoader → RunPlanBuilder` 先加入任务覆盖，再执行生产 `compileOptions`，最后由生产 `runtimePayload` 生成任务数组；普通No变体来自同一RunPlan实际调用 `runtimePayload(null,null)`。`agent/tests/test_purchase_app_payload_native.py` 将每份导出的 `pipelineOverrides` 数组原序交给 MaaFramework 5.14.2 `Tasker.post_task`，不预合并、不替换身份、不填占位符。实际P1、生产And、生产观察者和购买guard配合内存Controller，验证三case的default／unset／无option声明／伪造旧身份四类正例均只请求一次内存点击；坏intent、普通No及所有真实No变体均零点击；坏option由实际App编译拒绝且无payload；原D14第三option从既有P1消费段验证跳过最大及最终D14观察，没有冒充完整导航。`build-debug/pg27/calibration4/app-sdk-r1.xml` 为 **37 passed in 109.22s**。这些是实际App编译、离线SDK与合成run／许可的接链证据，ADB调用、系统输入及真实购买均为0。

较早的 `final-r1.xml` 为271 passed、2 skipped，覆盖三case真实全图／脱敏ROI／N1、原生Click兼容、D14原option、host／phone及终态parser；这批的selected-option路径不能单独证明缺省安全，后述37项补充必经任务覆盖与实际App导出。两项skip仅为沙箱拒绝实际Windows symlink／junction创建，确定性reparse拒绝测试已通过，不据此宣称实测了Windows链接或硬件断电。Schema收据 `schema-final.log` 为290文件通过。各批有重叠，数字不相加。原延迟占位符导致30项失败的 `once-actual.xml`、D14两项Rect序列化失败、parser来源／后缀7条红例及历史native崩溃均保留。实际App导出测试已在独立synthetic runtime留下pending，不能删证据后复跑；如需新一轮正例，应由生产者导出新的mock runtime和run绑定。

当前三份最终 `source-overlay/tasks/Screencap.json` 的SHA分别为 D12 `93b6be21162d5eb35362cb11d6b600551a776f234af3140888427b9e09a92aa0`、D13 `84995574053e82ee747d4bcbb38ae6dd2720c4621e5e8f274ab769f9722e73c5`、D14 `96cf421a501610d709969214dae89f475cac305efec0cae9108eb054de1d72d6`。旧仅依赖可选intent的候选不再用于部署。新APK、实际loaded源和代次、设备真实fsync能力、许可链、真实实购／后效及原生最终接受仍待唯一设备owner在独审接受后完成，本节不构成真机购买PASS。
