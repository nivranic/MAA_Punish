# v11 特殊球接长普：离线设计，尚未实现

本稿供 root 窄审。只设计一个 Android 本地同步调用内的双持有采样，不启用生产站场策略，不授权手机输入。现有游戏相态尚未建立实时检测；第二段应称“长普候选”，只有实图证据齐备后才能认作必要踏破及末段结算。

## 选择与依据

推荐从已接受 v10 的 707 文件源集合派生独立临时候选，复用原 `TrainingInputProbe` 与 `CombatInputScope`。先完成双动作离线边界，再绑定新设备前提；不从当前 dirty 源码混入其他未部署增量。

| 路径 | 当前可用性与代价 | 本次选择 |
| --- | --- | --- |
| 现有生产 `InverseCrown` 消费者 | 已有同调用转接、同帧 HP 和三值快照合同；实际相态/激活/末段检测仍 UNKNOWN，两处 live gate 保留。直接启用会缺必要真实信号。 | 保留并借用快照合同，本案不解 gate。 |
| 主机串行 ADB 持有 | 输入需跨主机调用，采图、传输及模型调度增加不可控间隔，也绕过现有 Scope 的作业归属与停止链。 | 不采用。 |
| 临时 Android 本地双持有 probe | 可在同线程、同 Scope 中完成 Up→下一 Down；改动临时模块和单次 host 校验，不增加执行框架。需补 Controller 保留、转接凭据及双动作 native gate。 | 推荐。 |

核过的文件：v10 `overlay/agent/TrainingInputProbe.py` SHA `a5682de170cbabe524e33188834632609bf33e6d14e6e978ec0fddf21634b062`；已接受 stage 的 `agent/action/combat/input_control.py` 与当前工作树该文件相同，SHA `591d2c75d912af18675527490be630b306eda666d5ff3d80dcbe9910487e08d7`。实际路径均位于 `E:/Mix/project/MAA_Punish/`。

## 必须处理的接口差距

1. 已接受 Scope 的 `_release()` 在第 267 行调用 `discard_idle_controller()`（定义在第 140 行）；首段 Up 成功后会清空 `_controller`。因此同 Scope 连续调用两次 `hold_semantic()`，仍会取得第二个 Controller，不能声称同 Controller。
2. 仅在 v11 临时 `_RecordedScope` 增加 pair 保留标志：从 before 采图前至 pair 最终收尾，暂缓 `discard_idle_controller()`。所有 Down、采图和 Up 均使用第一次取得的 Controller；正常 close 后关闭保留并调用原 discard。异常也必须先保留未确认归属，最终调用原 discard 的所有权检查，不能提前丢失 pending Job，也不能把 Controller 永久缓存到下一个 pair。pair保留限于临时子类；实际消费坐标的共享校验增量见下文审查补充。
3. `_release()` 会清空 `frame_metadata`，但保留 `last_frame_metadata`。借用当前 `inverse_crown.py:151–197,212–263` 的合同：在特殊球最后一次观察内，保留已验证 HP 与同代最后帧的局部凭据；Up 后用 `last_frame_metadata` 复验，不重新采图或 OCR。该凭据只活在本次 Python 调用中，不能成为下一 tick 的通行证。
4. `hold_semantic()` 第 290 行附近仍要经 `primary_input_position()` 解析当前 Context。保留该校验；在随后 `should_stop` 回调、尚未 Down 时再次验帧龄/预算/停止。Up→Down 间仍包含该同步 RPC 和必要校验，不能称为零延迟或物理硬截止。
5. v10 host 使用的 `NativeHoldTracker` 只接受一对 Down/Up（`stop_runner_bands.py:82` 拒绝第二个 Down），`release_path()` 也仅接受一对。两者不能原字节用于双动作案；在 v11 独立副本中改为有界两对判定，旧文件保留。

## 最小临时修改面

拟在新目录 `E:/Mix/project/MAA_Punish/build-debug/iqoo16-special-to-normal-v11-20261024/` 实施，先不创建实际 stage。

