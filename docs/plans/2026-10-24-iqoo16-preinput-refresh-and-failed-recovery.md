# iQOO16 首输入前重采与失败收尾 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复已确认的 OCR 冷加载首帧过期，并让证据充分的零输入失败获得有限安全观察和暂停，始终保留失败结果。

**Architecture:** 在新的 v11d 候选目录复用已审 v11c，实现首 Down 前最多一次新帧重采，以及与完整动作验收分开的零输入失败收尾。所有原始失败、v11c 冻结、生产源码及两处策略门保持；候选离线测试和独审通过后，另行准备现场、绑定及唯一执行。

**Tech Stack:** Python 3.12、MaaFramework v5.14.2、现有 TrainingInputProbe / CombatInputScope / pair_native / normal_pair_once。

---

## 已确认基线与工作边界

- 分支 `mine`，保留 dirty；本计划不授权 commit、push、APK 安装、新场或设备输入。候选隔离在 `build-debug/iqoo16-special-to-normal-v11d-20261024/`，无需改动共享工作树的既有实现。
- 原唯一 run 为 `run_20261024_170707_1`，原控制结论 `UNCONFIRMED`、任务 `COMPLETED_WITH_FAILURES` / native Failed，四个触摸 Job 均未产生。
- [实际 manifest](../../build-debug/iqoo16-device-live-20261024/v11c-fixed-original-archive-20261024T171923/manifest.original.json) SHA `d454891f52017a83fbd9bca82a2a5dd68910066f971c2cd38914775c6759c314`：`before_stale_frame`，`CombatInputError: transition frame expired after HP recognition`，HP 10509/10509、一个 before 帧、cleanup confirmed、persistence complete。原 run Agent 行给出相同异常。
- 原 native 显示首 HP RPC 1071ms，其中包含 OCR 模型冷加载。旧帧记录到 cleanup 已约 1.117s，超过现有 1.0s acquisition-completion age 限制。这不是设备帧龄或技能时长。
- v11c driver 在完整 pair proof 失败后退出，未进入场景观察；实际 footer `COMPLETED_WITH_FAILURES` 和非空拒绝异常未被旧 zero fixture 覆盖。事后画面已为失败结算页，未发送暂停或关闭，不回填原 live 接受。
- v11c 的 probe `ce3e8d71…`、Scope `d1119eb8…`、driver `521a5379…`、parser `d0597221…` 及全部冻结不修改。新候选不混入生产颜色增量。

## Task 1: 首输入前最多重采一次

**Files:**
- Create: `build-debug/iqoo16-special-to-normal-v11d-20261024/probe/TrainingInputProbe.py`，从 v11c Stage 原字节复制后局部修改。
- Create: `build-debug/iqoo16-special-to-normal-v11d-20261024/tests/test_before_refresh.py`。

1. 先用可控 monotonic、capture 和 HP 识别延迟重现：首次 capture 成功、HP 正且随后 age > 1s，旧实现首 Down 前拒绝。保留失败输出。
2. 仅在尚未发任何 Down、无 pending / Stop / 身份或线程变化、原帧及 HP 合法而**仅 age 过期**时，最多重采一次。
3. 第二张必须由同 Scope 实际新 capture Job 生成，重新读取 HP、校验本代递增 sequence、当前 owner 与状态；沿用 1s 门及 Down 前预算检查。
4. 不重置14秒 deadline，不改2/4/1.6/.4预算，不增加24帧及70MiB上限，不在特殊球释放到普攻持有之间插入刷新。第二次仍慢、预算不足、HP无效、识别异常、Stop、绑定或触点状态不确定时直接拒绝。
5. 冷帧和新帧都保留为 `before`，原 PNG、raw hash、capture Job 和 HP 观察均归档，不隐藏第一次 capture。

新增 manifest 字段仅在实际刷新时存在：

```json
{
    "before_refresh": {
        "reason": "hp_age_expired_before_first_down",
        "limit": 1,
        "generation": 1,
        "old_sequence": 1,
        "new_sequence": 2,
        "old_observed_at": 2.2,
        "new_observed_at": 2.4,
        "max_age_seconds": 1.0
    }
}
```

示例时间是同一 Scope monotonic 占位值，必须写实际值。`old_observed_at` 是第一次 HP 完成时刻，新值是第二次 HP 完成时刻；分别与对应帧 `captured_at` 推导 >1s / <=1s。标记只描述可恢复的过期观察，其他不安全事实不能被刷新洗掉。第二次未成功也保留实际刷新记录及最终拒绝，不制造成功标记。

运行：`build-debug/venv/Scripts/python.exe -B -X utf8 -m pytest -q -p no:cacheprovider build-debug/iqoo16-special-to-normal-v11d-20261024/tests/test_before_refresh.py`。

## Task 2: 归档完整消费第二个 before 帧

