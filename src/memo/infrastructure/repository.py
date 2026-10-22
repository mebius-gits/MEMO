from dataclasses import asdict
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from memo.domain.entities import Conflict, Device, Reading
from memo.infrastructure.database import DeviceRow, ReadingRow


def to_reading(row: ReadingRow) -> Reading:
    return Reading(**{key: getattr(row, key) for key in Reading.__dataclass_fields__})


class SqlAlchemyRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_device(self, device: Device) -> Device:
        self.session.add(DeviceRow(**asdict(device)))
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if self.get_device(device.id) is not None:
                raise Conflict(f"Device '{device.id}' already exists") from exc
            raise
        return device

    def get_device(self, device_id: str) -> Device | None:
        row = self.session.get(DeviceRow, device_id)
        return Device(row.id, row.name, row.location) if row else None

    def list_devices(self, limit: int, offset: int) -> list[Device]:
        rows = self.session.scalars(
            select(DeviceRow).order_by(DeviceRow.id).limit(limit).offset(offset)
        )
        return [Device(row.id, row.name, row.location) for row in rows]

    def add_reading(self, reading: Reading) -> Reading:
        try:
            # Serialize writers per device (also acquires SQLite's write lock).
            self.session.execute(
                update(DeviceRow)
                .where(DeviceRow.id == reading.device_id)
                .values(name=DeviceRow.name)
            )
            self.session.add(ReadingRow(**asdict(reading)))
            self.session.flush()
            from memo.infrastructure.environment_events import project_reading

            project_reading(self.session, reading)
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            existing = self.session.get(ReadingRow, reading.message_id)
            if existing is None:
                raise
            original = to_reading(existing)
            # A retry can differ only in server-generated received_at.
            incoming = asdict(reading)
            saved = asdict(original)
            incoming.pop("received_at")
            saved.pop("received_at")
            if incoming != saved:
                raise Conflict("message_id is already used by a different payload") from exc
            return original
        return reading

    def list_readings(
        self,
        device_id: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[Reading]:
        query = select(ReadingRow).where(ReadingRow.device_id == device_id)
        if start is not None:
            query = query.where(ReadingRow.observed_at >= start)
        if end is not None:
            query = query.where(ReadingRow.observed_at <= end)
        query = query.order_by(ReadingRow.observed_at.desc(), ReadingRow.message_id)
        return [to_reading(row) for row in self.session.scalars(query.limit(limit).offset(offset))]
