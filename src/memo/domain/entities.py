from dataclasses import dataclass
from datetime import datetime


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    location: str


@dataclass(frozen=True)
class Reading:
    message_id: str
    device_id: str
    observed_at: datetime
    received_at: datetime
    sequence: int
    temperature_c: float | None
    humidity_pct: float | None
    co2_ppm: float | None
    quality: str