| 位置 | 仅本案需要的变化 |
| --- | --- |
| `overlay/agent/TrainingInputProbe.py` 的 `validate_parameters()` | 增加独立 `special_to_normal` mode；沿用 special 的 `primary/calibration/desired_hold_seconds`，增加固定 `normal_primary="攻击"`、`normal_calibration`、`normal_hold_seconds`。两份校准均须同 Context 解析/覆盖/读回。旧 `hold/capture_only/diagnose` 参数范围及 8 秒旧案不变。 |
| `_RecordedScope` | pair 内有界保留 Controller；事件加 `stage`，区分 special/normal 两组 Job。记录两阶段 Down 确认时间、Up 确认时间与转接检查时间。只记内存，转接期间不编码、写盘或日志。 |
| `run_probe()` | 两段均调用原 `hold_semantic()`，各自 observer 复用 `_read_frame_hp()`。special 成功且 Up 确认后才验证局部凭据并进入 normal；任何 False、异常、停止或归属不清均不进入 normal。 |
| 本案 JSON 与 host driver | 配置唯一 probe_id/ledger/output、新双点位校准与预算；延续一次 Start、无重试、原任务终态/idle/无触点后严格双按钮暂停。双动作 native 校验与归档文件名需独立更新。 |
| 本案测试 | 复用仓库 FakeClock/FakeContext 和原 probe 测试替身，新增真实 Scope 的两阶段控制边界。不添加全局注册、线程、collector 或生产参数开关。 |

Stage 拟基于 v10 已接受源 revision `905dd072dc8bf41357f434d30c736810740874ec32a773e1425276d5c57cc7c8`，增量文件及 proposed revision 在实现后计算，本文不伪造它们。临时模块保留注册名可复用现有 agent 入口；diagnose 必须匹配本案模式/参数和同 PID pending，不能沿用仅认识旧单 hold 的参数解析。

## 单次控制与凭据合同

执行前由唯一设备执行者确认实际 loaded 绑定、原任务空闲、无触点、两设置保持本案值、仅勾本案任务；取得 fresh HUD/真暂停、正 HP、两按钮几何和实际颜色，并独立记录当前角色/相态及资源前提。v10 之后的旧场资源仍未知；本文不授权黄消、重开、重复 hold，也不把旧坐标或旧 SSS+ 开场状态当当前前提。

控制顺序：

1. 用本 Task Context 校准 HP、special、normal；读回基础目标与偏移。before 采图后读取完整 `0 < HP <= maximum`，复核停止、帧龄、预算，失败零 Down。
2. 同 Scope 持有 special。每帧保存有界 BGR 副本并读取同帧 HP，OCR 后重新验停止、metadata/图像/Scope 绑定、帧龄和预算。时长达到只标记 `special_observation_reached`，不填写“满蓄/深渊已确认”。
3. observer 末帧保存局部 ticket：Scope 身份、generation、sequence、metadata 对象及 acquisition completion 时间、同帧正 HP、HP 验证后时间；与最后接收序列严格相等。绝不把新造的三值信号置 YES。
4. 原 `hold_semantic()` 正常确认 special Up。要求无 owned_contact、release_pending、capture_in_flight、down_post_uncertain；Controller 对象仍为 pair 初始对象。
5. 验票后立即调用 normal 的 `hold_semantic("攻击", ...)`。在 Context 解析后的 pre-Down `should_stop` 再验票：同 Scope/generation/最后序列与 metadata、无更新帧、正 HP、`0 <= now-captured_at <= 1.0s`、阶段及总预算仍足够。这里不读取 HP、不请求截图、不编码、不写盘、不扫描角色、不 QTE。1 秒是沿用生产消费者的未标定局部帧龄保护，不能外推为游戏深渊窗口。
6. normal Down 确认后，各新帧执行同样严格 HP/停止/归属/期限检查；正常到达其采样预算后确认 Up。必要长普是否进入正确相、末段完成、厄债或退出效果，由后验实图独立判定。
7. 有限 after 采样，最后 `scope.close()`。只有所有作业与释放确认、无 Stop 路径才编码/写盘。停止/未确认释放沿旧 `_UNCONFIRMED`、原 PID 诊断链，不写大图拖延停止。

special 已完整释放而转接票过期/预算不足时，定义明确 `normal_refused`：只有 Scope 活跃、未停止、special Down/Up已确认、无任何 pending/触点时，保留一对 special 事实、零 normal Down、clean false；不得为了“清理”伪造新 Down/Up或PostStop。停止已到、归属变化或作业未确认均走原 fail/Stop 链并保留未确认状态，不能套用 clean refusal。

