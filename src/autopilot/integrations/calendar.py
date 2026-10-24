"""Calendar boundary: free/busy lookup and event creation.

Production adapters would wrap Google Calendar (freeBusy.query / events.insert) or
Microsoft Graph (getSchedule / events). The in-memory calendar backs tests and the demo.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class Calendar(Protocol):
    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]: ...

    def create_event(self, title: str, start: datetime, end: datetime, attendees: list[str],
                     description: str = "") -> str: ...

    def cancel_event(self, event_id: str) -> None: ...


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)
    description: str = ""


class InMemoryCalendar:
    def __init__(self) -> None:
        self.events: dict[str, CalendarEvent] = {}
        self._ids = itertools.count(1)

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        return [(e.start, e.end) for e in self.events.values() if e.start < end and e.end > start]

    def create_event(self, title: str, start: datetime, end: datetime, attendees: list[str],
                     description: str = "") -> str:
        event_id = f"evt_{next(self._ids)}"
        self.events[event_id] = CalendarEvent(event_id, title, start, end, list(attendees), description)
        return event_id

    def cancel_event(self, event_id: str) -> None:
        self.events.pop(event_id, None)

    def block(self, title: str, start: datetime, end: datetime) -> str:
        """Add an existing commitment (for tests/demo)."""
        return self.create_event(title, start, end, [])
