# 逆冕专项基础联接（2026-10-23）

这是 C 阶段的实例隔离、身份与输入保护记录，不是逆冕策略完成或真机验收。

## 当前行为

- `CombatRunner` 每次读取调用节点的 `attach.inverse_crown_profile`，创建不可变快照，再传入 `CombatTask` 和角色实例；不读账号默认阶级或上次战斗配置。
- 缺失配置默认关闭。配置对象、布尔类型、枚举与未知字段严格检查；坏配置返回 Custom 失败，不默默退回普通攻击。
- 启用时 Runner 先执行下述布局前置门；布局不完整报 `layout_calibration_required`。布局通过后，会话入口与 `BaseRole.perform()` 仍在输入前报 `strategy_not_ready`。该临时门槛阻止进入现有逆冕长按、大招和轮切逻辑；只有真实专项策略及信号消费者就绪后才能替换。
- 普通模式保留原有识别、攻击和轮切合同。Custom 成功仍只是回调完成，不表示游戏胜利。

## 内部配置合同

现阶段没有新增启动器选项或任务覆盖；以下仅供代码联接测试，不能当成已可用的战斗配置。

| 字段 | 默认值 | 允许值及当前消费 |
| --- | --- | --- |
| `enabled` | `false` | 严格布尔；驱动禁切、纯观察、独立输入作用域与未就绪拒绝 |
| `scene` | `unknown` | `single` / `multi` / `unknown`；传到角色，尚无大招分流消费者 |
| `source` | `unknown` | `self` / `trial` / `borrowed` / `mode` / `unknown`；本场来源，未知时有效阶级/专武能力不放行 |
| `rank` | `unknown` | `S` / `S5` / `SS` / `SS3` / `SSS` / `SSS3` / `SSS6` / `SSS+` / `unknown`；传到角色，尚无动作分支消费者 |
| `signature_weapon_confirmed` | `false` | 严格布尔；来源已知才形成有效装备事实，尚无返球额度消费者 |

## 身份、观察与清理

- 工厂分别保留请求类、实际 Python 类型名及回退原因；专项以实际类对象核验，`GeneralFight` 携带 `cls_name=InverseCrown` 不能算策略命中。导入失败的普通回退有明确原因。
- 专项观察不执行 `_blind_attack_tick`、provider 的攻击回调或等待攻击；场上身份未知/失配明确失败，不用 roster 冒充图像确认。
- `is_switch_disabled()` 统一供直接切人、请求切人、目标选择与可切判断消费；只读实例开关，不改全局 `SWITCH_DISABLED`。
- `input_scope` 仅在启用时建立，具有独立 generation；普通模式为 `None`。`combat_end()` 调用 `close()`；无法确认释放/停止时保留 scope 证据和 `input_stop_unconfirmed`。
- `observe_hold_state(image)` 只消费本作用域当前有效帧，复用停止、血量归零、覆盖界面与战斗 UI 丢失判断，不截图、不攻击、不闪避。持有封装先检查该共享判断，再执行角色观察回调。

## 专项布局前置门

实际入口为 `CombatRunner` → `entry/inverse_crown_layout.py:require_inverse_crown_layout()` → 既有 `LoadRoiZone.apply_offsets()`。仅 `enabled=true` 调用；普通模式不增加布局读取、采图或配置操作。

- 从当前 Context 的 `覆盖布局菜单` Custom 识别和 `识别区域` Custom 动作参数读取 `controller`；两个节点的注册名与控制器必须一致。未知值、缺失值或冲突不回退为 adb。当前专项主输入封装只支持触屏，因此 Win32 明确报 `primary_controller_unsupported:win32`，不转换键盘动作；普通 Win32 路径不受影响。
- 复用该控制器的 `cache_is_ready()` 和原缓存文件，要求八区均命中、默认框/识别框/偏移一致、整数坐标且在可用画面范围内。攻击、技能与球区的尺寸变化尚无输入缩放校准，专项拒绝放行；不生成第二缓存，不导入离线桌面缓存或硬编码手机坐标。
- 缓存完整才在同一个 Context 应用既有加载器；读回攻击、按下攻击、技能、特殊球持有和八个消球动作，以及角色/普通球/特殊球识别 ROI。动作偏移必须等于缓存平移，球动作必须与识别槽位一致，目标须落在校准区域及画面内；加载期间缓存变化也拒绝。加载器沿既有 `CheckROIZoneCache` 的生命周期模式懒建并保留，避免临时析构关闭同名日志 handlers；每次仍重新读缓存，不保存校准结果或 Context。
- 全局“缓存组件信息”的 No 继续表示普通任务不自动加载。专项要求有效校准，不改 No、不启用布局菜单、不写持久配置；缺少校准应通过手机现有手动更新组件任务生成，再在专项任务中局部加载。不能在 HUD 调用 `from_screen=true`。
- 这里的 `controller.cached_image` **仅提供当前控制器可用的缩放后几何尺寸**。它不证明当前成功截图、帧龄或本场新布局；正式策略接入后，必须将几何绑定到第一张成功的新帧。前置门通过也不证明输入命中、特殊球激活或策略已完成。

## 已验证与后续

新增回归覆盖真实 Runner 启用/坏配置输入前失败，节点到实例、开关和新实例隔离，未知来源不继承高阶能力，所有会话/角色切人入口，实际类回退，纯观察，停止与血量/UI 宽限及失败清理。

2026-10-23 布局门目标命令：`build-debug/venv/Scripts/python.exe -m pytest -c agent/pytest.ini agent/tests/test_inverse_crown_layout.py agent/tests/test_inverse_crown_profile.py -q -p no:cacheprovider --basetemp=build-debug/c-layout-gate-pytest-final --junitxml=build-debug/c-layout-gate-results.xml`，83 项通过。新用例覆盖缺失/坏缓存、控制器缺失/冲突、尺寸不可用、加载未生效、读回不一致、重载不累加、No 不修改、普通路径零新增布局读取、同名既有 logger 在首次/重复调用及对象回收后仍可输出，以及真实 Maa Context 中跨任务隔离。使用内存控制器和合成图像，没有真实设备或游戏输入。证据与源码摘要见 [verification.json](../../build-debug/c-layout-gate/verification.json)。

仍未接入：完整逆冕状态机、真实图像的最小信号、启动器 UI、游戏内本场事实确认、实机长按/停止与专项战斗验收。此阶段结果不得计作专项通关或能力实机通过。
