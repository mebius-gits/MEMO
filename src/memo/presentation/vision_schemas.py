from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from memo.presentation.interaction_schemas import image_problem
from memo.presentation.schemas import Schema

Answer = Literal["yes", "no", "unknown"]
Notify = Literal["on_alert", "on_change", "always"]
# memo.domain.vision.TRIGGER_LABELS
Trigger = Literal[
    "light",
    "co2_high",
    "co2_rising",
    "temperature_high",
    "temperature_low",
    "temperature_rising",
    "humidity_high",
    "humidity_low",
]


class Roi(Schema):
    """Part of the frame to look at, as fractions of the raw (unmirrored) camera frame."""

    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    w: float = Field(ge=0.05, le=1)
    h: float = Field(ge=0.05, le=1)

    @model_validator(mode="after")
    def inside(self):
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("The region must lie inside the frame")
        return self


class LookConfig(Schema):
    mode: Literal["check", "change", "report"] = Field(
        default="check",
        description="check：是非題；change：和基準照片比有沒有變化；report：描述畫面，連同環境一起回報",
    )
    question: str = Field(
        min_length=1, max_length=60, examples=["使用者是不是駝背？", "桌上的包裹"]
    )
    alert_on: Literal["yes", "no"] = Field(
        default="yes", description="需要提醒的答案；「電腦有沒有關機」是 no"
    )
    notify: Notify = Field(
        default="on_alert",
        description="on_alert：出現、持續或解除需要提醒的答案時說；on_change：答案改變時說；"
        "always：每次都說（report 一律每次說）",
    )
    roi: Roi | None = None
    watch: bool = Field(
        default=False,
        description="change 才有：持續盯著，網頁每 2 秒比對範圍，有變化並穩定後立刻拍照比對",
    )
    on: list[Trigger] = Field(
        default_factory=list,
        description="這些感測狀況發生時也看一次：light 燈光明顯變化，或環境事件開始（co2_high…）；"
        "kind 為 event 的任務只靠這些觸發",
    )


class LookUpdate(Schema):
    """Change a look task's settings; a field left out keeps its value, roi null clears it."""

    alert_on: Literal["yes", "no"] | None = None
    notify: Notify | None = None
    roi: Roi | None = None
    watch: bool | None = None
    on: list[Trigger] | None = None


class LookStateOutput(Schema):
    last: Literal["yes", "no", "unknown", "seen"] | None = None
    last_summary: str | None = None
    last_at: str | None = None
    changed_at: str | None = None
    alert_streak: int = 0
    unknown_streak: int = 0
    baseline_text: str | None = None
    baseline_at: str | None = None
    changes: int = 0
    sensor_run_at: str | None = None
    sensor_run: int | None = None
    triggered_by: list[str] | None = None


class CaptureQuality(Schema):
    """Measured in the browser on a grey copy of the photo; recorded, never used to retake."""

    brightness: float | None = Field(default=None, ge=0, le=255, description="平均亮度 0-255")
    contrast: float | None = Field(default=None, ge=0, le=255, description="亮度標準差")
    sharpness: float | None = Field(
        default=None, ge=0, le=1_000_000, description="Laplacian 變異數，越小越模糊"
    )
    width: int | None = Field(default=None, ge=1, le=10000)
    height: int | None = Field(default=None, ge=1, le=10000)


class CaptureInput(Schema):
    task_id: str = Field(min_length=1, max_length=64)
    attempt: Literal[1, 2] = Field(default=1, description="2 是確認用的第二張照片")
    image: str | None = Field(
        default=None,
        max_length=2_800_100,
        description="data:image/jpeg|png|webp;base64,...，最大 2 MB；只交給模型看，不保存。",
    )
    reference: str | None = Field(
        default=None,
        max_length=2_800_100,
        description="變化比對的基準照片（只存在瀏覽器），與 image 一起交給模型比較。",
    )
    quality: CaptureQuality | None = None
    captured_at: AwareDatetime | None = Field(
        default=None, description="拍下的時間；與伺服器差太多時改用伺服器時間"
    )
    skip: Literal["camera_off", "camera_unavailable", "permission_denied"] | None = Field(
        default=None, description="沒有照片時的原因；這次執行也會留下紀錄"
    )
    trigger: Literal["watch"] | None = Field(
        default=None, description="watch：網頁持續盯著時看到變化並穩定後觸發，不需要確認照片"
    )
    moment: str | None = Field(
        default=None,
        max_length=2_800_100,
        description="變化剛開始時的畫面，讓模型描述當時的動作；不保存。",
    )

    @field_validator("image", "reference", "moment")
    @classmethod
    def check_image(cls, value):
        if value is not None and (problem := image_problem(value)):
            raise ValueError(problem)
        return value

    @model_validator(mode="after")
    def photo_or_reason(self):
        if (self.image is None) == (self.skip is None):
            raise ValueError("Send either an image or a skip reason")
        return self


