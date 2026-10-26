# USB 调试会话与免重装调试实施计划

规划日期：2026-10-22。实施于 2026-10-23 完成 Phase 1–4 代码、集成检查及签名 APK 构建；Phase 5 已完成覆盖安装、稳定屏保与三类源码实际生效对照，原源码/配置恢复及最终锁屏已核验。物理拔线、故障与重叠使用权等矩阵仍未实机验收。证据见 [实施验收记录](../USB调试会话验收-20261023.md)。

## 1. 目标与范围

用户已确认：**电脑连接助手由项目调试命令自动启动，只在调试会话期间有效**，不配置登录自启或后台常驻。

预期流程：

1. 在电脑启动项目调试命令，通过已授权的 USB ADB 连接指定手机。
2. 用户打开 MAA 后，自动启用现有“熄屏挂机”；即使暂未运行任务，也能维持屏保与防止自动休眠的效果。
3. 直接拔线、结束电脑调试命令或心跳失效时，释放本次 USB 会话的屏保使用权。手动或任务仍在使用的屏保继续保留。
4. 修改 ROI、Pipeline、模板图片或不增加运行时依赖的纯 Python 后，通过电脑命令同步、停止旧任务、重新加载，再从合适入口验证，减少 APK 重装。

边界：

- 复用现有屏保，不另写手机 App；电脑端负责识别本机 USB 连接和同步，手机端负责屏保、租约超时及安全重载。
- 不修改系统自动亮度、全局亮度、自动锁屏超时，也不绕过密码、指纹或系统锁屏。屏保使用自身窗口的亮度和保持亮屏能力；其在 iQOO 上的实际效果须验收。
- 保持“运行时自动熄屏”和“运行时保存截图”等已有设置的含义与值。USB 临时策略不写入持久设置。
- **先集中更新一次 APK**，交付手机端会话和重载能力；之后源码调试按变更类别决定是否重装。Android/Kotlin、Manifest、原生库、Python 解释器或运行时依赖变化仍需要打包。
- 重载以任务边界为单位，不承诺正在执行的 Python 或战斗无缝热更新；保留游戏进程和画面不等于暂停游戏。
- 制定计划时未实施；用户后续已授权开始执行。实施代码只在 `mine`，`main` 仅同步上游 `main`；尚未授权本轮 commit/push。

## 2. Phase 0：文档与实现发现（已完成）

### 2.1 证据基线

- 当前仓库：`mine`，基线 `68f582d069ad5d0e65686ffc539d8b7e10a38175`；开始规划时工作区无 tracked/untracked 改动。`git status` 对 ignored 的 `agent/.pytest_cache/` 有访问警告。
- Android 上游：`Aliothmoon/MaaFwApp@bc55dac4c8231ab73e7ee42bd38c43e322838b14`，来自 [install.yml](../../.github/workflows/install.yml) 第 368–400 行；框架基线 MaaFramework `v5.14.2`。
- 持久实现入口：[maafwapp_patches.py](../../.github/android/maafwapp_patches.py)、[overlays/](../../.github/android/overlays/)、[patch_maafwapp.py](../../.github/android/patch_maafwapp.py)。`build-android/MaaFwApp/` 为本地核查用的第三方源码，不能作为唯一修改交付物。
- 已读权威指南：[开发与验证指南](../开发与验证指南.md) 的运行、Android 打包和验证章节；已核查固定版本的屏保、任务、资源、Agent、PI 安装与服务生命周期。
- 两份只读发现结果分别覆盖 USB/屏保和资源/Agent 重载；以下 API 清单由其源码位置与定向复核汇总。尚未做真机验证。

下表源码位置均相对固定版本的 `app/src/main/java/com/aliothmoon/maafw/`，实施前按文件和方法复核，行号只作定位提示。

### 2.2 已存在、可以复用的 API 与模式

