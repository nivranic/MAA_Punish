# iQOO16 专项 USB lease 与单次运行绑定停止

## 目标与范围

修复 B 的一个后续缺口：App 存活时，开启专项日志的 USB lease 过期、显式关闭或邮箱故障导致 lease 失效，必须撤销与该 lease 绑定的运行；迟到的停止与回调不能影响随后启动的普通运行。

本改动沿用 [专项日志初始化合同](2026-10-23-iqoo16-audit-logging-design.md)，修改 Android 源码补丁及相关测试。不改变主机工具、Python 战斗、日志目录、源码维护协议或设备上的冻结 B 构建。

## 已确认链路

`DevSessionProtocol` 过期与关闭会清除 lease；`DevSessionCoordinator` 邮箱失败也清除 lease。此前 Runner 只在构建 payload 时读取 lease，没有绑定运行、失效停止或 Preparing 拒绝派发。

`RunLauncher.gate` 覆盖完整启动及维护。若失效处理等待这个 gate，Preparing 时无法立即撤销，并会与维护的 `settling.join()` 形成不必要的等待关系。因此保留原 gate 的职责，在真实 `MaaFrameworkRunnerPort` 增加一个只覆盖占位、最终 `startRun` 和条件 `stopRun` 的命令 Mutex。等待终态时不持有这些锁。

## 实现合同

1. `start` 占位时固定 `DevSessionLease` 与 `executionId`。仅 `auditLogging=true` 创建 `DevAuditRunGuard`；同一不可变快照生成运行 payload，不在 Preparing 结束时切换成另一会话或普通模式。
2. Runner 直接消费 Coordinator 的 `StateFlow`。每个专项绑定只有一个 watcher，无额外定时器、daemon 或网络服务。首次观察到 lease 不匹配即永久撤销，该绑定不会因新 lease 恢复。
3. 最终 `startRun` 在命令 Mutex 内重查 executionId、撤销位及当前 lease。Preparing 撤销时零派发。若 lease 在检查后失效，watcher 在同一命令锁之后请求停止已经派发的那一次运行。
4. 专项停止同时核对绑定对象与 executionId，只认该绑定第一次停止请求。普通 `stop` 也在 IO 切换后重新核对原 executionId，避免旧的普通停止请求跨越启动边界。公共停止和专项停止都不能绕过最终派发锁。
5. `ExecutionCallback` 持有原绑定；旧回调只更新旧绑定。正常结束、未派发的启动失败、取消及 abort 回收 watcher；取消覆盖进入 IO 之前的切换窗口。native 可能已受理时的启动异常或取消先请求条件停止，保留 Stopping，不能编造 Idle。
6. 已派发停止之后最多等待 15 秒终态。此等待不持命令锁、不 `join` Launcher，也不阻断后续正常收尾。超时保留未确认状态与超时标记，只有该 execution 的真实 `onFinished` 能改为 `Finished`。
7. 独立审查补齐 Launcher 消费者：启动返回 Rejected 或取消时，若本 ctx 的 executionId 仍 busy，已挂载的 Release 在解除入口 gate 前移交 App scope 的既有 `settling`，按真实运行结束收尾。该分支在最终 Release 完成前阻止新运行复用环境；维护仍等待既有 15 秒屏障。未派发取消与其他普通 NotRun 保持立即收尾。

`DevAuditRunGuard` 只保存单次绑定、撤销位、watcher 及状态；实际命令由真实 Runner 执行。Runner 保留最近一次审计绑定供诊断，不累积历史列表或协程。

| 状态 | 证据含义 |
|---|---|
| `Preparing` / `Running` | 绑定已建立 / `startRun` 已尝试派发 |
| `StopRequested` / `StopAccepted` | 已请求 / Binder 报受理；均不证明 native 停止 |
| `StopRejected` | Binder 未受理或调用异常；等待或后续终态仍可能到达 |
| `NotDispatched` | 已知没有派发或服务明确拒绝派发 |
| `TimedOut` | 15 秒未收到终态；不强行清 Runner 的运行态 |
| `Unconfirmed` | 启动返回异常、取消或既有 abort 路径结束本地处理；不证明 native 停止 |
| `Finished` | 绑定自身的 `onFinished` 已到达；晚到回调可保留既有超时标记 |

