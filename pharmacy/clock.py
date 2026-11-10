"""Injectable clocks so time-dependent rules are deterministic in tests."""

from datetime import date, datetime, timedelta, timezone


class Clock:
    def now(self) -> datetime:
        raise NotImplementedError

    def today(self) -> date:
        return self.now().date()


class SystemClock(Clock):
    """Wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)


class FixedClock(Clock):
    """A clock that only moves when told to. Intended for tests."""

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        self._now = value

    def advance(self, **kwargs) -> datetime:
        """Move forward, e.g. ``clock.advance(days=3, hours=2)``."""
        self._now = self._now + timedelta(**kwargs)
        return self._now
