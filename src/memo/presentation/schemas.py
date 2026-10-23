from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, from_attributes=True)


class DeviceSchema(Schema):
    id: Identifier
    name: str = Field(min_length=1, max_length=100)
    location: str = Field(default="", max_length=200)


# Plausible sensor ranges. Each metric is checked on its own: an implausible value is dropped
# (stored as unknown) while the packet's other readings are kept.
RANGES = {
    "temperature_c": (-80, 100),
    "humidity_pct": (0, 100),
    "co2_ppm": (0, 100000),
    # Direct sunlight reaches about 100 000 lux.
    "illuminance_lux": (0, 200000),
}


class Metrics(Schema):
    temperature_c: float | None = Field(default=None, allow_inf_nan=False)
    humidity_pct: float | None = Field(default=None, allow_inf_nan=False)
    co2_ppm: float | None = Field(default=None, allow_inf_nan=False)
    illuminance_lux: float | None = Field(default=None, allow_inf_nan=False)

    def rejected(self) -> list[str]:
        return [
            key
            for key, (low, high) in RANGES.items()
            if getattr(self, key) is not None and not low <= getattr(self, key) <= high
        ]

    def usable(self) -> dict[str, float | None]:
        rejected = self.rejected()
        return {key: None if key in rejected else getattr(self, key) for key in RANGES}

    @model_validator(mode="after")
    def require_metric(self):
        if all(value is None for value in self.usable().values()):
            raise ValueError("At least one metric within its plausible range is required")
        return self


class TelemetryInput(Schema):
    schema_version: Literal[1] = 1
    message_id: str = Field(min_length=1, max_length=128)
    device_id: Identifier
    observed_at: AwareDatetime
    sequence: int = Field(ge=0, le=2147483647)
    metrics: Metrics
    quality: Literal["ok", "suspect", "invalid"] = "ok"


class ReadingOutput(Schema):
    message_id: str
    device_id: str
    observed_at: AwareDatetime
    received_at: AwareDatetime
    sequence: int
    temperature_c: float | None
    humidity_pct: float | None
    co2_ppm: float | None
    quality: str
    illuminance_lux: float | None = None


class TelemetryAccepted(ReadingOutput):
    rejected_metrics: list[str] = Field(
        default=[], description="超出合理範圍而未保存（記為缺值）的指標；其他指標照常保存"
    )


class MetricState(Schema):
    value: float
    observed_at: AwareDatetime
    age_seconds: float
    fresh: bool = Field(description="30 秒內的有效值；過期的值只供參考，不代表目前環境")


class DeviceState(Schema):
    device_id: str
    checked_at: AwareDatetime
    online: bool = Field(description="任一指標在 30 秒內有有效值")
    metrics: dict[str, MetricState | None]
    feels_like_c: float | None = Field(
        description="由新鮮的溫度（與濕度）計算；溫度不新鮮時為 null"
    )


class MetricStats(Schema):
    avg: float
    min: float
    max: float


class ReadingBucket(Schema):
    start: AwareDatetime
    count: int
    temperature_c: MetricStats | None
    humidity_pct: MetricStats | None
    co2_ppm: MetricStats | None
    illuminance_lux: MetricStats | None
