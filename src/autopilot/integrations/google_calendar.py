"""Reference Google Calendar adapter for the Calendar protocol.

Pure helpers (busy-interval parsing from a freeBusy response; event-insert body) are
tested. GoogleCalendar is a thin wrapper over the Calendar v3 client (guarded import),
needing OAuth and `google-api-python-client`, neither available here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from autopilot.integrations.calendar import Calendar  # noqa: F401 - documents the protocol implemented


def parse_freebusy(response: dict, calendar_id: str = "primary") -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for period in response.get("calendars", {}).get(calendar_id, {}).get("busy", []):
        start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
        out.append((start, end))
    return out


def event_body(title: str, start: datetime, end: datetime, attendees: list[str], description: str = "") -> dict:
    return {
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "attendees": [{"email": a} for a in attendees],
        "reminders": {"useDefault": True},
    }


class GoogleCalendar:  # pragma: no cover - needs OAuth + google-api-python-client
    def __init__(self, service: Any, calendar_id: str = "primary"):
        self.service = service  # build("calendar", "v3", credentials=...)
        self.calendar_id = calendar_id

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        resp = self.service.freebusy().query(body={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "items": [{"id": self.calendar_id}],
        }).execute()
        return parse_freebusy(resp, self.calendar_id)

    def create_event(self, title: str, start: datetime, end: datetime, attendees: list[str],
                     description: str = "") -> str:
        event = self.service.events().insert(
            calendarId=self.calendar_id, sendUpdates="all",
            body=event_body(title, start, end, attendees, description),
        ).execute()
        return event["id"]

    def cancel_event(self, event_id: str) -> None:
        self.service.events().delete(calendarId=self.calendar_id, eventId=event_id, sendUpdates="all").execute()
