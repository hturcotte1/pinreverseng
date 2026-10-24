"""Interview slot finding.

Generates candidate-facing options from the recruiter's availability window, minus
existing calendar commitments (with buffer), skip dates and minimum notice. Offers
are spread across different days and alternate morning/afternoon, the way a person
would write "I can do Tue 10am, Wed 2pm or Thu 11am".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from autopilot.integrations.calendar import Calendar
from autopilot.models import OfferedSlot
from autopilot.settings import SchedulingSettings

_PREFERRED_HOURS = (10, 14, 11, 15, 9, 16)


def format_slot(start: datetime, tz: str) -> str:
    local = start.astimezone(ZoneInfo(tz))
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local:%a %b} {local.day}, {hour}:{local.minute:02d} {ampm} {local.tzname()}"


def format_slot_dual(start: datetime, candidate_tz: str, recruiter_tz: str) -> str:
    label = format_slot(start, candidate_tz)
    other = start.astimezone(ZoneInfo(recruiter_tz))
    if start.astimezone(ZoneInfo(candidate_tz)).utcoffset() != other.utcoffset():
        hour = other.hour % 12 or 12
        label += f" ({hour}:{other.minute:02d} {'AM' if other.hour < 12 else 'PM'} {other.tzname()})"
    return label


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def free_slots(
    settings: SchedulingSettings,
    calendar: Calendar,
    now: datetime,
    recruiter_tz: str,
    candidate_windows: list[tuple[datetime, datetime]] | None = None,
) -> dict[date, list[OfferedSlot]]:
    """All bookable slots, grouped by local date."""
    tz = ZoneInfo(recruiter_tz)
    earliest = now + timedelta(hours=settings.min_notice_hours)
    horizon_end = now + timedelta(days=settings.horizon_days)
    duration = timedelta(minutes=settings.duration_minutes)
    buffer = timedelta(minutes=settings.buffer_minutes)
    step = timedelta(minutes=settings.slot_step_minutes)
    busy = calendar.busy(now, horizon_end + timedelta(days=1))

    by_day: dict[date, list[OfferedSlot]] = {}
    day = earliest.astimezone(tz).date()
    while datetime.combine(day, datetime.min.time(), tz) <= horizon_end:
        windows = settings.availability.get(day.weekday(), [])
        if day not in settings.skip_dates:
            for win_start, win_end in windows:
                start = datetime.combine(day, win_start, tz)
                window_end = datetime.combine(day, win_end, tz)
                while start + duration <= window_end:
                    end = start + duration
                    if start >= earliest and end <= horizon_end and not any(
                        _overlaps(start - buffer, end + buffer, b_start, b_end) for b_start, b_end in busy
                    ) and (
                        not candidate_windows
                        or any(c_start <= start and end <= c_end for c_start, c_end in candidate_windows)
                    ):
                        by_day.setdefault(day, []).append(OfferedSlot(start, end))
                    start += step
        day += timedelta(days=1)
    return by_day


def propose_slots(
    settings: SchedulingSettings,
    calendar: Calendar,
    now: datetime,
    recruiter_tz: str,
    candidate_windows: list[tuple[datetime, datetime]] | None = None,
    count: int | None = None,
    candidate_tz: str | None = None,
) -> list[OfferedSlot]:
    """Pick `count` slots on different days; prefer the candidate's local 9-5 when we know their zone."""
    count = count or settings.slots_to_offer
    by_day = free_slots(settings, calendar, now, recruiter_tz, candidate_windows)
    tz = ZoneInfo(candidate_tz or recruiter_tz)
    if candidate_tz and not candidate_windows:
        friendly = {d: [s for s in slots if 9 <= s.start.astimezone(tz).hour < 17] for d, slots in by_day.items()}
        friendly = {d: slots for d, slots in friendly.items() if slots}
        if friendly:
            by_day = friendly
    chosen: list[OfferedSlot] = []
    for i, day in enumerate(sorted(by_day)):
        if len(chosen) >= count:
            break
        target = _PREFERRED_HOURS[i % len(_PREFERRED_HOURS)]
        best = min(by_day[day], key=lambda s: abs(s.start.astimezone(tz).hour - target))
        chosen.append(best)
    if len(chosen) < count:  # few free days: fill from remaining slots on the same days
        remaining = [s for day in sorted(by_day) for s in by_day[day] if s not in chosen]
        for slot in remaining:
            if len(chosen) >= count:
                break
            if all(abs(slot.start - c.start) >= timedelta(hours=2) for c in chosen):
                chosen.append(slot)
    return sorted(chosen, key=lambda s: s.start)


def slot_is_free(settings: SchedulingSettings, calendar: Calendar, slot: OfferedSlot) -> bool:
    buffer = timedelta(minutes=settings.buffer_minutes)
    return not any(
        _overlaps(slot.start - buffer, slot.end + buffer, b_start, b_end)
        for b_start, b_end in calendar.busy(slot.start - buffer, slot.end + buffer)
    )


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_federal_holidays(year: int) -> set[date]:
    return {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # MLK Day
        _nth_weekday(year, 2, 0, 3),  # Presidents' Day
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus / Indigenous Peoples' Day
        _observed(date(year, 11, 11)),
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
