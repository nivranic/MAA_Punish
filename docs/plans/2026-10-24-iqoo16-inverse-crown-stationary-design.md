# 逆冕单体消费者（2026-10-24）

本次只在既有 `roles/inverse_crown.py` 增加 opt-in 单体路径及行为测试。`CombatTask.ensure_strategy_ready()` 和 `BaseRole.perform()` 的 live gate 保留；不是可部署打法或真机通过记录。普通路径沿用原分派和收尾。

专项从 `prepare` 消费当前 scope 新帧的三值快照，再选择特殊球、必要长普或单体蓄力大。快照绑定 generation、sequence、主机采图完成时间；UNKNOWN 不等于否定或成功。现有外环、近满能量和球数不足以证明激活/相/长普结算/大招，相关字段当前全部 UNKNOWN，不伪造探测器。`multi/unknown` 场景拒绝进入单体路径。

该阶段的特殊球、必要长普和蓄力大招复用 `hold_primary_input`，其共享观察及角色观察均不重新取得 Controller。特殊球成功观察到必要相/待结算后，在同一同步调用内、确认Up后立即用该次最后有效快照接长普；中间不再采图、扫角色或插QTE。该短期转接只用于这一次内部调用，核对同scope/generation/sequence、未有更新帧、无未完成输入、释放成功及预算有效；不把任意历史last metadata当新图，不建立全局输入追踪。任一hold返回False、异常、停止或观察失效均不推进后续动作，沿已有Stop/Up/异常链收尾。后续[基础补刃意](2026-10-24-iqoo16-resource-rebuild-design.md)使用同Scope的单次 `tap_primary_input`，不以短hold模拟普通点按；具体公共合同见[战斗指南4.4](../自动战斗框架开发指南.md#44-受保护阶段与后续站场合同)。

必要长普的pending优先于大招ready，直到末段完成的肯定信号才清除；外部reset不能抹去未完成阶段。单体满蓄与大招终结分开：满蓄后松开进入 `ultimate_finish`，再用新图肯定终结才回prepare。专项不调用带自动闪避的普攻/消球，不QTE、不切人；意外斩切明确失败，暂不自动恢复成多波策略。

5/10秒阶段预算和1秒主机完成时间新鲜度只是未启用消费者的有限保护值，尚未实机标定，不表示技能时长、设备帧龄或客户端1秒相窗口。解开live gate前仍需可靠最小图像信号、转接时序、长普/单体大招及真实终态验收。

严格HP已通过provider的独立 `parse_hp_fraction` / `read_hp_from_frame` 接到专项观察。持有前和持有中的同一传入帧须完整解析为 `0 < current <= maximum`；未知、零值、范围错误、坏详情或OCR异常均失败，不能开始或转入下一动作。同步OCR之后再次核scope、metadata、图像对象、主机完成时间与阶段预算；短期转接凭据携带这次已确认alive的结果，接长普时不额外识别。ROI沿当前Context HP节点，无写死iQOO ROI、扩大ROI或缓存回退；旧ROI读成10509/105会直接拒绝，校准另由当前任务完成。共享观察仍可能先读一次普通HP，专项随后在同图再做严格读取；此重复OCR及设备转接时序尚待测量，本次不加缓存框架。普通parse/read和共享session、资源、临时probe不变；探针通过不等于生产HP真机通过。

测试以现有FakeContext/FakeClock及真实CombatInputScope/动作包装注入合成信号，覆盖立即转接、pending优先、满蓄/终结分离、UNKNOWN/过期/跨generation/新帧/未完成触点拒绝、False/停止/Up失败收尾和普通模式回归。HP增量覆盖Down前拒绝、holding失败Up、正值OCR超预算/过期、同图与scope绑定，以及复用alive结果转接时不增加HP读取。没有设备操作或游戏机制通过结论。
