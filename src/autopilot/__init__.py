"""Clean-room AI recruiting reply agent.

Built from publicly documented behaviour of commercial "autopilot" recruiting
assistants; contains no third-party code. See docs/TEARDOWN.md.
"""

from autopilot.engine import Autopilot
from autopilot.settings import (
    AnswerMode,
    AutomationSettings,
    EscalationChannel,
    ReplySpeed,
    ScheduleMode,
    SchedulingSettings,
)

__all__ = [
    "Autopilot",
    "AnswerMode",
    "AutomationSettings",
    "EscalationChannel",
    "ReplySpeed",
    "ScheduleMode",
    "SchedulingSettings",
]
