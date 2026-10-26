# iQOO16 专项日志的进程初始化合同

本轮只实现附件第 4.4/6 章涉及的显式开关、USB 会话确认、Python 启动前环境贯通及可排序 stdout；不代表整个 B 阶段完成。

## 开关与链路

`tools/android_debug.py ... session --audit-logging` 显式申请专项模式，省略该参数时关闭。hello 必须声明 `audit_logging_supported=true`，请求与状态必须携带布尔 `audit_logging`；缺少新合同的旧 App 明确拒绝。模式在 lease 创建时固定，heartbeat 不能改变模式，关闭或过期后状态恢复关闭。

普通 start 从 `DevSessionCoordinator.lease` 现取 lease，经 `RunPlan.runtimePayload()`、`RunPlanPayload.piEnv`、Binder、`AgentLaunchRequest.piEnv` 传递。源码 reload 与 rollback 同样现取 lease，缓存 payload 不保留过期会话。`ExecAgentHost` 在 `ProcessBuilder.start()` 前清除继承环境和 runtime descriptor 的全部保留前缀键，再安装合法 payload，保留其他环境值。

| 环境键 | 专项合法值 |
|---|---|
| `MAA_PUNISH_AUDIT_LOG_MODE` | `stdout` |
| `MAA_PUNISH_AUDIT_SESSION_ID` | 32 位小写十六进制会话 ID |

普通 payload 去除全部 `MAA_PUNISH_AUDIT_` 键；专项仅由开启的 lease 生成上述一对键。孤立键、非法值和未知保留键在 spawn 前或 Python 首次导入时明确报错，不降级为文件日志。

## Python 行为

`agent/audit_config.py` 在首次导入时生成不可变 `AUDIT_CONFIG`，暴露 `enabled`、已校验的 `session_id` 和本次进程随机 `stream_id`。`logger_component.py` 导入该配置后才决定 sink；专项不创建 debug 目录、FileHandler 或日志文件，也不清历史日志。普通离线模式无需主机，保留原有文件、stdout 和过期日志策略。

专项记录为单行 JSON，包含 `event`、`session_id`、`stream_id`、`sequence`、`monotonic_ns`、`logger`、`level` 和 `message`，异常另含 `exception`。多 logger 共用进程锁，序号和 stdout 实际行顺序一致；跨进程使用 stream_id 区分，不能用两个进程各自的 monotonic_ns 推导全局顺序。替换或关闭仅处理当前组件拥有的 handler，并关闭被替换的旧文件 handler，不关闭宿主/外部 handler。

第一条实际日志从 sequence=1 开始，最后一条记录表示已观察到的最后日志；未增加伪 EOF 或保证进程正常退出的尾事件。异常退出、强杀、logcat 丢失或主机断流可能缺少尾部，不能据此宣称日志完整。现有 stderr/native 输出与 App 的 stdout/stderr 转发继续使用原链路。

专项开关仅影响新启动并读取新模块的 Python 进程。旧 APK 或旧 PI 的 Python 不会因新 native 环境自动获得该行为；安装、新源交付、旧进程退出与必要日志备份需分别确认。原有 reload 的已 prepare/复用显示器约束不变，不能以启用开关代替首次交付验收。

## 验证与边界

- Python 3.12.10：`agent/tests/test_audit_logging.py` 14 项通过，覆盖初始化前无写入/删除、非法输入、历史保留、并发序列、同会话重启换 stream、恢复普通模式和 handler 所有权。
- PC helper：`tools/tests/test_android_debug.py` 66 项通过；使用仓库内独立 TEMP/TMP 避免沙箱默认临时目录权限问题。覆盖开关默认关闭、旧握手拒绝、请求/确认模式一致和关闭复用。
- Android patch/helper：当时 `.github/android/tests` 33 项通过，覆盖补丁幂等与安全失败。
- 新增/扩展待 fresh App JVM 验证的类：`DevAuditEnvironmentTest`、`DevSessionProtocolTest`、`MaaFrameworkRunnerPortTest`；后者通过实际 start 序列检查开启、关闭、无 lease 时序列化 payload 的环境值。

尚未验收新 APK 的设备行为、真实 Python spawn、logcat 收流/断流守卫、整个框架和壳层日志的会话清理、长按停止与角色策略。本设计不新增网络服务、主机 daemon 或通用日志平台。
