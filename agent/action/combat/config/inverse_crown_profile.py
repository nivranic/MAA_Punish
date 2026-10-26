"""Per-run InverseCrown configuration; the specialized strategy is not ready yet.

The 战斗辅助 task's opt-in options supply the called ``CombatRunner`` node's
``attach.inverse_crown_profile`` (currently 战斗中). Facts describe this battle,
never account defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


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

Only ``enabled`` currently changes execution. Scene and confirmed battle facts
are carried to the role for the forthcoming strategy, not proof of its readiness.
"""

    enabled: bool = False
    scene: str = "unknown"
    source: str = "unknown"
    rank: str = "unknown"
    signature_weapon_confirmed: bool = False

    def __post_init__(self) -> None:
        for name in ("enabled", "signature_weapon_confirmed"):
            if type(getattr(self, name)) is not bool:
                raise InverseCrownProfileError(f"{name} must be a boolean")
        choices = {
            "scene": {"single", "multi", "unknown"},
            "source": {"self", "trial", "borrowed", "mode", "unknown"},
            "rank": {"S", "S5", "SS", "SS3", "SSS", "SSS3", "SSS6", "SSS+", "unknown"},
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
