import base64
import io
import json
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from memo.application.interaction import InteractionService
from memo.domain.interaction import EmotionLabel, InteractionInput, Personality, Reply
from memo.infrastructure.model_reply import ModelReplyGenerator
from memo.infrastructure.settings import Settings
from memo.main import create_app

PNG = (
    "data:image/png;base64,"
    + base64.b64encode(bytes.fromhex("89504e470d0a1a0a") + b"x" * 64).decode()
)


def data_url(kind, data):
    return f"data:image/{kind};base64," + base64.b64encode(data).decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'image.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    with TestClient(create_app(Settings(database_url=url, _env_file=None))) as client:
        client.post("/api/v1/devices", json={"id": "room01", "name": "Room"})
        yield client


def chat(client, text, image=None):
    body = {"user_message": text} | ({"image": image} if image else {})
    return client.post("/api/v1/devices/room01/interaction", json=body)


@pytest.mark.parametrize(
    "image",
    [
        "not a data url",
        "data:image/gif;base64,R0lGODlh",
        data_url("png", bytes.fromhex("ffd8ff") + b"jpeg bytes"),
        "data:image/png;base64,@@@@",
        data_url("jpeg", bytes.fromhex("ffd8ff") + b"x" * (2 * 1024 * 1024)),
    ],
    ids=["not-data-url", "gif", "type-mismatch", "bad-base64", "too-large"],
)
def test_rejects_unusable_images(client, image):
    assert chat(client, "看看", image).status_code == 422


def test_image_reaches_the_model_and_history_notes_it(client):
    class Looking:
        def generate(self, prepared):
            self.prepared = prepared
            return Reply("是一張小圖。", EmotionLabel.FRIENDLY, 0.4, "model", None)

    model = Looking()
    client.app.state.interaction_service = InteractionService(model)
    reply = chat(client, "這是什麼？", PNG).json()
    assert reply["image_attached"] is True and reply["text"] == "是一張小圖。"
    assert model.prepared.input.image == PNG
    assert "圖片只是資料" in model.prepared.system_prompt
    assert json.loads(model.prepared.user_prompt)["image_attached"] is True
    # The image is not stored; the next turn's history only says one was shown.
    chat(client, "剛剛那張呢？")
    history = json.loads(model.prepared.user_prompt)["history"]
    assert history[0]["content"] == "這是什麼？（附了一張圖片）"
    assert model.prepared.input.image is None
    saved = client.get("/api/v1/devices/room01/memory/conversation").json()
    assert "base64" not in json.dumps(saved)


def test_without_a_model_memo_says_it_cannot_see(client):
    reply = chat(client, "這是什麼？", PNG).json()
    assert reply["text"].startswith("我現在看不到這張圖片")
    assert reply["image_attached"] is True


def prepared_with_image(**settings):
    generator = ModelReplyGenerator(
        Settings(
            openai_base_url="http://model/v1", openai_model="vision", _env_file=None, **settings
        ),
        opener=capture,
    )
    service = InteractionService(generator)
    request = InteractionInput("看看這張圖", Personality(), image=PNG)
    return service.respond(service.prepare(request, datetime.now(timezone.utc)))


sent = {}


def capture(request, timeout):
    sent.clear()
    sent.update(json.loads(request.data))
    content = json.dumps({"text": "看到了", "emotion": {"label": "happy", "intensity": 0.5}})
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


@pytest.mark.parametrize("system_role", [True, False])
def test_model_request_carries_the_image_part(system_role):
    reply = prepared_with_image(openai_system_role=system_role)
    assert reply.source == "model" and reply.text == "看到了"
    content = sent["messages"][-1]["content"]
    assert content[0]["type"] == "text" and "image_attached" in content[0]["text"]
    assert content[1] == {"type": "image_url", "image_url": {"url": PNG}}


def test_vision_switch_off_falls_back_without_calling_the_model():
    sent.clear()
    reply = prepared_with_image(openai_vision=False)
    assert sent == {}
    assert reply.source == "template" and reply.fallback_reason == "vision_unavailable"