## 首案预算建议（未实施、未设备标定）

常数复用 v8：capture C=0.50s、input I=0.35s、release R=0.35s、额外逻辑余量 E=0.10s。建议 special desired S=2.0s、normal desired N=4.0s、after A=1.6s、observer 最小间隔 0.40s。2/4 秒均为本案采样预算，不是满蓄或末段常数。

| 项 | 计算 | 秒 |
| --- | --- | --- |
| special hard | S+C+I+E | 2.95 |
| normal hard | N+C+I+E | 4.95 |
| 首次 Down 前必须剩余 | special hard + normal hard + 3R + A + C | 11.05 |
| normal Down 前必须剩余 | normal hard + 2R + A + C | 7.75 |
| 最低静态总预算 | before C + 两个 hard + 3R + A + C | 11.55 |
| 独立 transition mode 总逻辑预算 | 固定候选值，留 2.45s 给前置/同步检查消耗 | 14.00 |

三份 R 代表 special Up、normal Up 与一次最终收尾余量。运行时在首次 Down 前、各 `should_stop` 的 pre-Down 处重新比较剩余预算；持有中在总截止前预留当前释放与剩余必要收尾。预算耗尽不能进入后段，也不自动延长。所有 native/Context/OCR 调用仍为同步，逻辑期限不能硬中断一个卡住的原生调用。

保守帧计划：before 1 + special `ceil(2/.4)+1=6` + normal `ceil(4/.4)+1=11` + after `ceil(1.6/.4)+1=5` = **23**；硬 cap **24**。每帧 BGR `1280*720*3=2,764,800` bytes，24 帧 **66,355,200** bytes，小于 **73,400,320** bytes 的 70MiB。cap 与内存检查仍逐帧执行，后段开始前须预留该段及至少一张 after；预算不能满足时拒绝，不拿“补跑”凑帧数。

v10 的 after+落盘已显示 12 帧编码/持久化约4.592s；双阶段最多24帧不能机械沿用20s host collection。建议本案固定 **35s** host 收集上限，覆盖14s逻辑观察及约束内持久化/任务终态；这是独立新案收集预算，不修改或回证旧案时限。若未收齐，保持原 PID 与 pending，禁止第二 Start/重载/换 probe；上限仍不保证物理截止。

## 证据与回归门

host 正常放行暂停的证明改为：同一精确参数 callback、同 native PID/display/绑定、同一 Task 终态与 run footer；成功事件严格 `DownS→UpS→DownN→UpN`，contact0、两点位各自匹配、四个不同 Job，任何失败/多余触摸拒绝；再核 idle 与无触点。partial `DownS→UpS` 只有明确 normal refusal 和无第二 Down 证据时，可安全暂停但本案不是双动作 PASS。零 Down 沿 clean preinput refusal。Stop/迟到/未确认分支须原 PID 取证，不能伪装正常双段。

记录 special Up 成功→normal Down wait 开始的本地间隔，以及同 native 日志的 UpSucceeded→DownStarting/DownSucceeded 间隔；post 本身可能同步耗时，wait 开始不能冒称精确发射时刻。主机与手机单调时钟不可相减。捕获事件带 stage/generation/sequence/job；PNG、raw BGR、各 capture native 回调逐一核对。文件名使用 before/special_holding/normal_holding/after_release，host 归档正则随独立副本更新。

实施前测试须涵盖以下行为，不以实现字节比较替代：

- 两段、before 与 after 只读取一次 Tasker.controller getter；Down/Up/capture 始终同对象；close 后允许释放，pending Job/触点时绝不丢引用，不跨 pair 复用。
- special False、HP未知/0、capture失败/迟到、Down不确定、Up失败/迟到、Stop任一处：normal Down为0；normal 阶段同类失败只释放本段，不补攻击/重试。
- 正 HP OCR 后发生停止、换代、metadata/Scope/图像/序列替换、票过期/未来时间、Context RPC跨期限：无normal Down；含 special Up 成功但耗尽帧龄和总预算。
- 正常转接的采图/OCR/编码/写盘计数在 special Up 与 normal Down 之间均不增加；停止和目标/票据校验仍执行。
- 两段参数/几何消费者一致，坏normal目标/键盘/非contact0在首Down前拒绝；旧mode 8秒合同与原模块hash均不变。
- 23帧计划与24帧/70MiB cap、后段预留、无负sleep、14秒前中预算、无持有中落盘；正常和失败归档/host双段解析正反例。
- 原host gate对零对、一对refusal、完整两对、多一Down/Up、混PID/display/参数、任务未终态、仍有触点逐一验证；严格暂停新正例和HUD/加载/失败反例仍保留。