**Files:**
- Create: `build-debug/iqoo16-special-to-normal-v11d-20261024/pair_native.py`，从 v11c parser 原字节复制后局部修改。
- Create: `build-debug/iqoo16-special-to-normal-v11d-20261024/tests/test_pair_archive_refresh.py`。

1. 无刷新标记仍只接受一个 before；两个 before 必须有上述完整标记，并严格核类型、有限时间、同代、相邻序号、捕获顺序和旧新 age。
2. 冷帧、新帧与其余帧均须逐一对应 native capture、PNG/raw/shape/Job；不能让一个未记入 manifest 的 capture 通过。
3. 冷→暖计划由23帧变为最多24帧，raw为66,355,200字节；第三帧、缺标记、伪造序号、错误年龄、缺旧帧或 native 对应均拒绝。
4. 保留完整 pair 原四触摸、参数、Scope cleanup 与正常 handoff 验证，不将刷新标记当作动作完成或机制证据。

## Task 3: 零输入失败的有限安全收尾

**Files:**
- Create: `build-debug/iqoo16-special-to-normal-v11d-20261024/normal_pair_once.py`，从 v11c driver 原字节复制后局部修改。
- Test: `build-debug/iqoo16-special-to-normal-v11d-20261024/tests/test_zero_input_recovery.py`。
- Parser helper 如确需新增，仅在 Task 2 的新 parser 中实现；两位执行者先协调该文件归属。

1. 先以**真实形状**建立失败回归：非空 `before_stale_frame` 异常、footer `COMPLETED_WITH_FAILURES`、原 native Failed + 完整 inactive、无任何触摸 Starting，复现旧 driver 未观察场景。
2. 完整 pair release / archive / force-stop 的原门保持。另设局部纯判断，允许已证明的零输入失败进入现有 fresh 场景观察。
3. 证明必须包括：原唯一 callback / task / bound run、相容失败 footer、成功完整 inactive、没有 invalid / Stop / pending / 失败或额外 Controller Job。检查所有原始 job 的 Starting 和终态，绝不能以 `touches=[]` 代替零触摸证明。
4. native 原窗口必须能证明从 pre-start offset 到当前 drain 连续；身份、区间、实际读取字节数及必要原始来源不足就拒绝。仅 `size < cursor` 检查不充分；不以一个自报 `complete=true` 标签代替来源。
5. 复用既有当前 metadata、进程、owner、idle、noTouch 及 manual capture。fresh 已是 pause / failure 时0输入，fresh HUD时最多Pause1并确认；未知、漂移、异常、超时、传输不确定均不重试。
6. 结果单独保留失败与 recovery 状态。不得进入完整 pair `CLOSABLE_RESULTS`，不得获取 force-stop 资格，不得因为安全终态成立就调用完整 pair 归档验证并宣称通过。失败诊断归档与完整 pair 验收分别记录。

必测：真实错误/footer形状；仅 Down Starting 且 touches 空；任意 partial / Failed / 多余 Job；Stop；suffix 短读、缺口、旋转或不确定；task / lifecycle / metadata / owner / PID / boot / display 漂移；scene 和 Pause 前后异常；无 manifest 或 manifest 冲突。所有失败恢复场景 close 恒为0。

## Task 4: 窄回归与独立审查

1. 执行新增三组定向测试，沿用现有本地 fixture；保留首次失败、命令与输出。必要时运行受影响的原 host / lifecycle / archive 用例，不机械重复全仓测试。
2. 独立审查新旧差异、真实 v11c 失败字节重放、旧完整 pair 正例、24帧边界和所有反例。单元/回放不计真机通过。
3. 冻结新 probe/parser/driver/测试与来源 hash；保留 v11c 原件。Scope 若无必要不改。

## Task 5: 新候选与后续真机

由 root 在离线修复独审通过后处理，不属于本计划当前设备输入许可。

1. 新版本候选从已接受 v11c 的707源出发，显式列出 probe 和 JSON 差异；不复制当前生产 dirty 源。
2. 新免费场、新 HUD / 真Pause / 实际参数、当前会话和 owner 分别取证；旧失败页不复用为 fresh 前置，不复跑 `special-normal-v11c-01`。
3. 沿既有 standard CLI / daemon / heartbeat 与真实 native 版本来源，重新 source sync 和 A/B 绑定；真实原字节、独立精确输入审查、最终纯 validator 通过后，root另行授权唯一新 case。
4. 本次修复目标首先是冷→暖真实执行与失败后安全暂停；满蓄、踏破、必杀和正式 C 仍分别验收。没有完整双段证据时不允许异常关闭。

## Task 6: 台账与交接

主账和接续文件追加本轮实际修改、离线证据、现场结果和未完成项。20日常、6肉鸽、正式单体/多波/长期、最终APK、还原和清理范围不变。恢复项仍包括正式704源、原配置和两设置true、训练攻击2/生命1、临时日志及helper关闭/清理/复查。
