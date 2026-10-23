from dataclasses import replace
from datetime import datetime, timedelta, timezone

from memo.application.ports import Repository
from memo.domain.entities import Device, NotFound, Reading

METRICS = ("temperature_c", "humidity_pct", "co2_ppm", "illuminance_lux")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class EnvironmentService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def register_device(self, device: Device) -> Device:
        return self.repository.add_device(device)

    def get_device(self, device_id: str) -> Device:
        device = self.repository.get_device(device_id)
        if device is None:
            raise NotFound(f"Device '{device_id}' does not exist")
        return device

    def list_devices(self, limit: int, offset: int) -> list[Device]:
        return self.repository.list_devices(limit, offset)

    def ingest(self, reading: Reading) -> Reading:
        self.get_device(reading.device_id)
        return self.repository.add_reading(
            replace(
                reading,
                observed_at=reading.observed_at.astimezone(timezone.utc),
                received_at=reading.received_at.astimezone(timezone.utc),
            )
        )

    def history(
        self,
        device_id: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[Reading]:
        self.get_device(device_id)
        return self.repository.list_readings(device_id, start, end, limit, offset)

    def latest_state(self, device_id: str) -> dict[str, tuple[float, datetime] | None]:
        """Each metric's newest valid value; metrics fail independently, so they may differ
        in age."""
        self.get_device(device_id)
        return {metric: self.repository.latest_valid(device_id, metric) for metric in METRICS}

    def summary(
        self, device_id: str, start: datetime, end: datetime, bucket_seconds: int
    ) -> list[dict]:
        """Average, minimum and maximum of valid readings per time bucket; empty buckets are
        left out rather than filled with zeros. Buckets align to UTC clock boundaries."""
        self.get_device(device_id)
        buckets: dict[int, dict[str, list[float]]] = {}
        for reading in self.repository.valid_readings(device_id, start, end):
            index = int((reading.observed_at - EPOCH).total_seconds() // bucket_seconds)
            bucket = buckets.setdefault(index, {metric: [] for metric in METRICS})
            for metric in METRICS:
                if (value := getattr(reading, metric)) is not None:
                    bucket[metric].append(value)
        return [
            {
                "start": EPOCH + timedelta(seconds=index * bucket_seconds),
                "count": max(len(values) for values in bucket.values()),
                **{
                    metric: {
                        "avg": round(sum(values) / len(values), 2),
                        "min": min(values),
                        "max": max(values),
                    }
                    if values
                    else None
                    for metric, values in bucket.items()
                },
            }
            for index, bucket in sorted(buckets.items())
        ]
