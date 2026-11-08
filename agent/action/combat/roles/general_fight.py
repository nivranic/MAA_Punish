# Copyright (c) 2024-2025 MAA_Punish
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""通用战斗程序

状态机::

    idle ──► farm（5 连普攻 + 5 连消球2，直至大招条满）──► ult（技能 + 5 连普攻 + 5 连消球2）──► switch
              └── 长时间未出大 ──► switch
"""

from __future__ import annotations

import time

from action.combat.core.role import BaseRole

_FARM_MAX = 100
_BALL_SLOT = 2
_ATTACK_BURST = 5
_BALL_BURST = 5
# 连打间隔（毫秒）：0 太快（实测输入易被吞），先用 25ms
_BURST_INTERVAL_MS = 25
_ULT_POLL_INTERVAL = 0.05


class GeneralFight(BaseRole):
    """通用兜底：盲按 2 号球攒大 → 大招连段 → 切人。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._farm_ticks = 0

    def reset_state(self) -> None:
        super().reset_state()
        self._farm_ticks = 0

    def _burst(self) -> bool:
        """一轮：连续 5 次普攻 → 连续 5 次消球，每下间隔 25ms，每次输入前检查共享预警。"""
        if not self.action.attack_burst(_ATTACK_BURST, _BURST_INTERVAL_MS):
            return False
        return self.action.ball_elimination_burst(
            _BALL_SLOT, _BALL_BURST, _BURST_INTERVAL_MS
        )

    def _skill_energy_ready(self, *, fresh: bool = False) -> bool:
        """采帧和能量识别分别检查停止，调用者再区分停止与普通未命中。"""
        if self.combat.context.tasker.stopping:
            return False
        image = self.action._get_image(fresh=fresh)
        if self.combat.context.tasker.stopping:
            return False
        ready = self.action.check_Skill_energy_bar(image=image)
        return not self.combat.context.tasker.stopping and ready

    def do_perform(self) -> None:
        if self.combat.context.tasker.stopping:
            return

        if self.phase == "idle":
            self._phase_idle()
        elif self.phase == "farm":
            self._phase_farm()
        elif self.phase == "ult":
            self._phase_ult()
        else:
            self.phase = "idle"
            self._phase_idle()

    def _phase_idle(self) -> None:
        self.action.logger.info("通用战斗: 开始")
        self.action.lens_lock()
        if self.combat.context.tasker.stopping or not self._burst():
            return
        self._farm_ticks = 0
        self.phase = "farm"

    def _phase_farm(self) -> None:
        ready = self._skill_energy_ready()
        if self.combat.context.tasker.stopping:
            return
        if ready:
            self.action.logger.info("通用战斗: 大招就绪")
            self.phase = "ult"
            return

        if self._farm_ticks >= _FARM_MAX:
            self.action.logger.info("通用战斗: 未攒出大招，直接切人")
            self.phase = "switch"
            return

        if not self._burst():
            return
        self._farm_ticks += 1

    def _phase_ult(self) -> None:
        timeout = self.action.SKILL_BURST_TIMEOUT
        deadline = time.monotonic() + timeout
        presses = 0
        self.action.logger.info("通用战斗: 大招循环（技能 + 5 连普攻 + 5 连消球）")

        while time.monotonic() < deadline:
            if self.combat.context.tasker.stopping:
                self.action.logger.info("通用战斗: 大招中止 presses=%d", presses)
                return
            self.action.use_skill()
            if self.combat.context.tasker.stopping:
                return
            presses += 1
            ready = self._skill_energy_ready(fresh=True)
            if self.combat.context.tasker.stopping:
                return
            if not ready:
                self.action.logger.info("通用战斗: 大招结束 presses=%d", presses)
                break
            if not self._burst():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_ULT_POLL_INTERVAL, remaining))
            if self.combat.context.tasker.stopping:
                return
        else:
            self.action.logger.warning(
                "通用战斗: 大招超时 %.0fs presses=%d", timeout, presses
            )

        if self.combat.context.tasker.stopping:
            return
        self.phase = "switch"