class CaptureOutput(Schema):
    status: Literal["done", "confirming", "queued", "skipped", "cancelled"]
    decision: (
        Literal["done", "confirm", "notify", "muted", "unconfirmed", "baseline", "skipped"] | None
    ) = None
    result: Literal["yes", "no", "unknown", "seen", "skipped"] | None = None
    notice: Literal["alert", "repeat", "recovered", "changed", "report", "unseen"] | None = None
    level: int | None = None
    reason: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    observation_id: str | None = None
    store_reference: bool = Field(
        default=False, description="true：網頁要把這張照片存成新的基準照片"
    )
    confirm_after_seconds: int | None = Field(
        default=None, description="要確認時：第二張照片應在第一張之後幾秒拍"
    )


class CaptureRequest(Schema):
    task_id: str
    job_id: str
    attempt: Literal[1, 2]
    mode: Literal["check", "change", "report"]
    question: str
    roi: Roi | None = None
    baseline: bool = Field(description="變化比對還沒有基準：這張照片會成為基準")
    expires_at: datetime


class Watched(Schema):
    job_id: str
    question: str
    roi: Roi | None = None


class PendingCaptures(Schema):
    requests: list[CaptureRequest]
    active_jobs: int = Field(description="啟用中的拍照任務數")
    watching: list[Watched] = Field(
        default_factory=list, description="持續盯著的任務：網頁每 2 秒比對這些範圍"
    )


class People(Schema):
    count: int | None = None
    description: str = ""


class Scene(Schema):
    """人（people）、事（activity）、地（place）、物（objects）、動作（actions）與光線；
    時是 captured_at。"""

    people: People = Field(default_factory=People)
    animals: list[str] = Field(default_factory=list)
    activity: str = ""
    place: str = ""
    objects: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    lighting: str = ""


class QualityRecord(Schema):
    metrics: dict[str, float] = Field(default_factory=dict)
    flags: list[Literal["dark", "overexposed", "low_contrast", "blurry"]] = Field(
        default_factory=list
    )
    visibility: Literal["clear", "partial", "poor"] | None = None


class ObservationOutput(Schema):
    id: str
    job_id: str
    run: int
    attempt: int
    captured_at: datetime
    mode: Literal["check", "change", "report"]
    question: str
    result: Literal["yes", "no", "unknown", "seen", "skipped"]
    detail: str | None = None
    scene: Scene | None = None
    quality: QualityRecord | None = None
    reason: str | None = None
    decision: str | None = None
    notice: str | None = None
    level: int | None = None
    sensors: dict | None = Field(
        default=None, description="拍照當下的感測數據：亮度、CO₂、溫濕度、光線是否剛變過"
    )


class ObserveInput(Schema):
    trigger: Literal["patrol", "scene_change", "person_enter", "person_leave"] = Field(
        description="為什麼看：例行巡視、畫面變化並穩定、有人出現、有人離開"
    )
    image: str | None = Field(
        default=None, max_length=2_800_100, description="整個畫面的照片；只交給模型看，不保存。"
    )
    quality: CaptureQuality | None = None
    captured_at: AwareDatetime | None = None
    skip: Literal["camera_off", "camera_unavailable", "permission_denied"] | None = None

    @field_validator("image")
    @classmethod
    def check_image(cls, value):
        if value is not None and (problem := image_problem(value)):
            raise ValueError(problem)
        return value

    @model_validator(mode="after")
    def photo_or_reason(self):
        if (self.image is None) == (self.skip is None):
            raise ValueError("Send either an image or a skip reason")
        return self


class ObserveOutput(Schema):
    status: Literal["recorded"]
    result: Literal["seen", "unknown", "skipped"]
    summary: str | None = None
    situation: str | None = Field(default=None, description="這次看到的人事地物與動作")
    reason: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    events: list[str] = Field(
        default_factory=list,
        description="和上次相比的事件：start、arrival、departure、animal、animal_gone、lights",
    )
    notice: Literal["arrival", "animal"] | None = Field(
        default=None, description="要說出來的事；其他事件只記錄"
    )
    observation_id: str


class SceneNow(Schema):
    seen_at: str
    people: int | None = None
    people_detail: str | None = None
    animals: list[str] = Field(default_factory=list)
    lighting: str | None = None
    summary: str | None = None


class ObservationState(Schema):
    now: SceneNow | None = Field(description="最近一次看到的現場；null 表示還沒看過")
    today_counts: dict[str, int] = Field(default_factory=dict)
    patrol_minutes: int = Field(description="網頁例行巡視的間隔")
    min_gap_seconds: int = Field(description="兩次觀察的最短間隔")


class TimelineEntry(Schema):
    at: datetime
    source: Literal["observe", "look", "environment"]
    kind: str
    text: str
    spoken: bool