## 验证记录

- Python 3.12.10：设置 `PYTHONUTF8=1` 后，`python -m unittest discover -s .github/android/tests -q` **34 项通过**。新增真实 pinned Runner fixture，验证整套补丁在实际消费者上组合且幂等；单锚点、混合状态拒绝及全量原有补丁安全测试通过。
- 独立 ignored stage：`build-android/iqoo16-audit-bound-stop-20261023T063520Z/`。从固定 `bc55dac4c8231ab73e7ee42bd38c43e322838b14` pristine 生成；首次补丁 72 文件，重复为 0。后续调整也从 pristine 重生成受影响文件，整套补丁再次为 0。
- 首次 JVM 编译通过，41 tests 中 1 项测试失败：同一测试 dispatcher 未制造预期的 IO 切换。已改成独立 dispatcher，并增加进入 IO 前取消、native 派发后取消两例；首轮报告保存在 `evidence/first-test-results/`。
- 独立审查发现并修复一项 P2：native 可能已运行但启动返回异常时，Launcher 会提前执行 NotRun Release。修复后定向复核未发现新增 P1/P2。
- 最终 JVM **75 项通过，0 failure/error/skipped**：真实 `MaaFrameworkRunnerPortTest` 30 项（含本次新增 14 项）、`RunLauncherTest` 32 项（含新增 5 项）、`DevAuditEnvironmentTest` 3 项、`DevSessionProtocolTest` 10 项。`:app:testReleaseUnitTest` 实际执行，未 FROM-CACHE/UP-TO-DATE/SKIPPED；生产 Kotlin、测试 Kotlin 编译通过。收据 `evidence/bound-stop-verification.json` 记录源码 hash、生成代码 hash、每个 XML 和全部 case 名。命令如下：

```powershell
& ./build-android/dev-session-delivery/test_stage.ps1 `
  -StageManifest ./build-android/iqoo16-audit-bound-stop-20261023T063520Z/stage.json `
  -TestFilters @(
    'com.aliothmoon.maafw.runner.MaaFrameworkRunnerPortTest',
    'com.aliothmoon.maafw.runner.RunLauncherTest',
    'com.aliothmoon.maafw.devsession.DevAuditEnvironmentTest',
    'com.aliothmoon.maafw.devsession.DevSessionProtocolTest'
  ) -LogName gradle-bound-stop-reviewed-tests.log
```

真实 Runner 回归覆盖：活动专项断 lease、重复停止、旧停止/回调与普通新运行、Preparing 失效后新 lease、普通关闭模式、最终派发前核对、Preparing 期间新开 lease 不改变快照、停止超时与晚终态、准备取消、IO 切换前取消、native 派发后取消、停止与 finish 同时到达、旧回调不确认新专项绑定、native 明确拒绝。

真实 Launcher 回归新增：未确认启动拒绝的 Release 与维护屏障、已派发取消的收尾归属、未派发取消立即收尾、下一次普通启动等待旧 Release、维护超时不能越过屏障且不遗留入口锁。

## 未验证边界与后续

本次未构建 APK、未安装、未操作手机；上述 JVM 使用真实 Runner 与 fake Binder 服务，不替代设备停止及触点释放验收。B 冻结构建和设备上的初次证据链不含本修复。

本次不承诺 App 崩溃后的 native 孤儿处理、不实现 logcat 鲜活检测、主机持久化 ACK、完整成本节点或进程级证据完整性。15 秒是 App 侧终态等待期限，不是同步 Binder 系统调用的硬中断期限；`MaaRunner.stop` 的既有语义是立即受理再异步完成。

回滚只撤销本记录对应的 Runner 补丁、guard overlay 与新增测试，保留此前 audit 环境和 source reload 实现。源码变更不能修改已安装 APK；设备验收需另行构建与明确交付。
