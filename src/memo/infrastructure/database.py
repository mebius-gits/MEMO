from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
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
    illuminance_lux: Mapped[float | None] = mapped_column(Float)


class EventStateRow(Base):
    __tablename__ = "environment_states"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON)


class EventUpdateRow(Base):
    """Append-only evidence for an event: notices, unknown/offline spells, user actions."""

    __tablename__ = "event_updates"
    __table_args__ = (Index("ix_event_updates_device_event", "device_id", "event_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    rule_id: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[str] = mapped_column(String(64))
    event_version: Mapped[int] = mapped_column(Integer)
    # active | escalated | reminder | resolved | unknown | acknowledged | snoozed | handled
    # | rule_changed | rule_disabled
    kind: Mapped[str] = mapped_column(String(24))
    level: Mapped[str | None] = mapped_column(String(16))
    value: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(UTCDateTime())


class RuleConfigRow(Base):
    """Threshold overrides for a built-in rule; absent means the code defaults apply."""

    __tablename__ = "rule_configs"
    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    override: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


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


class ScheduledJobRow(Base):
    """A reminder, recurring task or follow-up; due runs become task_memory notices."""

    __tablename__ = "scheduled_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # once | interval | daily | followup
    purpose: Mapped[str] = mapped_column(String(16))  # remind | summary | followup
    message: Mapped[str] = mapped_column(String(120))
    minutes: Mapped[int | None] = mapped_column(Integer)
    time_of_day: Mapped[str | None] = mapped_column(String(5))
    baseline: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(16), index=True
    )  # active | paused | done | cancelled
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    session_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DeviceSettingRow(Base):
    """Per-device preferences; the last persona used in chat also voices proactive notices."""

    __tablename__ = "device_settings"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    personality: Mapped[dict] = mapped_column(JSON)
    # Per-device overrides of server defaults: quiet_hours, max_proactive_per_hour.
    preferences: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LampStateRow(Base):
    """The desk lamp beside MEMO; no row means the default mode."""

    __tablename__ = "lamp_states"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8))  # off | dim | warm | day
    # The last lit mode, so "開燈" brings back what was on before "關燈".
    last_on: Mapped[str | None] = mapped_column(String(8))
    changed_by: Mapped[str] = mapped_column(String(16))  # chat | api
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


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


class MemorySummaryRow(Base):
    """Compressed conversation: the running summary of an open episode, or a finished one."""

    __tablename__ = "memory_summaries"
    __table_args__ = (
        Index("ix_memory_summaries_scope", "device_id", "session_id", "last_turn_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    session_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # running | episode
    first_turn_id: Mapped[int] = mapped_column(Integer)
    last_turn_id: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime())
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class UserProfileRow(Base):
    """What the person has said about themselves (name, likes, projects), per memory scope."""

    __tablename__ = "user_profiles"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


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
