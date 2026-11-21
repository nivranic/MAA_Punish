"""矩阵循生：用同帧头像、试玩标记及招募预算确认一次编入。"""
from __future__ import annotations

import re

from maa.define import AndRecognitionResult, ColorMatchResult, OCRResult, OrRecognitionResult


_LIST_BUDGET_OCR_NODES = (
    "矩阵循生_招募能量_列表_整区", "矩阵循生_招募能量_列表_定位",
)


class R03Recruitment:
    # 最近一次实测预算（编队页或列表页 OCR 命中时刷新）。列表页能量文本被
    # 右侧角色预览机甲遮挡（seg120/121：随选中角色漂移）时，列表侧读数回落
    # 到它；编队页读数（team=True）永不回退——那是编入扣款算术的基准。
    _budget_cache = None

    def __init__(self, selector):
        self.selector = selector

    @staticmethod
    def red_equipment_patch():
        return {"确认装备_矩阵循生": {
            "action": {"type": "DoNothing"},
            "next": ["矩阵循生_关闭套装激活提示",
                     "矩阵循生_红位装备确认", "矩阵循生_装备选择红位",
                     "矩阵循生_余位装备确认", "矩阵循生_装备选择余位",
                     "矩阵循生_非装备确认"],
        }}

    @staticmethod
    def read_budget(context, image, *, team=False):
        if image is None:
            return None
        entry = "矩阵循生_招募能量_编队" if team else "矩阵循生_招募能量_列表"
        result = context.run_recognition(entry, image)
        value = R03Recruitment._budget_value(result)
        if value is not None:
            R03Recruitment._budget_cache = value
            return value
        if team:
            # 编队页预算是编入扣款算术的基准，读不到必须硬失败，不回退缓存。
            return None
        # seg121：列表页能量文本被预览角色遮挡时（页面身份已由 招募列表就绪
        # 两锚钉死），回落最近实测值；真实扣款仍由编入后的编队页预算算术闸门兜底。
        return R03Recruitment._budget_cache

    @staticmethod
    def _budget_value(result):
        best = result.best_result if result and result.hit else None
        if isinstance(best, OrRecognitionResult):
            if result.name != "矩阵循生_招募能量_列表":
                return None
            hits = [part for part in best.sub_results if part.hit]
            if len(hits) != 1 or hits[0].name not in _LIST_BUDGET_OCR_NODES:
                return None
            best = hits[0].best_result
        if not isinstance(best, OCRResult):
            return None
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", best.text.strip())
        if not match:
            return None
        current, maximum = map(int, match.groups())
        return (current, maximum) if 0 <= current <= maximum <= 99 and maximum else None

    @staticmethod
    def _list_budget_overrides(current, maximum):
        # Both OCR paths must wait for the same exact, pre-click budget.
        return {name: {"recognition": {"param": {"expected": f"^{current}/{maximum}$"}}}
                for name in _LIST_BUDGET_OCR_NODES}

    @classmethod
    def _completed_list_budget(cls, context, detail):
        """只读取本次开槽子任务已完成的列表同帧预算，不另取动画帧。"""
        for node_id in reversed(getattr(detail, "node_id_list", None) or []):
            node = context.tasker.get_node_detail(node_id)
            if node is None:
                return None
            if node.name != "矩阵循生_招募列表就绪":
                continue
            recognition = node.recognition
            best = recognition.best_result if recognition and recognition.hit else None
            if not node.completed or not isinstance(best, AndRecognitionResult):
                return None
            budgets = [result for result in best.sub_results
                       if result.name == "矩阵循生_招募能量_列表"]
            return cls._budget_value(budgets[0]) if len(budgets) == 1 else None
        return None

    @classmethod
    def read_formation(cls, context, image):
        """只读同帧预算、固定三色槽及红位首发，不从未知页面推断空位。"""
        if image is None:
            return None
        page = context.run_recognition("矩阵循生_编队色位", image)
        budget = cls.read_budget(context, image, team=True)
        if not page or not page.hit or budget is None:
            return None
        empty = {}
        for color, label in (("B", "蓝"), ("R", "红"), ("Y", "黄")):
            result = context.run_recognition(f"矩阵循生_{label}位空槽", image)
            if result is None:
                return None
            empty[color] = bool(result.hit)
        first = context.run_recognition("矩阵循生_红位首发", image)
        if first is None:
            return None
        return {"budget": budget, "empty": empty, "red_first": bool(first.hit)}

    def open_slot(self, context, color):
        """仅打开已确认的空槽；入列表后的预算必须与编队页相同。"""
        label = {"B": "蓝", "R": "红", "Y": "黄"}.get(color)
        if not label or context.tasker.stopping:
            return False
        image = context.tasker.controller.post_screencap().wait().get()
        formation = self.read_formation(context, image)
        if formation is None or not formation["empty"][color] or context.tasker.stopping:
            return False
        current, maximum = formation["budget"]
        detail = context.run_task(f"矩阵循生_打开{label}位",
                                  self._list_budget_overrides(current, maximum))
        if not self.completed(context, detail, "矩阵循生_招募列表就绪") or context.tasker.stopping:
            return False
        list_budget = self._completed_list_budget(context, detail)
        # seg121：两锚变体的识别详情不含预算文本（None=不可读），页面身份已由
        # 招募列表就绪 两锚钉死，此时跳过前后预算一致性复核。
        return list_budget is None or list_budget == formation["budget"]

    def return_to_formation(self, context):
        """只从已确认的招募列表返回；预算可读时校验前后一致，不可读时以锚为准。"""
        if context.tasker.stopping:
            return None
        image = context.tasker.controller.post_screencap().wait().get()
        if image is None:
            return None
        page = context.run_recognition("矩阵循生_招募列表就绪", image)
        budget = self.read_budget(context, image)
        if not page or not page.hit or budget is None:
            # An unreadable animation snapshot must not authorize navigation.
            # Reuse the list's bounded, input-free readiness task and consume
            # only its own completed recognition, as opening a slot already does.
            detail = context.run_task("矩阵循生_招募列表就绪", {
                "矩阵循生_招募列表就绪": {"timeout": 3000, "on_error": []},
            })
            if not self.completed(context, detail, "矩阵循生_招募列表就绪") or context.tasker.stopping:
                return None
            budget = self._completed_list_budget(context, detail)
            # 不回退 _budget_cache：缓存可能是上一局遗留值，不能作为本页一致性基准。
        detail = context.run_task(
            "矩阵循生_返回编队",
            self._list_budget_overrides(*budget) if budget is not None else {},
        )
        if not self.completed(context, detail, "矩阵循生_编队已返回") or context.tasker.stopping:
            return None
        image = context.tasker.controller.post_screencap().wait().get()
        formation = self.read_formation(context, image)
        if budget is None:
            # seg121：能量文本被预览角色遮挡时列表预算不可读——页面身份已由
            # 招募列表就绪 两锚钉死，导航后以编队页实测预算为准（并预热缓存）。
            return formation
        return formation if formation and formation["budget"] == budget else None

    @staticmethod
    def red_ready(context, image):
        if image is None:
            return False
        result = context.run_recognition("矩阵循生_红位队长首发", image)
        return bool(result and result.hit)

    def ensure_red_leader(self, context):
        """勾选前确认未勾选，保存后重新确认红位队长和首发，避免重复点击取消。"""
        if context.tasker.stopping:
            return False
        image = context.tasker.controller.post_screencap().wait().get()
        formation = self.read_formation(context, image)
        if formation is None or formation["empty"]["R"]:
            return False
        if self.red_ready(context, image):
            return True
        detail = context.run_task("矩阵循生_打开队伍调整")
        if not self.completed(context, detail, "矩阵循生_调整页就绪"):
            return False
        for label in ("队长", "首发"):
            if context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None:
                return False
            checked = context.run_recognition(f"矩阵循生_红位{label}已勾选", image)
            if checked is None:
                return False
            if checked.hit:
                continue
            detail = context.run_task(f"矩阵循生_设置红位{label}")
            if not self.completed(context, detail, f"矩阵循生_红位{label}已勾选"):
                return False
        if context.tasker.stopping:
            return False
        detail = context.run_task("矩阵循生_保存队伍调整")
        if not self.completed(context, detail, "矩阵循生_编队已返回") or context.tasker.stopping:
            return False
        image = context.tasker.controller.post_screencap().wait().get()
        after = self.read_formation(context, image)
        return after == {**formation, "red_first": True} and self.red_ready(context, image)

    def recruit_team(self, context, role_dict, attacker, supports, max_try):
        """空队起步，主力放红位；只返回本次实际扣款并确认槽位的成员。"""
        formation = self.return_to_formation(context)
        if formation is None or not all(formation["empty"].values()):
            return None
        if not self.open_slot(context, "R") or not self.recruit(context, role_dict, attacker, max_try):
            return None
        image = context.tasker.controller.post_screencap().wait().get()
        after = self.read_formation(context, image)
        if after is None or after["empty"] != {**formation["empty"], "R": False}:
            return None
        if not self.ensure_red_leader(context):
            return None
        confirmed = {"R": attacker, "B": "", "Y": ""}
        for color in ("B", "Y"):
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            formation = self.read_formation(context, image)
            if formation is None or not self.red_ready(context, image):
                return None
            if not formation["empty"][color]:
                return None
            if formation["budget"][0] == 0:
                break
            used = {name.replace("[试用]", "") for name in confirmed.values() if name}
            candidates = list(dict.fromkeys(
                name.replace("[试用]", "") for name in supports
                if name.replace("[试用]", "") not in used
            ))
            if not candidates:
                break
            if not self.open_slot(context, color):
                return None
            fielded = False
            for name in candidates:
                if context.tasker.stopping:
                    return None
                recruited = self.recruit_support(context, role_dict, name, max_try, formation["budget"])
                if recruited:
                    image = context.tasker.controller.post_screencap().wait().get()
                    after = self.read_formation(context, image)
                    if after is None or after["empty"] != {**formation["empty"], color: False}:
                        return None
                    confirmed[color] = recruited
                    fielded = True
                    break
                # 仅能证明仍在原预算的列表时，才允许尝试下一位；未知扣款不可重试。
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    return None
                page = context.run_recognition("矩阵循生_招募列表就绪", image)
                if not page or not page.hit:
                    return None
                listed = self.read_budget(context, image)
                if listed is None:
                    # seg121：预算被遮挡无法证明同预算——不尝试下一位（未知扣款
                    # 不可重试），退回编队按已确认槽位收尾（R 单色位编队合法）。
                    break
                if listed != formation["budget"]:
                    return None
            if not fielded:
                returned = self.return_to_formation(context)
                if returned != formation:
                    return None
                break
        if context.tasker.stopping:
            return None
        image = context.tasker.controller.post_screencap().wait().get()
        final = self.read_formation(context, image)
        if final is None or not self.red_ready(context, image):
            return None
        if any(final["empty"][color] == bool(name) for color, name in confirmed.items()):
            return None
        return confirmed

    @staticmethod
    def read_power(context, image, box):
        """读取同帧头像旁的完整战力，不将标签、标点或残缺数字当作数值。"""
        if image is None or context.tasker.stopping:
            return None
        result = context.run_recognition("矩阵循生_角色战力", image, {
            "矩阵循生_角色战力": {"recognition": {"param": {
                "roi": [307, box[1] + 57, 76, 30],
            }}},
        })
        best = result.best_result if result and result.hit else None
        if context.tasker.stopping or not isinstance(best, OCRResult):
            return None
        text = best.text.strip()
        return int(text) if re.fullmatch(r"[1-9]\d{0,5}", text) else None

    def _support_list_frame(self, context, budget):
        if context.tasker.stopping:
            return None
        image = context.tasker.controller.post_screencap().wait().get()
        if image is None:
            return None
        page = context.run_recognition("矩阵循生_招募列表就绪", image)
        if not page or not page.hit or self.read_budget(context, image) != budget or context.tasker.stopping:
            return None
        return image

    def recruit_support(self, context, role_dict, role_name, max_try, budget):
        """有限翻页比较可负担的正式/试玩版本，只返回实际扣费确认的版本。"""
        base_name = role_name.replace("[试用]", "")
        action = role_dict.get(base_name)
        if action is None or max_try < 1 or self._support_list_frame(context, budget) is None:
            return None
        if not self._open_element(context, role_dict, base_name):
            return None
        # 元素列表可能记住上次的滚动位置；沿用同一有界翻页预算回顶。
        for _ in range(max_try - 1):
            if self._support_list_frame(context, budget) is None:
                return None
            if not context.run_action("反向滑动_选人"):
                return None
        versions = (base_name, base_name + "[试用]")
        powers = {}
        advances = 0
        for index in range(max_try):
            image = self._support_list_frame(context, budget)
            if image is None:
                return None
            for name in versions:
                for box, row_budget, _cost in self.candidates(context, image, name, action):
                    if row_budget != budget:
                        return None
                    power = self.read_power(context, image, box)
                    if power is not None:
                        if name in powers and powers[name] != power:
                            return None
                        powers[name] = power
            if context.tasker.stopping:
                return None
            if len(powers) == 2 or index + 1 == max_try:
                break
            if not context.run_action("滑动_选人"):
                return None
            advances += 1
        if not powers:
            return None
        selected = max((name for name in versions if name in powers), key=powers.get)
        for _ in range(advances):
            if self._support_list_frame(context, budget) is None:
                return None
            if not context.run_action("反向滑动_选人"):
                return None
        if self._support_list_frame(context, budget) is None:
            return None
        self.selector.logger.info("肉鸽3辅助版本战力: %s，选择 %s", powers, selected)
        return selected if self._recruit_from_list(context, role_dict, selected, max_try) else None

    @staticmethod
    def completed(context, detail, name):
        for node_id in getattr(detail, "node_id_list", None) or []:
            node = context.tasker.get_node_detail(node_id)
            if node and node.name == name and node.completed:
                return True
        return False

    @staticmethod
    def selection_patch(box):
        center_y = box[1] + box[3] // 2
        return {
            "矩阵循生_点选角色": {"action": {"param": {"target": list(box)}}},
            "矩阵循生_选中左角": {"recognition": {"param": {"roi": [128, center_y - 53, 13, 13]}}},
            "矩阵循生_选中右角": {"recognition": {"param": {"roi": [357, center_y - 53, 13, 13]}}},
        }

    def candidates(self, context, image, role_name, action):
        if image is None:
            return []
        base_name = role_name.replace("[试用]", "")
        want_trial = role_name != base_name
        result = context.run_recognition("识别角色", image, {
            "识别角色": {"recognition": {"param": {"template": action["template"]}}},
        })
        if not result or not result.hit:
            return []
        budget = self.read_budget(context, image)
        if budget is None:
            return []
        candidates = []
        for match in self.selector._template_match_hits(result):
            x, y, width, height = match.box
            # 顶/底裁切会丢失试玩标记或消耗图标，不能当作正式角色。
            if y < 78 or y + height > 630:
                continue
            trial = context.run_recognition("识别试用角色", image, {
                "识别试用角色": {"recognition": {"param": {"roi": list(match.box)}}},
            })
            if trial is None or bool(trial.hit) != want_trial:
                continue
            cost_result = context.run_recognition("矩阵循生_角色招募消耗", image, {
                "矩阵循生_角色招募消耗": {"recognition": {"param": {"roi": [240, y + 4, 155, 32]}}},
            })
            hits = cost_result.filtered_results if cost_result and cost_result.hit else []
            # 选中态行右侧会出现深色装饰，在消耗区引入 1x1 碎片与 22x32 色块分量
            # （2026-11-16 run_20261116_055519_2 实测）；先按真实图标尺寸过滤再校验，
            # 否则碎片会让下方尺寸校验跳过整行，选中校验拿不到 current 而误杀。
            # 被遮挡而缺失的末位图标让 cost 偏小：可负担性以点击前的读取为准，
            # 点击后仅要求同行+预算未扣，实际扣款由编入后的预算算术把关。
            icons = sorted(
                (item.box for item in hits
                 if isinstance(item, ColorMatchResult)
                 and 16 <= item.box[2] <= 26 and 16 <= item.box[3] <= 26),
                key=lambda box: box[0],
            )
            if not 1 <= len(icons) <= 5:
                continue
            if any(not (16 <= box[2] <= 26 and 16 <= box[3] <= 26) for box in icons):
                continue
            if any(abs(box[1] - icons[0][1]) > 2 for box in icons):
                continue
            if any(not 26 <= right[0] - left[0] <= 33 for left, right in zip(icons, icons[1:])):
                continue
            if len(icons) <= budget[0]:
                candidates.append((match.box, budget, len(icons)))
        return candidates

    def recruit(self, context, role_dict, role_name, max_try):
        """仅在选中身份与预算扣除均确认后返回 True；不修改角色缓存。"""
        base_name = role_name.replace("[试用]", "")
        if not role_dict.get(base_name) or not self._open_element(context, role_dict, base_name):
            return False
        return self._recruit_from_list(context, role_dict, role_name, max_try)

    def _open_element(self, context, role_dict, base_name):
        element = self.selector._get_role_element_task(role_dict, base_name)
        if context.tasker.stopping:
            return False
        if not element:
            return True
        confirmation = element.replace("打开", "确认进入", 1)
        # Nested StopTask would stop the parent before it can report failure.
        # Return this bounded step's failure to the owning RoleSelection action.
        # 2026-11-16 run_20261116_062130_2：memo 命中快速路径下，列表页出现后 ~1s
        # 就点击元素页签，转场动画吃掉点击，确认轮询全程落空；加一次有界重试，
        # 重试先独立确认（页面可能已打开，避免二次点击把筛选切掉）再重走打开链。
        for attempt in range(2):
            if context.tasker.stopping:
                return False
            if attempt:
                detail = context.run_task(confirmation, {
                    confirmation: {"on_error": [], "timeout": 3000},
                })
                if self.completed(context, detail, confirmation):
                    return not context.tasker.stopping
            detail = context.run_task(element, {
                element: {"on_error": []}, confirmation: {"on_error": []},
            })
            if self.completed(context, detail, confirmation) and not context.tasker.stopping:
                return True
        return False

    def _recruit_from_list(self, context, role_dict, role_name, max_try):
        """列表已就绪后共用同一选中/扣费链，不重复点击已选中的元素筛选。"""
        action = role_dict.get(role_name.replace("[试用]", ""))
        if not action:
            return False
        for _ in range(max_try):
            if context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None:
                return False
            candidates = self.candidates(context, image, role_name, action)
            if context.tasker.stopping:
                return False
            if not candidates:
                context.run_action("滑动_选人")
                continue
            box, budget, cost = candidates[0]
            patch = self.selection_patch(box)
            detail = context.run_task("矩阵循生_点选角色", patch)
            if not self.completed(context, detail, "矩阵循生_选中角色") or context.tasker.stopping:
                return False
            # 点击失败/未切换时，既不能借用上一位的选中态，也不能借用旧预算。
            # 只校验同行 + 预算未扣；消耗数不参与等式：2026-11-16 run_20261116_050352_2
            # 实测选中行右侧出现确认按钮深色竖条（22x32、n=426）遮住第 4 个消耗图标，
            # ColorMatch 只见 3 个干净图标，cost 4→3，严格等式误杀已选中的正常状态。
            # 真正的扣款闸门在编入后的预算算术（after == budget-cost）。
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None:
                return False
            current = self.candidates(context, image, role_name, action)
            selected = context.run_recognition("矩阵循生_选中角色", image, patch)
            if not selected or not selected.hit or not any(
                tuple(row[0]) == tuple(box) and row[1] == budget for row in current
            ):
                return False
            if context.tasker.stopping:
                return False
            detail = context.run_task("矩阵循生_编入角色")
            if not self.completed(context, detail, "矩阵循生_编入后确认") or context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            after = self.read_budget(context, image, team=True)
            if after != (budget[0] - cost, budget[1]):
                self.selector.logger.warning("肉鸽3编入预算未确认: %s, before=%s, cost=%s, after=%s", role_name, budget, cost, after)
                return False
            self.selector.logger.info("肉鸽3编入已确认: %s, before=%s, cost=%s, after=%s", role_name, budget, cost, after)
            return True
        self.selector.logger.warning("肉鸽3未找到版本及预算均匹配的角色: %s", role_name)
        return False
