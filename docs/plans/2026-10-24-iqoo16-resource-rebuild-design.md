# 逆冕基础补刃意设计

目标来自已接受专项方案的长期资源重建要求。当前消费者在未激活且大招未就绪时没有输入分支；这项只补单次普攻路径，真实信号与两处正式运行门继续独立验收。当前 v11d 训练源和已执行证据不随生产源码修改而改变。

## 选择与边界

在现有 `CombatInputScope` 增加 `tap_semantic`，复用同一 Controller、Down 作业有限等待、Stop 和 `_release`。目标解析后、Down 前再次调用调用方守卫；先登记责任再提交 Down，成功终态后立即 Up，不主动延时、不在按下期间采图。原生 `post_click` 需要另建迟到 Click 状态，因此首版不采用；短 `hold_semantic` 会被采图耗时拉成长按，也不采用。

首版只点按攻击。黄球输入尚缺同帧颜色、组数、槽位的实际证据；特殊球颜色和总球数不能授权黄消。点按完成仅说明输入完成，普通攻击被游戏接纳及实际刃意增长仍须设备验证。异常原生调用的物理时长和取消不由 Python 等待上限保证。

## 文件与实现

1. `agent/action/combat/input_control.py`：新增有限、单触点 `tap_semantic`。禁止已有触点／pending capture／Stop／失效 scope；保留 Down post 不确定、迟到 Down、Up 未确认的现有责任，禁止后续输入。成功后当前帧失效。可传有限绝对 `start_deadline`，拒绝bool及非有限／坏类型；None只保留通用兼容路径。
2. `agent/action/combat/actions/CombatActions.py`：新增专项 `tap_primary_input`，使用已有共享退战观察并透传开始期限，不调用普通 `attack` 的自动闪避，不改变普通模式。
3. `agent/action/combat/roles/inverse_crown.py`：保留必要结算、大招、已激活特殊球的优先级。仅在 `settlement_required`、`abyss_phase`、`slash_phase`、`special_activated`、`ultimate_ready` 五项均明确NO，无pending，原snapshot的正HP／身份／作用域／新帧及预算都有效时，一轮点按一次攻击后返回。任何UNKNOWN只观察。
4. 目标解析、共享识别之后且首Down前，再核snapshot的scope、generation、sequence、图像绑定、1秒年龄及阶段预算；输入后不能复用旧snapshot。角色必须传 `min(snapshot.captured_at + 1秒, prepare_deadline)`。Scope在最后同步停止查询之后、登记触点之前，用本地monotonic拒绝到期开始；此后到post间不追加同步查询。调用方守卫本身不能覆盖其后可能耗时的SDK调用。
5. 每次补攻保持prepare原5秒预算，不重置期限。该数值是既有安全预算，实际基础循环的充分性仍待测；超时明确失败，不能用循环刷新预算隐藏不足。

## 验证与交付

在现有输入和站场回归中覆盖点按与帧失效、Down迟到／Stop／Up失败责任、同句柄、解析或观察后过期、五项三值门、既有优先级、重复补攻不重置预算及普通模式。运行相关profile门回归，确认 `strategy_not_ready` 两门保持关闭。以冻结作者结果交独立代码审查；不重复无关全仓检查。

本项不授权ADB、部署、source-sync、APK构建或正式策略启用，不修改已执行v11d探针／707训练Stage／旧报告。仓库保持mine，保留他人dirty，不commit/push。设备点按时长、实战补资源、长期循环、20＋6和最终交付继续保留未完成。
