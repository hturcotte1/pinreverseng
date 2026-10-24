from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from autopilot.integrations.calendar import InMemoryCalendar
from autopilot.models import OfferedSlot
from autopilot.scheduling import format_slot, format_slot_dual, propose_slots, slot_is_free, us_federal_holidays
from autopilot.settings import SchedulingSettings

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)  # Mon 9am ET


def test_offers_three_slots_on_different_days_within_hours():
    slots = propose_slots(SchedulingSettings(), InMemoryCalendar(), NOW, "America/New_York")
    assert len(slots) == 3
    days = {s.start.astimezone(ET).date() for s in slots}
    assert len(days) == 3
    for s in slots:
        local = s.start.astimezone(ET)
        assert local.weekday() < 5 and 9 <= local.hour < 17
        assert s.start >= NOW + timedelta(hours=18)  # minimum notice


def test_respects_busy_time_with_buffer_and_skip_dates():
    cal = InMemoryCalendar()
    tue = date(2026, 10, 27)
    cal.block("all day", datetime.combine(tue, time(9), ET), datetime.combine(tue, time(17), ET))
    settings = SchedulingSettings(skip_dates={date(2026, 10, 28)})
    slots = propose_slots(settings, cal, NOW, "America/New_York")
    assert all(s.start.astimezone(ET).date() not in (tue, date(2026, 10, 28)) for s in slots)


def test_slot_is_free_honours_buffer():
    cal = InMemoryCalendar()
    start = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
    cal.block("meeting", start - timedelta(minutes=40), start - timedelta(minutes=10))
    settings = SchedulingSettings(buffer_minutes=15)
    assert not slot_is_free(settings, cal, OfferedSlot(start, start + timedelta(minutes=30)))
    assert slot_is_free(SchedulingSettings(buffer_minutes=5), cal, OfferedSlot(start, start + timedelta(minutes=30)))


def test_candidate_windows_constrain_slots():
    window = (datetime(2026, 10, 29, 14, 0, tzinfo=ET), datetime(2026, 10, 29, 16, 0, tzinfo=ET))
    slots = propose_slots(SchedulingSettings(), InMemoryCalendar(), NOW, "America/New_York", [window])
    assert slots and all(window[0] <= s.start and s.end <= window[1] for s in slots)


def test_prefers_candidate_business_hours():
    slots = propose_slots(SchedulingSettings(), InMemoryCalendar(), NOW, "America/New_York",
                          candidate_tz="America/Los_Angeles")
    pt = ZoneInfo("America/Los_Angeles")
    assert all(9 <= s.start.astimezone(pt).hour < 17 for s in slots)


def test_labels():
    start = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
    assert format_slot(start, "America/New_York") == "Wed Oct 28, 2:00 PM EDT"
    assert format_slot_dual(start, "America/Los_Angeles", "America/New_York") == "Wed Oct 28, 11:00 AM PDT (2:00 PM EDT)"
    assert format_slot_dual(start, "America/New_York", "America/New_York") == "Wed Oct 28, 2:00 PM EDT"


def test_us_holidays_2026():
    h = us_federal_holidays(2026)
    assert date(2026, 11, 26) in h  # Thanksgiving
    assert date(2026, 7, 3) in h  # July 4th is a Saturday -> observed Friday
    assert date(2026, 5, 25) in h  # Memorial Day