| 位置 | 已确认能力与限制 |
| --- | --- |
| `overlay/screensaver/ScreenSaverOverlayManager.kt:81–140,186–209` | `suspend fun show(): Boolean`、`suspend fun hide()`、`isShowing: StateFlow<Boolean>`；已有 `FLAG_KEEP_SCREEN_ON` 和窗口 `screenBrightness = 0.01f`，只支持 `RunMode.BACKGROUND`。已显示时 `show()` 仍返回 true，返回值不代表本次调用拥有屏保。 |
| `runner/EnvironmentHooks.kt:213–233,360–363`、`di/RunnerModule.kt:142–147` | `ScreenSaverHook` 与 `RunScreenSaver.show()/hide()` 已用于运行期申请和释放；注释里的“本次盖上”语义与 manager 的实际返回语义不一致。 |
| `ui/tasks/TasksRunnerBar.kt:186–196,253–260`、`session/SessionViewModel.kt:597`、`ui/AppRoot.kt:345–347` | 已有手动屏保入口与“运行时自动熄屏”设置，不新增同义开关。 |
| `privileged/PermissionManager.kt:119–145` | 已有 `ProcessLifecycleOwner.get().lifecycle.addObserver(this)` 与 `onResume(owner: LifecycleOwner)` 模式，可用于“用户打开 App”后的会话握手。Application 初始化不等于用户打开 App。 |
| `service/RunForegroundService.kt:93–118,136–157` | 现有前台服务在任务不 busy 时自停，返回 `START_NOT_STICKY`；不能直接用它保证无任务 USB 会话存活。 |
| `remote/MaaRunner.kt:204–234,310–318,355–396,467–481` | 已有 `start(payloadJson: String): Boolean`、`stop(): Boolean`、`destroy()`；资源按路径缓存，Agent 按配置和存活状态缓存。同路径内容变化不会自动重建。`stop()` 和 UI Idle 都不是原生线程停止屏障。 |
| `remote/MaaRunner.kt:578–590,638–684` | 私有 `destroyTasker(lib, handle)` 依次 PostStop、等待 `MaaTaskerRunning == 0`、释放关联 Agent、销毁 Tasker；`releaseResource(lib)` 再释放 Agent 和 Resource，保留 Controller。这些是内部实现，不是现成公开重载 API。 |
| `remote/RemoteServiceImpl.kt:337–355` | `stopRun(): Boolean` 不销毁虚拟屏；`destroy()`/cleanup 会销毁虚拟屏，不能用于保留页面的重载。 |
| `runner/RunPlanPayload.kt:13–35`、`project/ProjectRepository.kt:45–63`、`session/SessionViewModel.kt:610–612` | 当前 payload 无源码 revision；UI `ReloadProject` 只重新解析项目，不负责重建原生 Resource 或 Python Agent。 |
| `constant/AppPaths.kt:33–37`、`constant/AppFiles.kt:20`、`project/PiInstaller.kt:123–169,318–325` | PI 位于 `getExternalFilesDir(null)/pi`；安装标记为 files 根的 `pi.version`。同版本 ensure 保留源码；完整重解 PI 可能删除其中的配置与调试数据，不能当热重载。 |

### 2.3 官方资料及明确不依赖的能力

