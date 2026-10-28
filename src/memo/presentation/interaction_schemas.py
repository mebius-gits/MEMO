import base64
import binascii
import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator

from memo.domain.interaction import EmotionLabel, Tone
from memo.presentation.schemas import Identifier, Metrics, Schema


class PersonalityConfig(Schema):
    name: Literal["MEMO"] = "MEMO"
    language: Literal["zh-TW"] = "zh-TW"
    tone: Tone = Field(
        default=Tone.GENTLE, description="人格語氣：溫柔、活潑或簡潔；也用於備援模板。"
    )
    persona: str = Field(
        default="",
        max_length=300,
        description="人格描述（角色與說話風格），交給模型扮演；只影響措辭，不改變事實或規則。",
        examples=["愛吐槽的損友，語氣輕鬆幽默，但該提醒的一定講到。"],
    )


class HistoryMessage(Schema):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ObservationInput(Schema):
    observed_at: AwareDatetime = Field(description="含時區的觀測時間；超過 30 秒不作目前讀值。")
    metrics: Metrics
    quality: Literal["ok", "suspect", "invalid"] = "ok"


class EventInput(Schema):
    event_id: str = Field(min_length=1, max_length=128)
    event_version: int = Field(ge=1)
    status: Literal["pending", "active", "recovering", "resolved", "unknown"]
    rule_evidence: str = Field(
        min_length=1, max_length=1000, description="呼叫端提供的規則依據，不是 LLM 自行判斷。"
    )


class InteractionContext(Schema):
    device_id: Identifier | None = None
    observation: ObservationInput | None = None
    event: EventInput | None = None
    approved_suggestions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=5, description="呼叫端已核准建議；不代表 API 已驗證。"
    )


class InteractionRequest(Schema):
    request_id: UUID = Field(default_factory=uuid4, description="追蹤識別碼；不提供儲存或去重。")
    user_message: str = Field(min_length=1, max_length=2000, examples=["MEMO，現在環境如何？"])
    personality: PersonalityConfig = Field(
        default_factory=PersonalityConfig, description="僅套用本次請求，不覆寫預設或持久化。"
    )
    context: InteractionContext = Field(default_factory=InteractionContext)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=10)


class EmotionOutput(Schema):
    label: EmotionLabel = Field(description="MEMO 的顯示情緒，不代表使用者情緒或事件嚴重程度。")
    intensity: float = Field(ge=0, le=1, allow_inf_nan=False, description="表情強度，0 到 1。")


IMAGE_MAX_BYTES = 2 * 1024 * 1024
_IMAGE_URL = re.compile(r"^data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/]+={0,2})$")


def image_problem(value: str) -> str | None:
    """Why a data URL is not an acceptable image, or None. The bytes must match the type."""
    match = _IMAGE_URL.match(value)
    if not match:
        return "image 必須是 JPEG、PNG 或 WebP 的 base64 data URL"
    try:
        data = base64.b64decode(match[2], validate=True)
    except (binascii.Error, ValueError):
        return "image 的 base64 內容無效"
    if len(data) > IMAGE_MAX_BYTES:
        return "圖片不可超過 2 MB"
    signature = {
        "jpeg": data.startswith(bytes.fromhex("ffd8ff")),
        "png": data.startswith(bytes.fromhex("89504e470d0a1a0a")),
        "webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }[match[1]]
    return None if signature else "圖片內容與宣告的格式不符"


class DeviceInteractionRequest(Schema):
    session_id: Identifier = "default"
    request_id: UUID = Field(default_factory=uuid4)
    user_message: str = Field(min_length=1, max_length=2000)
    personality: PersonalityConfig = Field(default_factory=PersonalityConfig)
    image: str | None = Field(
        default=None,
        max_length=2_800_100,
        description="使用者上傳的一張圖片，data:image/jpeg|png|webp;base64,...，最大 2 MB；"
        "只交給模型看，不保存。",
    )
    camera: Literal["on", "off"] | None = Field(
        default=None,
        description="網頁鏡頭現在是否開著，讓 MEMO 知道自己看不看得到；未提供表示不知道。",
    )

    @field_validator("image")
    @classmethod
    def check_image(cls, value):
        if value is not None and (problem := image_problem(value)):
            raise ValueError(problem)
        return value


class ModelReply(Schema):
    text: str = Field(min_length=1, max_length=2000, description="顯示給使用者的繁體中文文字。")
    emotion: EmotionOutput


class WebSource(Schema):
    title: str
    url: str
    source: str = Field(description="網站名稱（網域）")


class InteractionResponse(ModelReply):
    request_id: UUID
    character: Literal["MEMO"] = "MEMO"
    source: Literal["template", "model"] = "template"
    fallback_reason: (
        Literal[
            "model_not_configured",
            "model_timeout",
            "model_unavailable",
            "invalid_model_output",
            "vision_unavailable",
        ]
        | None
    ) = "model_not_configured"
    context_source: Literal["caller_supplied", "stored_readings"] = "caller_supplied"
    event_id: str | None = None
    event_version: int | None = None
    warnings: list[
        Literal[
            "missing_observation",
            "unreliable_observation",
            "future_observation",
            "stale_observation",
        ]
    ]
    actions: list[str] = Field(
        default_factory=list,
        description="後端為這則訊息完成的動作（設定提醒、延後、取消等），以中文描述。",
    )
    image_attached: bool = Field(default=False, description="這則訊息附了圖片（圖片本身不保存）")
    look_requested: bool = Field(
        default=False,
        description="MEMO 需要看實物才能回答：網頁拍照後帶著照片再送一次同一句話。",
    )
    mode: Literal["assignment", "interaction"] | None = Field(
        default=None,
        description="assignment：指派（觀察、回報、問看到什麼），先講事實；interaction：日常互動",
    )
    sources: list[WebSource] = Field(
        default_factory=list, description="這則回覆參考的網路搜尋結果（SearXNG），供網頁顯示來源。"
    )


class PreparedResponse(Schema):
    request_id: UUID
    system_prompt: str
    user_prompt: str = Field(description="JSON 編碼的使用者訊息及已篩選上下文。")
    output_schema: dict = Field(description="給未來模型 adapter 使用的 JSON Schema。")
    warnings: list[str]
