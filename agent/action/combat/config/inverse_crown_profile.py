"""Per-run InverseCrown configuration for the opt-in single-target strategy.

The 战斗辅助 task's opt-in options supply the called ``CombatRunner`` node's
``attach.inverse_crown_profile`` (currently 战斗中). Facts describe this battle,
never account defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


# 方案 3.5 候选模板（原作者攻略名单；冰队优先是复杂度选择，不是强度排序）。
# 效应证据（触发/维持/失效/恢复）须真机登记；本表只固化成员与核对前提。
TEAM_TEMPLATES: dict[str, dict[str, Any]] = {
    "ice": {
        "label": "优先冰队",
        "members": ("逆冕", "万事·明晰梦", "布偶熊·骇影"),
        "defaults": "逆冕队长、首发、留场，ready QTE",
        "verify": ("实际持有/次数", "意识", "增益触发", "非焚烧/空置效应是否实际生效"),
    },
    "burn": {
        "label": "焚烧候选",
        "members": ("逆冕", "七实·芒星之迹", "维罗妮卡·铮骨"),
        "defaults": "独立配置验证后台收益",
        "verify": ("跃升", "焚烧效应", "建场和续场条件", "有限收益失效后的表现"),
    },
}


class InverseCrownProfileError(ValueError):
    """A malformed profile must not silently enable ordinary combat."""


class InverseCrownUnavailable(RuntimeError):
    """The requested strategy or on-field identity cannot safely be used."""

    def __init__(
        self, reason: str, *, requested: str = "InverseCrown", actual: str = "unknown"
    ):
        self.reason = reason
        self.requested = requested
        self.actual = actual
        super().__init__(
            f"inverse_crown:{reason}; requested={requested}; actual={actual}"
        )


@dataclass(frozen=True, slots=True)
class InverseCrownProfile:
    """Validated snapshot; unknown facts do not grant higher-rank capabilities.

``enabled`` and ``scene`` select the single-target consumer. Confirmed battle
facts remain separate from live resource observations and action outcomes.
"""

    enabled: bool = False
    scene: str = "unknown"
    source: str = "unknown"
    rank: str = "unknown"
    signature_weapon_confirmed: bool = False
    team_template: str = "unknown"

    def __post_init__(self) -> None:
        for name in ("enabled", "signature_weapon_confirmed"):
            if type(getattr(self, name)) is not bool:
                raise InverseCrownProfileError(f"{name} must be a boolean")
        choices = {
            "scene": {"single", "multi", "unknown"},
            "source": {"self", "trial", "borrowed", "mode", "unknown"},
            "rank": {"S", "S5", "SS", "SS3", "SSS", "SSS3", "SSS6", "SSS+", "unknown"},
            "team_template": {"ice", "burn", "unknown"},
        }
        for name, allowed in choices.items():
            value = getattr(self, name)
            if type(value) is not str or value not in allowed:
                raise InverseCrownProfileError(f"{name} has an invalid value")

    @classmethod
    def from_node_data(cls, node_data: Any) -> InverseCrownProfile:
        """Copy facts only from a successfully read node object."""
        # SDK None means an unreadable/unknown node, not a valid node without
        # a profile. It must never silently select ordinary combat.
        if not isinstance(node_data, dict):
            raise InverseCrownProfileError("node data must be an object")
        attach = node_data.get("attach", {})
        if not isinstance(attach, dict):
            raise InverseCrownProfileError("attach must be an object")
        if "inverse_crown_profile" not in attach:
            return cls()
        raw = attach["inverse_crown_profile"]
        if not isinstance(raw, dict):
            raise InverseCrownProfileError("inverse_crown_profile must be an object")
        if set(raw) - {item.name for item in fields(cls)}:
            raise InverseCrownProfileError("inverse_crown_profile has unknown fields")
        return cls(**raw)

    @property
    def effective_rank(self) -> str:
        """Unidentified battle origin cannot inherit an account rank."""
        return self.rank if self.source != "unknown" else "unknown"

    @property
    def effective_signature_weapon(self) -> bool:
        return self.source != "unknown" and self.signature_weapon_confirmed
