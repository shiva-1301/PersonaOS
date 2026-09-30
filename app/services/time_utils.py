"""Timezone helpers. Everything is stored in UTC; "today" and "this week" are computed
in the user's own timezone (users.timezone, an IANA name such as "Asia/Kolkata")."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def user_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def to_utc(value: datetime, tz: ZoneInfo) -> datetime:
    """Naive datetimes are wall-clock times in the user's timezone."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(UTC)


def local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def today_window(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[local midnight today, local midnight tomorrow) as UTC."""
    today = now.astimezone(tz).date()
    return (
        local_midnight(today, tz).astimezone(UTC),
        local_midnight(today + timedelta(days=1), tz).astimezone(UTC),
    )


def week_window(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The current calendar week, Monday 00:00 to next Monday 00:00 local, as UTC."""
    today = now.astimezone(tz).date()
    monday = today - timedelta(days=today.weekday())
    return (
        local_midnight(monday, tz).astimezone(UTC),
        local_midnight(monday + timedelta(days=7), tz).astimezone(UTC),
    )


def week_start(moment: datetime, tz: ZoneInfo) -> date:
    """Monday (local) of the week containing `moment`."""
    day = moment.astimezone(tz).date()
    return day - timedelta(days=day.weekday())
