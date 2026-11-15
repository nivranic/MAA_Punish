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

"""终焉战斗程序

状态机::

    idle ──大招条──► ult_open ──► ult_loop ──► ult_close ──► switch ──► idle
      └──无大──► farm ──► idle
"""

from __future__ import annotations

from action.combat.core.role import BaseRole
from action.combat.core.role_detect import is_dodge_button_visible
from action.combat.actions.oblivion_form import classify_oblivion_form
from action.combat.actions.signal_ball_layout import signal_ball_slot_centers

_CRESCENT_NODE = "检查残月值_终焉"
_CLEAR_MAX = 5
_FARM_MAX = 30
_ULT_LOOP_MAX = 30
_SKILL_DELAY_MS = 1000
_CRESCENT_PRESS_MS = 2000
_OPENING_CHARGE_MS = 2500
_MAIN_CHARGE_MS = 2500


class Oblivion(BaseRole):
    """终焉：攒大 → 主序蓄力核心 → 二段大 → 切人（不使用 QTE）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._farm_ticks = 0
        self._ult_ticks = 0
        self._clear_ticks = 0
        self._opening_charge_attempted = False
        self._open_cast_requested = False
        self._close_cast_available = False
        self._close_cast_requested = False

    def reset_state(self) -> None:
        super().reset_state()
        self._farm_ticks = 0
        self._ult_ticks = 0
        self._clear_ticks = 0
        self._opening_charge_attempted = False
        self._open_cast_requested = False
        self._close_cast_available = False
        self._close_cast_requested = False

    def do_perform(self) -> None:
        if self.combat.context.tasker.stopping:
            return

        if self.phase == "idle":
            self._phase_idle()
        elif self.phase == "farm":
            self._phase_farm()
        elif self.phase == "ult_open":
            self._phase_ult_open()
        elif self.phase == "ult_loop":
            self._phase_ult_loop()
        elif self.phase == "ult_close":
            self._phase_ult_close()
        else:
            self.phase = "idle"
            self._phase_idle()

    def _phase_idle(self) -> None:
        self._open_cast_requested = False
        self.action.lens_lock()
        self.action.attack()

        if self.action.check_Skill_energy_bar():
            self.phase = "ult_open"
            return

        self._farm_ticks = 0
        self._clear_ticks = 0
        self._opening_charge_attempted = False
        self.phase = "farm"

    def _finish_farm_if_ready(self) -> bool:
        """Yield on ready or stop; the next tick verifies before casting."""
        ready = self.action.check_Skill_energy_bar()
        if self.combat.context.tasker.stopping:
            return True
        if ready:
            self._open_cast_requested = False
            self.phase = "ult_open"
        return ready

    def _clear_balls(self) -> bool:
        """Perform one elimination pair, then yield to fresh HP/identity checks."""
        if (
            self._clear_ticks >= _CLEAR_MAX
            or not self.action.count_signal_balls()
            or self._finish_farm_if_ready()
        ):
            return False
        self.action.ball_elimination_target(1)
        self._clear_ticks += 1
        self.action.attack()
        return True

    def _crescent_press_if_ready(self) -> bool:
        if not self.action.check_status(_CRESCENT_NODE):
            return False
        if self.combat.context.tasker.stopping:
            return False
        if self._finish_farm_if_ready():
            return False
        self.action.long_press_attack(_CRESCENT_PRESS_MS)
        return True

    def _phase_farm(self) -> None:
        if self._finish_farm_if_ready():
            return
        if self._farm_ticks >= _FARM_MAX:
            self.phase = "idle"
            return

        if not self._opening_charge_attempted:
            form, hud_ready = self._observe_form()
            if self.combat.context.tasker.stopping or not hud_ready:
                return
            if form == "normal":
                # One bounded opening charge builds crescent while reducing
                # exposure before the first ultimate. Never infer its success;
                # the next tick must recheck live identity, HP and energy.
                if self._finish_farm_if_ready():
                    return
                self._opening_charge_attempted = True
                self.action.long_press_attack(_OPENING_CHARGE_MS)
                return

        # Do not hold a ready core behind the remaining elimination batch.
        # A request consumes this tick; the next action needs fresh identity/HP.
        core_requested = self._crescent_press_if_ready()
        if self.combat.context.tasker.stopping or self.phase != "farm":
            return
        if core_requested:
            self._clear_ticks = 0
            self._farm_ticks += 1
            return
        if self._clear_ticks == 0:
            self.action.attack()
        if self._clear_balls():
            return
        if self.combat.context.tasker.stopping or self.phase != "farm":
            return
        self.action.attack()
        self._clear_ticks = 0
        self._farm_ticks += 1

    def _phase_ult_open(self) -> None:
        form, hud_ready = self._observe_form()
        if self.combat.context.tasker.stopping:
            return
        if form == "main":
            self.action.auxiliary_machine()
            self._ult_ticks = 0
            self._clear_ticks = 0
            self._close_cast_available = True
            self._close_cast_requested = False
            self.phase = "ult_loop"
        elif hud_ready:
            if form == "normal":
                energy_ready = self.action.check_Skill_energy_bar()
                if self.combat.context.tasker.stopping:
                    return
                if energy_ready:
                    self._open_cast_requested = True
                    self.action.use_skill(_SKILL_DELAY_MS)
                elif not self._open_cast_requested:
                    # A transient ready hit must not strand unspent yellow
                    # orbs here. After a cast, still wait for positive main
                    # form evidence rather than assuming its animation ended.
                    self._farm_ticks = 0
                    self._clear_ticks = 0
                    self.phase = "farm"
                else:
                    self.action.attack()
            else:
                self.action.attack()

    def _observe_form(self):
        image = self.action._get_image()
        context = self.combat.context
        if not is_dodge_button_visible(context, image):
            return "unknown", False
        hp = self.combat.combat_check.read_hp_from_frame(context, image)
        if not hp.alive:
            return "unknown", False
        count = self.action.count_signal_balls(image=image)
        sparse_normal = False
        sparse_main = False
        if type(count) is int and 1 <= count <= 2:
            if self.phase == "ult_open":
                # Re-entering after a rotation may leave only one yellow orb.
                # A living HUD, all occupied orbs yellow and ready energy on
                # this frame can authorize opening without waiting for three.
                sparse_normal = self.action.check_Skill_energy_bar(image=image)
            elif self.phase == "ult_close" and self._close_cast_requested:
                energy_ready = self.action.check_Skill_energy_bar(image=image)
                sparse_normal = not energy_ready
                # A closing request can leave two main-form orbs unchanged.
                # Only fresh live, ready evidence can permit another request.
                sparse_main = energy_ready
        centers = signal_ball_slot_centers(context)
        form = classify_oblivion_form(
            image, centers, count,
            allow_sparse_normal=sparse_normal,
            allow_sparse_main=sparse_main,
        )
        logger = getattr(self.action, "logger", None)
        if logger is not None:
            logger.debug(
                "Oblivion form phase=%s form=%s balls=%s hp=%r opening_charge=%s slots=%s",
                self.phase, form, count, hp.text,
                getattr(self, "_opening_charge_attempted", False), centers,
            )
        return form, True

    def _phase_ult_loop(self) -> None:
        if self.action.check_Skill_energy_bar() or self._ult_ticks >= _ULT_LOOP_MAX:
            self.phase = "ult_close"
            return

        form, hud_ready = self._observe_form()
        if self.combat.context.tasker.stopping or not hud_ready:
            return
        if form == "normal":
            self._close_cast_available = False
            self._close_cast_requested = False
            self.phase = "idle"
            return
        if form != "main":
            return

        # Charging itself builds crescent (40/s, capped at 90) and protects
        # the character. Do not wait through several exposed elimination
        # batches for a broad red-pixel predicate to report a full gauge.
        # The bound also permits an empty gauge to fill; the request alone
        # does not confirm a release. Yield to fresh identity/HP afterwards.
        ready = self.action.check_Skill_energy_bar()
        if self.combat.context.tasker.stopping:
            return
        if ready:
            self.phase = "ult_close"
            return
        self.action.long_press_attack(_MAIN_CHARGE_MS)
        self._clear_ticks = 0
        self._ult_ticks += 1

    def _phase_ult_close(self) -> None:
        form, hud_ready = self._observe_form()
        if self.combat.context.tasker.stopping:
            return
        if form == "normal":
            self._close_cast_available = False
            self._close_cast_requested = False
            self.action.auxiliary_machine()
            # 黄球正向确认退出主序后再交还切人；无队友由既有回落攒大。
            self.phase = "switch"
        elif hud_ready:
            # This visit to main was positively observed before ult_loop.
            # Its closing attack must still be requestable after consuming the
            # last orbs. Spend that permission before input; later attempts need
            # fresh main-form evidence, and switching still needs normal form.
            can_request = form == "main" or self._close_cast_available
            energy_ready = can_request and self.action.check_Skill_energy_bar()
            if self.combat.context.tasker.stopping:
                return
            if energy_ready:
                self._close_cast_available = False
                self.action.use_skill(_SKILL_DELAY_MS)
                self._close_cast_requested = True
            elif (
                form == "main" and not self._close_cast_requested
                and self._ult_ticks < _ULT_LOOP_MAX
            ):
                # A transient ready pulse can disappear before any closing
                # request. Resume the unspent farming budget on the next
                # identity-checked tick; do not strand remaining orbs here.
                self.phase = "ult_loop"
            else:
                self.action.attack()
