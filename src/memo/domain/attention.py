"""When MEMO may speak on its own: quiet hours and an hourly cap; danger always gets through."""

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class AttentionPolicy:
    timezone: str = "Asia/Taipei"
    quiet_start: time | None = None
    quiet_end: time | None = None
    max_per_hour: int = 6

    @classmethod
    def from_settings(cls, quiet_hours: str, max_per_hour: int, timezone: str):
        """quiet_hours is "HH:MM-HH:MM" in local time (may cross midnight) or empty."""
        start = end = None
        if quiet_hours.strip():
            start, end = (
                time(*(int(part) for part in clock.strip().split(":")))
                for clock in quiet_hours.split("-", 1)
            )
        return cls(timezone, start, end, max_per_hour)

    def local(self, at: datetime) -> datetime:
        return at.astimezone(ZoneInfo(self.timezone))

    def quiet(self, at: datetime) -> bool:
        if self.quiet_start is None or self.quiet_end is None:
            return False
        now = self.local(at).time()
        if self.quiet_start <= self.quiet_end:
            return self.quiet_start <= now < self.quiet_end
        return now >= self.quiet_start or now < self.quiet_end

    def allows(self, notice: str, level: str | None, at: datetime, spoken_last_hour: int) -> bool:
        if notice != "resolved" and level == "danger":
            return True
        if self.quiet(at):
            return False
        return spoken_last_hour < self.max_per_hour
