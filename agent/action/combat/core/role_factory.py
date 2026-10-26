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

"""
MAA_Punish
MAA_Punish 战斗角色工厂
作者:overflow65537

约定：roles 包内类名与 LoadSetting.ROLE_ACTIONS.cls_name 一致。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

from action.combat.core.role import BaseRole
from action.combat.config.inverse_crown_profile import InverseCrownUnavailable

if TYPE_CHECKING:
    from action.combat.core.session import CombatTask

logger = logging.getLogger(__name__)

_DEFAULT_CLS_NAME = "GeneralFight"
ROLE_IMPORT_ERRORS: dict[str, str] = {}


def _discover_role_classes() -> dict[str, type[BaseRole]]:
    import action.combat.roles as roles_pkg

    mapping: dict[str, type[BaseRole]] = {}
    for modinfo in pkgutil.iter_modules(roles_pkg.__path__):
        if modinfo.name.startswith("_"):
            continue
        module_name = f"{roles_pkg.__name__}.{modinfo.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            # Preserve ordinary-mode fallback, but never report a failed import
            # as the requested implementation having been selected.
            ROLE_IMPORT_ERRORS[module_name] = type(exc).__name__
            logger.exception("角色策略导入失败: %s", module_name)
            continue
        for attr_name in dir(module):
            role_cls = getattr(module, attr_name)
            if not isinstance(role_cls, type):
                continue
            if not issubclass(role_cls, BaseRole) or role_cls is BaseRole:
                continue
            cls_name = role_cls.__name__
            if cls_name in mapping:
                logger.warning(
                    "重复注册 cls_name=%s: %s 与 %s",
                    cls_name,
                    mapping[cls_name].__name__,
                    role_cls.__name__,
                )
            mapping[cls_name] = role_cls
    return mapping


ROLE_CLASS_MAP: dict[str, type[BaseRole]] = _discover_role_classes()


def require_inverse_crown_identity(role: BaseRole) -> None:
    """Check the actual class object, independently of roster/cls_name aliases."""
    try:
        module = importlib.import_module("action.combat.roles.inverse_crown")
        expected = module.InverseCrown
    except Exception as exc:
        raise InverseCrownUnavailable(
            "strategy_import_failed", requested=role.requested_cls_name,
            actual=role.implementation_name,
        ) from exc
    if role.requested_cls_name != "InverseCrown" or type(role) is not expected:
        raise InverseCrownUnavailable(
            "strategy_identity_mismatch", requested=role.requested_cls_name,
            actual=role.implementation_name,
        )


def create_role(combat: CombatTask, color: str, cls_name: str) -> BaseRole:
    role_cls = ROLE_CLASS_MAP.get(cls_name)
    fallback_reason = None
    if role_cls is None:
        fallback_reason = "requested_strategy_unavailable"
        if cls_name == "InverseCrown":
            import_error = ROLE_IMPORT_ERRORS.get("action.combat.roles.inverse_crown")
            if import_error:
                fallback_reason = f"strategy_import_failed:{import_error}"
        role_cls = ROLE_CLASS_MAP.get(_DEFAULT_CLS_NAME)
    if role_cls is None:
        raise RuntimeError(f"缺少默认角色策略: {_DEFAULT_CLS_NAME}")
    role = role_cls(combat, color, cls_name)
    if fallback_reason is None and type(role).__name__ != cls_name:
        fallback_reason = "requested_strategy_mismatch"
    role.fallback_reason = fallback_reason
    if fallback_reason:
        logger.warning(
            "角色策略回退: requested=%s actual=%s reason=%s",
            cls_name, role.implementation_name, fallback_reason,
        )
    # Strict validation can raise before callers assign the role into roles.
    # Keep that actual implementation and every subsequent rebind in the result.
    combat.record_role_binding(role)
    if combat.inverse_crown_profile.enabled and cls_name == "InverseCrown":
        require_inverse_crown_identity(role)
    return role
