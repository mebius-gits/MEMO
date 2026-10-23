"""Shared HTTP/MQTT wire contract conversion, outside the application layer."""

from datetime import datetime

from memo.domain.entities import Reading
from memo.presentation.schemas import TelemetryInput


def to_reading(payload: TelemetryInput, received_at: datetime) -> Reading:
    return Reading(
        message_id=payload.message_id,
        device_id=payload.device_id,
        observed_at=payload.observed_at,
        received_at=received_at,
        sequence=payload.sequence,
        quality=payload.quality,
        **payload.metrics.usable(),
    )