- [Android ADB 文档](https://developer.android.com/tools/adb)：使用 USB transport 选择与显式设备校验；无线 ADB、模拟器和默认任意设备均不满足本次触发条件。
- [Settings.Global.ADB_ENABLED](https://developer.android.com/reference/android/provider/Settings.Global#ADB_ENABLED)：第三方 App 读取受限，不作为可用的 USB 调试判断接口。
- [AOSP UsbManager](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/hardware/usb/UsbManager.java)：`ACTION_USB_STATE` 不是稳定的第三方公开接口；连接、充电或 adb extra 都不能证明是本机已授权 ADB。
- [Android 广播生命周期](https://developer.android.com/develop/background-work/background-tasks/broadcasts)：动态广播依赖进程存活。即使使用可用的 USB 断开提示加速清理，仍必须有手机本地的超时回收。
- `ACTION_USB_DEVICE_ATTACHED/DETACHED` 面向手机作为 USB host 的设备连接，不用于判断手机连电脑。
- 现有 App→特权服务 `heartbeat(int appPid)` 不代表电脑在线，不能复用为电脑会话心跳。

**本阶段产物**：确认可复用入口、共同消费者和缺失能力。没有现成的“本机 USB 自动屏保”或“安全源码重载”公开 API；下文新增类型、命令和协议均为拟实现设计。

## 3. Phase 1：最小电脑调试会话与传输通道

依赖：Phase 0。本阶段先做端到端小验证，确认当前设备与 Android 版本允许所选通道，再扩展功能。

### 实施任务与文件

1. 拟新增 `tools/android_debug.py`，统一负责 `session`、`status`、`sync`、`close` 子命令。采用 Python 3.12 标准库和已有 ADB，不引入独立常驻服务、HTTP 服务或通用 RPC。
2. 会话命令显式接收 `--serial`、`--package` 和 ADB 路径。首版仅支持单台 USB Android 设备：以 `adb -d get-serialno` 结果与指定序列号交叉校验；多 USB 设备、offline、unauthorized、无线连接或模拟器直接拒绝，不自动选第一台。每次发送命令/心跳前重新确认 USB 绑定，后续操作固定目标。
3. 首选受限的文件邮箱：在目标 App 的 external files 根下新建专用 `.dev-session/`，不放进 PI 或用户配置目录。电脑通过 ADB 读写，手机通过自身文件权限读写；先验证目标手机的访问权限与同目录重命名行为。
4. 拟新增 overlays 中的 `debug/DevSessionCoordinator.kt`、协议与文件邮箱实现。App 前台打开后生成新 challenge；正在运行的电脑助手响应，绑定 package、host 标识、session ID、challenge 和递增序号。host 标识只用于区分会话，不能宣称它本身构成主机身份认证；信任边界来自已授权的本机 USB ADB。
5. 每条消息有协议版本、固定操作类型、请求 ID、有限大小和确认结果。临时文件完整写入后再晋升；拒绝旧 challenge、旧序号、其他 session、未知操作和重复执行。仅允许会话及本计划的重载操作，不接受任意 shell、任意路径读写或任意任务执行。
6. 建议心跳间隔 2 秒、手机租约超时 8 秒，验收以拔线后 10 秒内释放为目标。手机根据单调时钟记录最近一次有效心跳，不信任电脑时间戳；重启 App/进程后必须新握手，磁盘旧消息不能恢复有效租约。
7. 邮箱访问或原子晋升不成立时，本阶段停止扩展并记录证据，重新评审一个同等受限的 IPC 方案；不得悄悄降级为裸露端口、无权限广播或全目录写入。

参考模式：`AppPaths.kt:33–37` 的目录定位；`PermissionManager.kt:119–145` 的前台生命周期；[ADB 文档](https://developer.android.com/tools/adb)。补丁接入复用 `patch_maafwapp.py:17–55` 的全部预检后写入机制。

### 验证与防反模式

- [ ] 用临时目录与假时钟测试握手、过期、重复、消息损坏、双会话冲突和 App 重启；协议结构错误不能建立租约。
- [ ] 手机先打开/后连接、电脑先启动/后打开 App 均能握手；单纯充电、无线 ADB、模拟器和未启动助手不触发。
- [ ] 助手退出、进程异常退出或拔线后，手机无需电脑发出结束消息也能过期；旧文件不能续租。
- [ ] 原有 App→特权服务 heartbeat、项目设置和系统设置均不改变。

## 4. Phase 2：屏保使用权与无任务生命周期

依赖：Phase 1 的租约与退出事件。

### 实施任务与文件

1. 按现有 `ScreenSaverOverlayManager.kt` 的窗口创建/删除代码实现最小使用权管理。拟区分手动使用权、任务运行 ID 使用权、USB session ID 使用权；窗口是否显示由有效使用权与用户主动退出状态共同决定。申请与释放可重复调用，并在主线程串行处理。
2. 在 `maafwapp_patches.py` 中覆盖全部消费者：手动入口、manager 的 `observePhase()`、`ScreenSaverHook`、`RunScreenSaver` 与 DI 适配。任务的两条生产链须合并职责或共享同一运行 ID，避免一条链结束时错误撤销另一条链。不能只改 USB 调用方。
3. USB 过期仅释放对应 session；任务结束仅释放对应运行 ID。手动已开启或任务仍在使用时，拔线不能关闭窗口；USB 仍有效时，任务结束也不能关闭它。
4. 用户右滑明确退出屏保后，不让同一 USB session 的下一次心跳马上重新盖屏；屏保保持关闭，直到用户手动再开、明确的新任务触发，或 USB 断开后建立新会话。抑制规则写入测试，正常续租或打开 App 不算新会话。
5. 复用现有前台生命周期，在用户打开 App、设置加载完成且租约有效时申请 USB 使用权。保留仅后台运行模式的约束；不兼容模式、缺悬浮窗权限或窗口失败时显示明确状态，不擅自切换运行模式或授予权限。
6. 审查并最小扩展 `RunForegroundService.kt` 的存活条件为“任务正在运行，或已建立的 USB 调试会话需要存活”。覆盖 onCreate、onStartCommand、观察协程和延迟后二次检查，不只改一处 busy 判断。服务从 App 前台启动，通知提供会话状态与退出操作；启动受系统拒绝时结束租约并返回失败，不假报成功。
7. 加入 overlays 单元测试，例如 `ScreenSaverOwnershipTest.kt`、`DevSessionCoordinatorTest.kt`；按现有 Koin 单例方式接入，避免重复观察者或重复窗口。进程死亡后窗口随进程移除，重启不从旧消息自动恢复。

参考模式：`ScreenSaverOverlayManager.kt:81–169,186–209`；`EnvironmentHooks.kt:213–233,360–363`；`di/OverlayModule.kt:24–30`；现有 `app/src/test/.../runner/EnvironmentHooksTest.kt:48–61,240–269` 的 `RecordingScreenSaver`、假设置和 `runTest`。

### 验证与防反模式

- [ ] 覆盖手动→USB→拔线、任务→USB→任务结束、USB→任务→拔线、重复心跳和重复释放；没有来源被误关或遗留。
- [ ] 右滑后同一会话不反复遮屏；显式退出与新会话行为符合上述规则。
- [ ] 在没有任务时打开 MAA、保持连接，屏保能持续工作；助手停止后在时限内退出，前台服务按使用理由结束。
- [x] 真机同会话观察 329.062 秒超过原 300 秒自动锁屏时长，屏保仍可右滑退出且无需系统解锁；原自动亮度/锁屏超时/ADB 常亮设置不变。见实施验收记录。
- [ ] 读回原有设置，并验证任务自动屏保、手动屏保、前台模式和权限失败路径。
- [ ] 不用 `show() == true` 判断使用权，不调用全局 `hide()` 处理单一来源，不写系统亮度/锁屏设置。

## 5. Phase 3：安全停止、源码同步与资源/Agent 重载

依赖：Phase 1；可与 Phase 2 分别开发，集中交付同一 APK。

### 实施任务与文件

1. 拟新增手机端 `debug/DevReloadCoordinator.kt`，在会话内串行处理维护请求。通过 Android 精确补丁为 `MaaRunner` 和特权服务增加受约束的维护入口；具体方法是新 API，不把 `ReloadProject` 或 `stop()` 当成现成重载。若扩展 AIDL，只追加明确 transaction ID，不改变已有编号和签名。
2. 增加维护状态，覆盖新任务启动、项目解析、停止与销毁的竞争。先请求停止，等待 runner 工作结束及 native `MaaTaskerRunning == 0`，再按已有安全顺序释放 Tasker、关联 Agent、Resource；保留 Controller 与 VirtualDisplay。整个过程有超时，未能停止时禁止修改源文件或销毁仍被访问的句柄。
3. 原来的 `destroyTasker()` 是无界轮询。实现时抽取/复用安全等待逻辑，为维护路径提供可失败的有限等待；超时保持“尚未安全停止”，不能只在 UI 上显示超时后让后台继续替换文件。
4. 电脑端计算本地内容清单和 revision（包含未提交修改的实际内容 hash），先将增量上传到该会话专用暂存区。核查目标包、APK版本、协议、文件数量/大小与剩余空间；上传中断不触碰正在使用的 PI。
5. 同步白名单从 [pi-profile.yaml](../../.github/android/pi-profile.yaml) 的实际源码载荷规则派生，覆盖所需 `interface.json`、`tasks/`、`options/`、`resource/`、`i18n/` 与 `agent/`。排除测试、缓存和本机环境；模型、解释器、native/runtime 载荷不走普通快速同步。新增依赖或白名单外内容明确要求重新构建。
6. 规范化并校验每个目标相对路径，拒绝越界、绝对路径、符号链接及路径大小写冲突；只替换/删除本工具清单管理的源码文件。保护配置、日志、角色缓存、截图、凭据、`pi.version` 与 `/data/local/tmp/maafw-agent/...`，禁止 `adb sync` 或镜像删除整个 PI。
7. 在维护锁内验证暂存 hash，记录文件级回滚日志与旧内容，再晋升变更。多目录更新不能假设单次 rename 原子完成；未完成状态必须在下次项目加载/任务启动前恢复。换包或版本变化时废弃旧会话和暂存数据，不跨 APK 继续旧事务。
8. 重新解析项目并校验选项/入口，再通过 worker 重建 Resource、Agent 和 Tasker，完成资源加载与 Agent 握手。修正路径不变和进程仍活着造成的缓存命中；可用新的源码 revision 明确使旧运行实例失效。Controller/显示配置发生变化则拒绝“保留页面”承诺并提示完整重启要求。
9. 手机返回“已暂存 / 已停止 / 已加载 / 失败”及实际 revision。确认成功必须包含已加载版本和新 Agent 实例证据，不能只看 ADB push 返回值。任何失败禁止启动不完整新版本；恢复旧源码并尝试重建，恢复失败保持停止并报告，不伪装已回退成功。
10. USB 在晋升前断开则取消；晋升后断开由手机完成当前提交或回滚，不能依赖电脑继续在线。恢复任务始终由显式操作触发，不默认从头运行“进入游戏”或执行 StartApp。

参考模式：`MaaRunner.kt:355–396,467–481,578–590,638–684` 的缓存与释放顺序；`RemoteServiceImpl.kt:337–355`；`ExecAgentHost.kt:214–223` 的 Agent 限时退出；`ProjectRepository.kt:45–63` 与 `PiInstaller.kt:123–169` 的项目载入/安装边界。

### 验证与防反模式

- [x] 相同路径 ROI/JSON、PNG 修改后 native generation 增加且实际识别分支改变；相同启动配置修改 Python 后新 Agent PID 与实际版本 B 有证据。见实施验收记录的顺序对照表；非全任务验收。
- [ ] 用可控替身覆盖停止超时、加载失败、Agent 握手失败、重复请求、并发启动和销毁；未安全停止前零文件晋升。
- [ ] 覆盖上传中断、晋升中拔线、App/特权进程死亡与下一次恢复；配置、缓存及原版本源码有可核验的保护结果。
- [ ] 从手机读回 revision 与 hash；记录 VirtualDisplay ID、Controller 复用情况和游戏进程，证明页面保留边界。
- [ ] 不用 `MaaTaskerWait()`、UI Idle 或 `isRunning == false` 单独证明原生停止；不调用完整 destroy、PI reinstall 或全服务重启实现日常重载。

## 6. Phase 4：开发命令、持续检查与文档

依赖：Phase 1–3；命令仅在相关能力完成后可用。

### 实施任务与文件

- 完成 `tools/android_debug.py` 的明确参数、状态输出、失败退出码、Ctrl+C 清理和目标绑定。`session` 在终端运行；另一个终端运行 `sync`，复用同一会话，避免每次同步建立新屏保会话。所有 ADB 子进程有超时，不拼接任意 shell 内容。
- 为工具新增独立的 `tools/tests/test_android_debug.py`，复用标准库 unittest；测试清单、路径、ADB 参数、断连与协议状态，不把临时战斗脚本直接包装为正式工具。
- 在 `.github/android/overlays/` 保存手机端生产文件与 JVM 测试；在 `.github/android/tests/` 补补丁幂等、锚点漂移、覆盖冲突及无部分写入的回归。
- 更新 `.github/workflows/install.yml` 的路径过滤及 JVM `--tests` 选择，使新增 debug/屏保/重载测试实际运行。保持原更新器、专版设置和签名检查；当前第 493–506 行的 filters 不会自动覆盖新测试。
- 在 [开发与验证指南](../开发与验证指南.md) 补操作说明、变更分类、读回证据和回退；说明 APK版本与本地源码 revision 分别代表什么。APK升级可能重置 PI 源码，之后先重新建立会话并同步。
- 电脑侧直接加载仓库的工具适合快速识别回放；控制器必须明确支持实际输入与显示映射。现有临时脚本有硬编码 ID 或空动作，不能作为通用自动战斗验收。

命令示例（手机能力须先通过集中 APK 更新交付）：

```powershell
$py = (Resolve-Path './build-debug/venv/Scripts/python.exe').Path
$deviceSerial = '<USB序列号>'
$targetPackage = 'com.overflow65537.maafw.punish.iqoo16'
$adbPath = '<ADB完整路径>'
$debugArgs = @('--adb', $adbPath, '--serial', $deviceSerial, '--package', $targetPackage)
# 终端一：启动助手，然后用户打开 MAA。
& $py tools/android_debug.py @debugArgs session
# 终端二：修改源码后同步。它会停止当前任务，不会自动开始新任务。
& $py tools/android_debug.py @debugArgs sync
& $py tools/android_debug.py @debugArgs status
# 结束：终端一 Ctrl+C，或使用 close；直接拔线由手机租约超时兜底。
& $py tools/android_debug.py @debugArgs close
```

参考模式：`patch_maafwapp.py:30–55` 与 `.github/android/tests/test_android_patch.py:21–72`；[开发与验证指南](../开发与验证指南.md) 的 Python 3.12、检查入口和真机证据要求。

### 验证与防反模式

- [ ] 在干净固定版本源码上应用补丁两次，第二次无新增差异；锚点失配时所有目标文件不发生部分修改。
- [ ] 从命令帮助、文档和 CI 都能找到新增工具与测试；测试报告确认实际命中新增类，不能只看 Gradle 返回 0。
- [ ] 源码改动、generated 文件、APK、签名材料和临时设备数据分离；调试会话不需要安装新电脑服务或修改开机启动项。

## 7. Phase 5：集中一次 APK 更新与真机验收

依赖：Phase 1–4 的最小检查通过。先完成静态与模拟验证，再集中打包一次，减少无效安装。若真机暴露 Android 原生缺陷，仍可能需要后续 APK 修订，不能承诺永远只装一次。

### 检查入口（实际执行结果见实施验收记录）

```powershell
$env:PYTHONUTF8 = '1'
$py = (Resolve-Path './build-debug/venv/Scripts/python.exe').Path
& $py -m unittest discover -s .github/android/tests -v
& $py -m unittest discover -s tools/tests -p 'test_android_debug.py' -v
& $py tools/ci/check_android_pi_profile.py
git diff --check
```

Android JVM 检查沿用 CI 的 `-Pandroid.onlyEnableUnitTestForTheTestedBuildType=false :app:testReleaseUnitTest` 与实际签名 variant，补齐新增测试过滤。涉及资源时执行适用的 Schema、资源加载与相关 pytest；不因本功能自动重复全仓所有测试。

### 真机验收矩阵

| 场景 | 必须观察的结果 |
| --- | --- |
| 助手已运行、USB 已授权，打开 MAA但不启动任务 | 自动显示已有屏保，持续超过原锁屏时长仍可退出且无系统锁屏；窗口失败或模式不兼容可见，不假报成功。 |
| 只充电、无线 ADB、模拟器、未运行助手 | 不因本功能打开屏保，不改持久设置。 |
| 拔线、关闭终端、助手异常退出 | 10 秒内释放 USB 使用权；手动/任务屏保按剩余来源保留；无任务且无会话时结束对应服务存活理由。 |
| 用户右滑屏保，同一 session 继续心跳 | 不立刻重新遮屏；重新连接形成新会话时按定义恢复自动触发。 |
| ROI/Pipeline、模板图片、纯 Python 各修改一次 | 三类分别同步成功；至少各验证一次实际识别或动作变化；APK版本/安装状态不变，新 Resource/Agent 加载与 revision 有证据。 |
| 非法资源、坏 Python、停止超时、同步中断 | 不启动半成品；回退结果明确，原配置/缓存仍在；无法安全回退时保持停止。 |
| 同步前后 | 游戏进程/VirtualDisplay 未被同步工具主动重建；从当前页面适合的入口重新验证，不默认运行 StartApp。 |
| 退出调试与后续正常启动/升级 | 原“运行时自动熄屏”“运行时保存截图”与配置行为正常；更新检查仍走既有专版渠道；升级后本地源码 revision 明确失效并允许重新同步。 |

安装前核对实际包名、versionCode、签名和源码 revision。系统要求指纹/允许安装时由用户完成；本方案减少日常重装次数，不绕过系统安装验证。

验收证据分别记录：源码/补丁检查、JVM/CLI 测试、APK构建与签名、安装、屏保行为、资源与 Agent 加载、实际游戏结果。测试或截图只能证明对应阶段，不合并描述为“全部真机通过”。

参考：[开发与验证指南](../开发与验证指南.md) 第 136–176 行；现有 Android 工作流的 metadata、badging 和证书检查。

### 回退与交付

- 会话回退：结束助手或拔线，手机自行释放 USB 使用权；不要求恢复全局亮度或锁屏设置，因为没有修改它们。
- 源码回退：通过事务日志恢复本工具上一份已验证源码与 revision，重建资源/Agent后读回；不得删除用户配置“恢复出厂”。
- Android 功能回退：在 `mine` 撤销本功能补丁/overlay并构建可覆盖安装的更高版本；不依赖低 versionCode 降级或卸载保数据。
- 交付手机端能力和工具说明；后续循环为“修改 → 相关检查 → sync → 核对已加载 revision → 当前页面验证”。Android/runtime 变化或阶段整体验收再集中打包。
- 最后审查新增 API 是否与本文及固定上游源码一致，检查是否存在错误的全局 hide、全目录同步、无界等待或未启用测试。提交和推送按届时授权执行，不由本计划自动授权。

## 8. 跨会话交接状态

| 项目 | 当前状态 |
| --- | --- |
| 用户选择 | 已确认项目调试命令启动助手、仅调试会话有效。 |
| 资料与源码发现 | 已完成定向核查；事实基线见 Phase 0。 |
| 实施变更 | 已新增 PC 会话/同步工具、共享源码白名单、Android 会话/屏保及重载模块；各阶段按模块分工集成。 |
| 检查与设备证据 | 214 项 JVM（0 失败/0 跳过）、当前 61 项 PC、33 项 Android Python 检查通过；719 个源码文件逐字节、双 ABI/runtime/policy/DEX及同证书核验通过。已覆盖安装 versionCode 2208，设备 base APK hash 与冻结源码 68111e9dc4ec 的签名产物一致；三类源码实际生效后最终 APK 版本/hash 仍相同，不等于整套设备矩阵或真实业务任务通过。 |
| 本轮收尾 | 稳定会话、防休眠及右滑复测通过；ROI、PNG、Python 三类源码的 5 次增量同步实际生效。已恢复白名单 696 个源码文件及原配置，删除临时项，正常 close 并关闭服务，02:43:12 只读确认 Asleep/系统锁屏/输入受限。此后不再操作手机；剩余设备矩阵留待后续。 |
| 尚未证明的关键边界 | 手动/任务/USB 重叠使用权、通知退出、异常退出的精确 10 秒上限与物理拔线、故障注入和断连/进程死亡恢复、登录后页面及战斗/配队等真实任务效果。 |

2026-10-23 集成补充：冻结源码快照 `68111e9dc4ec` 的本地 dirty APK 已完成集中编译、核验和覆盖安装；设备实际 base APK hash 与已验证产物一致，证据路径及 SHA-256 见实施验收记录。首轮长时采样首末间隔 373.359 秒，相关样本仍亮屏且无系统锁屏，中间窗口有 `KEEP_SCREEN_ON`；但助手意外报 `stale_challenge`，最终租约过期，此轮完整稳定会话验收未通过，不能将部分防休眠事实写成整体通过。后续优化复测与源码实际生效结果见下文。JVM 的 premain 代理仅用于本地测试环境解决 MockK attach 限制，未进入生产配置。首版 `sync` 要求当前配置有可执行的启用任务，且服务曾经通过正常运行链成功 prepare，拥有可信 payload 与既有 Controller/显示环境。首次可短跑经确认的安全任务后手动停止；`Screencap` 任务仍经过完整启动链，不作为“绝不启动应用或产生输入”的保证。空配置、未 prepare、控制器或显示变化保持明确拒绝；详细错误码见开发指南。

后续电脑端优化不更改上述 Android 冻结版本，也不需要重装 APK。已确认同一进程/实例中先出现至少 8 秒无有效心跳，超时换 challenge 后旧请求才报 `stale_challenge`；首故障缺逐条 ADB 时序，不能归责特定命令、FUSE 或手机轮询。旧循环额外 2 秒等待与约 15 次 ADB 往返已优化为固定 monotonic 节拍、受限 stdin 原子发送及逐轮时序诊断，目标绑定、8 秒租约和不自动重开保持不变。当前工具 61 项测试通过并完成独立审查，设备复测证据见下一段及实施验收记录。

后续复测现已归档：02:00:08.528 至 02:05:37.585，手机单调时钟 329.062 秒内保持同一 `6cd…` 会话 active、屏保 active，所有状态样本 Awake/无系统锁屏/KEEP_SCREEN_ON；最后用户活动时间始终不变，终点距该活动 438783 ms。观察窗口的 165 条心跳均 accepted，0 failed，随后才右滑并在同一会话变为 suppressed。另一个 `9db…` 会话右滑后 311.938 秒持续 suppressed，独立 close 正常关闭；优化后的长时会话也已正常 close。历史失败保留，后续优化复测通过；助手硬退出约 40 秒后的样本已 expired/inactive，但不能据此证明 10 秒内回收。通知操作、手动/任务重叠及物理拔线仍未验收。

源码主链验收前已完成原配置备份：手机 Downloads 中实际导出的 `FOS-config-20261023-020857.json` 已取回，16011 bytes，SHA-256 为 `036deff44ba77637bfff8c7166aa281c450d65f1afd73b6461635f3d7e17b452`，`format=maafwapp-config`、`formatVersion=1`。原有日常 18、肉鸽 6、工具 5 项任务，脱敏摘要保存在 `acceptance/live/original-config-backup-summary.json`；原始备份仅保留于 ignored 私有证据。未执行备份导入；同步后配置保留由收尾比较单独核验。

源码主链现已完成实际对照：真实 TemplateMatch 基线 `HIT/A` 后，02:36:09 至 02:37:18 串行执行 ROI `MISS/A` → 基线 `HIT/A` → PNG `MISS/A` → 基线 `HIT/A` → 纯 Python `HIT/B`。5 次同步全部 loaded、无回滚；native generation 4–8、Agent generation 5–9，每次有新 Agent PID（最后为 20604），display 8、Controller 复用及游戏 PID 7624 均保留。已读取 `probe-sequence-summary.json`、对应 sync/marker 日志并查看第 1、5 步前后标题页截图；完整 revision 表见实施验收记录。早期 DirectHit/Screencap 反复保存黑帧来自内建 action 的缓存路径，不能当作 Controller 持续黑帧证据；App 手动截图仅制模板，Controller 实际识别由命中日志证明。此项不等于登录后页面、战斗或配队通过。

最终恢复与锁屏证据已核对：`final-summary.json`、`final-source-comparison.json`、`final-config-comparison.json`、`final-session-close.json`、`final-device-runtime.json`、`final-locked-state.json`。手机与仓库白名单 revision 均为 `28cf9bd6239c…`，696 个文件实际 hash 全匹配、3 个临时 probe 文件已删；原三配置全部内容、App 设置与计划任务一致，当前“日常”，临时配置已通过界面删除。APK 仍为 2208，hash 仍为 `c5d9b9246fa012308c25580505e3481c6a2cfadd9fb7ef9c68a91e8a6ad78f70`，三类源码验证及恢复无需再次安装。

会话正常 close，助手均已结束；随后通过既有“关闭所有服务”入口主动释放虚拟显示和本次游戏进程，这与同步过程保留页面的证据分开记录。按用户要求 02:43:09 发送最后一次 `KEYCODE_SLEEP`，02:43:12.248 只读确认 `Asleep`、`keyguard_showing=true`、`input_restricted=true`、会话 `closed`、屏保 `inactive`、无 `KEEP_SCREEN_ON`；自动亮度模式 1、锁屏超时 300000、ADB 常亮 0 不变。此后未再发送手机输入。物理拔线、精确 10 秒回收上限、通知退出、手动/任务重叠、故障注入和实际战斗等未验边界保留，不再要求休息中的用户配合。
