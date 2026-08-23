from dataclasses import dataclass
from enum import Enum

from skills.base import Permission


class GateVerdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


@dataclass(frozen=True)
class GateDecision:
    verdict: GateVerdict
    reason: str


class GatePolicy:
    def evaluate(self, permission: Permission) -> GateDecision:
        if permission is Permission.READ:
            return GateDecision(GateVerdict.ALLOW, "read skill allowed")
        if permission is Permission.WRITE:
            return GateDecision(GateVerdict.DENY, "write skills are not enabled")
        if permission is Permission.DANGEROUS:
            return GateDecision(
                GateVerdict.DENY,
                "dangerous skills require confirmation flow",
            )
        return GateDecision(GateVerdict.DENY, "unsupported skill permission")
