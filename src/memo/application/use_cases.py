from dataclasses import replace
from datetime import datetime, timezone

from memo.application.ports import Repository
from memo.domain.entities import Device, NotFound, Reading


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
