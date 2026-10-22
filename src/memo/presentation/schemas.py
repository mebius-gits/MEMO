from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, from_attributes=True)


class DeviceSchema(Schema):
    id: Identifier
    name: str = Field(min_length=1, max_length=100)
    location: str = Field(default="", max_length=200)


class Metrics(Schema):
    temperature_c: float | None = Field(default=None, ge=-80, le=100, allow_inf_nan=False)
    humidity_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    co2_ppm: float | None = Field(default=None, ge=0, le=100000, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_metric(self):
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("At least one metric is required")
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
