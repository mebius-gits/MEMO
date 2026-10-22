from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    pass


class DeviceRow(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(String(200))


class ReadingRow(Base):
    __tablename__ = "readings"
    __table_args__ = (Index("ix_readings_device_observed", "device_id", "observed_at"),)
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    sequence: Mapped[int] = mapped_column(Integer)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    humidity_pct: Mapped[float | None] = mapped_column(Float)
    co2_ppm: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(16))


class EventStateRow(Base):
    __tablename__ = "environment_states"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON)


class TaskMemoryRow(Base):
    __tablename__ = "task_memory"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(64))
    event_version: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(64))
    event_status: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    response: Mapped[dict | None] = mapped_column(JSON)


class ConversationRow(Base):
    __tablename__ = "conversation_memory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    session_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    user_text: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    __table_args__ = (
        Index("ix_conversation_scope", "device_id", "session_id", "id"),
        Index("uq_conversation_request", "device_id", "session_id", "request_id", unique=True),
    )


def build_engine(url: str):
    kwargs = (
        {"connect_args": {"check_same_thread": False, "timeout": 10}}
        if url.startswith("sqlite")
        else {}
    )
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine
