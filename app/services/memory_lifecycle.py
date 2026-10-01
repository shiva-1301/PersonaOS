"""Memory lifecycle rules (Ebbinghaus-style forgetting curve). Pure functions: the caller
passes `now`, so tests can use a fake clock.

    S        = base_days * (1 + 0.5 * access_count)     # each recall slows forgetting
    strength = exp(-days_since_last_access / S)
    state    : strength < archive_below -> archived  (kept, but never recalled)
               strength < stale_below   -> stale     (recalled, ranked lower)
               otherwise                -> active

`superseded` is final (a newer fact replaced it). `archived` is not revived by decay; a
stale memory that is recalled and reinforced back above stale_below becomes active again.
"""

import math
from dataclasses import dataclass
from datetime import datetime

HIDDEN_STATES = ("archived", "superseded")


@dataclass(frozen=True)
class LifecycleRules:
    base_days: float = 14.0
    stale_below: float = 0.5
    archive_below: float = 0.15
    reinforce_step: float = 0.1


def decayed_strength(
    last_accessed_at: datetime, access_count: int, now: datetime, base_days: float
) -> float:
    days = max(0.0, (now - last_accessed_at).total_seconds() / 86400)
    stability = base_days * (1 + 0.5 * max(0, access_count))
    return math.exp(-days / stability)


def next_state(current: str, strength: float, rules: LifecycleRules) -> str:
    if current in HIDDEN_STATES:
        return current
    if strength < rules.archive_below:
        return "archived"
    if strength < rules.stale_below:
        return "stale"
    return "active"


def reinforced(strength: float, rules: LifecycleRules) -> float:
    return min(1.0, strength + rules.reinforce_step)
