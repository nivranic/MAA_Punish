# 2026-10-23 USB 主机 heartbeat 预算修复记录

本次只修改电脑端 `tools/android_debug.py` 与对应测试。手机 8 秒 lease、协议版本、身份/审计校验与 APK 均未修改；新 helper 是独立主机交付，不能把此前 APK 构建快照的工具 hash 当作本文件的新版本。

## 已确认的问题

- `build-android/iqoo16-c-device-20261023T1445/host-c-session.log` 的 seq 204：`usb_serial=750 ms`、`usb_state=2625 ms`，合计 3375 ms，尚未进入 `mailbox_exchange` 就失败。此前 seq 201/202/203 分别用时 1485/2734/2375 ms。原 `Session.send()` 的 3 秒预算覆盖全部 USB 校验、发布和 ACK 读取。
- `build-android/iqoo16-b-device-20261023T135403/host-audit-session-2.log` 的 seq 779：单次 `usb_serial=5797 ms`。Windows 进程启动或超时收尾可能超过传给 subprocess 的 timeout，不能承诺硬截止时间。
- `lease_remaining_ms` 是手机写状态时的快照。收到 ACK 的本机时间加上该快照不代表当前剩余租约，不能据此延长主机预算。

## 已实施的时间语义

1. `UsbAdb.write(open)` 在实际 `mv` 前、`exchange_heartbeat` 在实际发布 shell 前回调本机 `time.monotonic()`。这是本轮最早可能发布的保守起点，不是手机实际处理时刻。
2. `Session.publication_started` 只保存本轮候选；只有精确的 accepted ACK 通过 package、app instance、challenge、session、host、sequence 和 audit mode 校验后，才提升为 `last_confirmed_publication_started`。
3. 默认 heartbeat 单轮最多 5 秒，且截止时间为 `min(本轮开始 + 5, 上次已确认发布起点 + 8 - 0.5)`；显式较短的 `wait_seconds` 仍生效，较长值不超过 5 秒。0.5 秒是主机保守余量，不是 OS 调度保证。
4. `open` 的 ACK 等待仍默认 3 秒，但同时受其发布起点加 7.5 秒限制。写入收尾或 ACK 返回已越界则失败，不能把迟到的 accepted ACK 当作可续租会话，也不会将这种错误交给初始 mailbox 重试分支。
5. 未知或未确认发布、预算耗尽、失配、拒绝和会话过期均结束可续租状态。每个 heartbeat ADB 调用前使用剩余预算，调用返回后再核对截止时间；晚到结果不能算成功，也不发下一条请求。已启动的 OS/远端调用无法被这一检查追溯取消。
6. 固定 shell 在原子 `mv` 后 `sleep 0.3` 再读取状态，为手机通常约 250 ms 的轮询留出一次机会。它仍受同一剩余 timeout 约束；未取得匹配 ACK 时保留独立 USB 校验和有界读取。单次事务只发布一次，不循环写入，不增设 daemon。
7. 两秒 monotonic 节拍继续跳过错过的 tick，不连发追赶。独立 close mailbox 与 `sequence=0` 保持可用，失败后的 best-effort close 不恢复续租状态。

日志保留 `lease_snapshot_ms`、各 ADB 阶段时长和 ACK 观察时间，增加 `budget_remaining_ms`、`lease_bound_monotonic_s`、`publication_started_monotonic_s`。负的剩余预算直接显示 OS 调用超出的时间；发布起点为 `none` 表示尚无该轮发布候选，不能解读成成功续租。

## 已验证与交付

在 PowerShell 7、Python 3.12.10 下执行；`TEMP`/`TMP` 和 pytest basetemp 都使用仓库 `build-debug/` 内独立目录，禁用 pytest cache：

```powershell
$env:PYTHONUTF8 = '1'
$testRoot = Join-Path (Get-Location) ('build-debug/heartbeat-budget-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$env:TEMP = $testRoot
$env:TMP = $testRoot
& ./build-debug/venv/Scripts/python.exe -m pytest tools/tests -q -p no:cacheprovider --basetemp (Join-Path $testRoot 'pytest')
```

- 初次定向故障注入：22 passed、53 deselected、11 subtests passed。
- 补充迟到 open 不重试后完整 helper：81 passed、842 subtests passed，2.92 秒。
- 回归覆盖慢 USB 超过旧 3 秒但仍在保守租约内成功、早发布晚 ACK、open 越界、未知/未确认发布、不再写入、拒绝/过期/身份/审计/序列失配、单调用超预算、独立后续读取、节拍跳过和 close 0 序列。
- `tools/android_debug.py` SHA-256：`BF1871235E49EA8767AF232106A4A82AB26CE69CDA31B43413799084F1AC2D5D`。
- `tools/tests/test_android_debug.py` SHA-256：`BA41EF62B2D704075CEB83A7ABA9FF542199090BBC23C5F1C129C4D983B6114D`。

本轮未执行 ADB、操作手机或安装 APK，未执行 commit/push。测试使用进程/手机替身，只证明主机预算与失败控制流；固定 sleep 是否减少真实 ADB 调用、当前 Windows 负载下的持续会话稳定性、拔线后的手机释放时限，仍须由唯一设备执行者在独立审查后验收。旧失败日志保留，不能用本次模拟通过覆盖历史故障。