本设计未运行以上新回归。实现、独立窄审、freeze与实际 loaded 绑定全部完成后，root才可放行唯一现场案。控制验证结果与游戏业务效果分开：两对输入/释放和归档齐全可以证明控制链；要认定必要踏破，还需正确相/末段/资源与退出的组合实证，单靠4秒、伤害或满蓄持续buff不足。

## v10 标注边界

`E:/Mix/project/MAA_Punish/build-debug/iqoo16-v10-visual-review-20261024/effect-review.json` 已有可信人工正证：前置无状态、事后新增“残心·无我”，文本为“逆冕释放满蓄力的源响或终声后获得”；本轮唯一蓝槽输入支持终声路线。该标签属于整次运行的后验结果，不能硬贴到某一持有帧当精确满蓄完成时刻，也不能把后续持续出现的buff当新一轮事件。

本目录 `v10-labels.json` 只从该已冻结独审及manifest提取12帧的可见颜色/计数/刀型与归档阶段，逐帧激活、准确满蓄、深渊相、必要长普完成均保持未标注。003首次颜色/刀型变化、005新图标、006–007的12/16、008–012的11/16可作后续标注线索；不把它们解释为360刃意、厄债或相倒计时。

## 实施审查补充：实际消费坐标

独立故障注入已复现：开局校准后修改Context坐标，特殊球或长普仍可能把漂移点传给Down。共享 `CombatInputScope.hold_semantic()` 因此新增可选 `position_validator`：将本次 `primary_input_position()` 已解析、随后实际消费的不可变坐标元组交给校验器，严格返回True才继续登记/发射Down；不通过另一遍Context读取来证明前一次解析。默认None保留旧调用路径，校验期间停止仍会拒绝Down。

v11按各阶段已登记的期望点严格校验。首段漂移零Down，第二段漂移只保留首段已释放事实，以 `normal_refused=position_drift` 拒绝，不能计双段PASS。v11部署须显式包含该Scope精确overlay，pair Controller保留仍只在临时子类实现，不混入其他未接受生产增量。原设计、原失败探针与首次实现冻结继续保留。

## 实施审查补充：异常场景收尾

**授权状态（2026-10-24更新）：**自动审批此前拒绝新增关闭游戏代码，理由为可能丢失未保存状态且当时的用户授权未明确覆盖；该历史拒绝保留。用户现已明确答复“允许，仅用于上述异常收尾”：仅限本轮免费训练，两段动作均已释放、原任务已结束且无活动触点，仍无法确认暂停时，允许执行 `am force-stop com.kurogame.haru.hero`，可能丢失未保存的训练进度。下文约束在该限定授权内适用，不扩展至零输入失败、一般任务或其它场次。具体实现、部署及验收状态以对应候选和运行收据为准；本次只更新授权记录，不修改已执行探针或认定新分支已部署。

两段结束后，host须在原Task/run终态、释放/无pending、idle和无活动触点全部确认后，再请求当前Controller场景图，并重新核绑定。精确HUD才发送一次暂停；已暂停或明确失败直接记安全终态。若场景未知或一次暂停后仍不能确认终态，在上述释放证明仍有效、绑定未变且游戏包已预先核实的前提下，可对本案免费训练的 `com.kurogame.haru.hero` 做一次有界force-stop并核进程不存在，避免活场留待下一轮；结果记FAIL/INTERRUPTED，不能计双动作PASS或通关。关闭失败明确记终态未确认。

原任务、释放或绑定任一未确认时，不执行暂停或关闭游戏，保留原PID的pending诊断边界。此分支不清数据、不关闭助手/native进程、不重载或重试Start；只适用于本案已登记的免费训练，不扩展到一般任务。对应fakeIO回归须验证干净释放后unknown只关闭一次、未确认释放绝不关闭，以及关闭失败的报告。
