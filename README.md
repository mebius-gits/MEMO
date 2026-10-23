# MEMO：FastAPI 雛形

環境感知 AI 角色名稱為 **MEMO**。

## Docker 一次啟動 API 與 Node-RED

```powershell
docker compose up -d --build
```

開啟 http://localhost:1880，已預載完整範例節點；按「送出一筆模擬資料」即可自動註冊裝置、寫入讀值並查詢歷史。API 文件：http://localhost:8000/docs。需要 Docker Desktop（Linux containers）。詳見 [Node-RED 操作說明](node-red/README.md)。

目前 Compose 也包含 Mosquitto 與獨立 MQTT environment-worker。要每 5 秒持續模擬資料，執行 `docker compose --profile demo up -d --build --wait`。接收流程、設定與驗證方式見 [MQTT Worker 說明](docs/mqtt-worker.md)。

MEMO 從感測輸入到主動說話的完整架構（規則、嚴重程度、排程任務、提示與模型）見 [LLM 架構](docs/llm-architecture.md)。

Compose 另含本機語音服務 `speech`（http://localhost:8001），在 CPU 上執行，不需 GPU，詳見 [語音服務說明](speech/README.md)。

Compose 也含自架的 SearXNG 搜尋（`searxng`，http://localhost:8080），免費、免金鑰，讓 MEMO 能回答「查一下明天台北天氣」這類問題；只有 API 會呼叫它，只送出搜尋關鍵字。設定在 [searxng/settings.yml](searxng/settings.yml)。網頁的 MEMO 旁邊有一盞小檯燈，可在側欄切換關燈、暗光、溫暖光、白天光。MEMO 不知道燈的模式，也不會控制它，只透過光照感測（`illuminance_lux`）感覺空間多亮多暗；示範時由 Node-RED 依檯燈模擬這個讀值。對話時可以上傳或貼上一張圖片給 MEMO 看（見第 4.6 節），圖片不會被保存。詳見 [LLM 架構](docs/llm-architecture.md) 第 4.4、4.5 節。

依 `environment_ai_plan.md` 建立的 Clean Architecture 後端。這一版完成裝置與感測歷史的 HTTP 流程，讓後續 MQTT、規則引擎與 AI 可以沿用 application 層。

## 本機啟動（PowerShell）

需要 Python 3.11+ 與 uv。在專案根目錄執行：

```powershell
uv sync
Copy-Item .env.example .env
uv run alembic upgrade head
uv run uvicorn memo.main:create_app --factory --reload --host 127.0.0.1
```

Swagger：http://127.0.0.1:8000/docs；OpenAPI：http://127.0.0.1:8000/openapi.json。
預設資料保存於 `memo.db`，重啟後保留。不在 API 啟動時自動建表，請先執行 migration。

環境變數不使用前綴（例如 `DATABASE_URL`；模型連線沿用 OpenAI 慣例的 `OPENAI_*`；MQTT worker 使用 `MQTT_`）。將 `.env.example` 複製為 `.env` 後設定；Compose API 也會載入這個檔案，容器資料庫仍使用 `/data/memo.db`。

模型連線使用 `OPENAI_API_KEY`、`OPENAI_BASE_URL`（含 API 版本路徑，預設 `https://api.openai.com/v1`）、`OPENAI_MODEL`、`OPENAI_TIMEOUT_SECONDS`，以及選填的 `OPENAI_REASONING_EFFORT`、`OPENAI_MAX_COMPLETION_TOKENS`（設定才會送出；推理模型設 `low` 或 `none` 可大幅縮短回覆時間），與相容性開關 `OPENAI_SYSTEM_ROLE`、`OPENAI_JSON_MODE`（模型不理會 system 角色或 JSON 模式時設 `false`）。填入模型名稱後會實際呼叫 Chat Completions 相容端點；未設定或呼叫失敗時使用明確標示的備援回覆。API key 僅放後端，設定物件以 `SecretStr` 遮蔽顯示。

升級既有安裝時，請移除環境變數的舊 `MEMO_` 前綴（`MEMO_MQTT_*` 改為 `MQTT_*`；模型設定 `LLM_*` 改為 `OPENAI_*`），並同步更新 MQTT 發布端的 `memo/v1/` topic。既有本機資料庫需搬移至新預設路徑，或用 `DATABASE_URL` 指向原資料庫；Docker volume 路徑沒有變更。

## 分層與依賴

```text
src/memo/
├── domain/          # 純 Python entity 與業務錯誤
├── application/     # Use cases、Repository Protocol
├── infrastructure/  # SQLAlchemy、Repository 實作、環境設定
├── presentation/    # FastAPI routes、Pydantic HTTP schema
└── main.py          # Composition root：組裝依賴、生命週期、錯誤處理
migrations/          # Alembic schema 版本
tests/               # 使用獨立 SQLite 的 API / migration 整合測試
```

依賴方向：`presentation → application → domain`；`infrastructure` 實作 application 的介面；`main.py` 負責接線。Domain / Application 不引用 FastAPI、Pydantic 或 SQLAlchemy。每個 request 使用獨立 Session；目前每個寫入用例只有單一持久化操作，由 Repository 提交交易。

日後新增事件與 outbox 時，需引入 Unit of Work，讓兩者共用交易。MQTT 應使用獨立 worker 入口並呼叫 application use case，避免 API 多進程造成重複訂閱。

## 已實作 API

| Method | Path | 用途 |
| --- | --- | --- |
| GET | `/health/live` | 程序存活 |
| GET | `/health/ready` | 資料庫與必要資料表可讀 |
| POST | `/api/v1/devices` | 註冊裝置 |
| GET | `/api/v1/devices` | 裝置清單，支援 limit / offset |
| GET | `/api/v1/devices/{id}` | 裝置詳情 |
| POST | `/api/v1/telemetry` | HTTP 測試入口，寫入感測資料；回應列出被剔除的指標 |
| GET | `/api/v1/devices/{id}/readings` | 依觀測時間倒序，支援 start / end / limit / offset |
| GET | `/api/v1/devices/{id}/readings/summary` | 依時間區間彙總平均／最小／最大（預設最近一小時、每分鐘） |
| GET | `/api/v1/devices/{id}/state` | 每個指標最新的有效值、資料年齡、是否新鮮、體感溫度 |
| GET | `/api/v1/devices/{id}/events/history` | 最近的事件（開始、結束、最高等級、規則版本） |
| GET | `/api/v1/devices/{id}/events/{event_id}` | 事件證據與每次變化 |
| POST | `/api/v1/devices/{id}/events/{event_id}/ack`、`/snooze`、`/handled` | 確認、延後、記錄已處理並排程追蹤 |
| GET／PUT | `/api/v1/rules`、`/api/v1/rules/{id}` | 查詢或修改規則門檻，免重啟 |
| GET／PUT | `/api/v1/devices/{id}/settings` | 人格、勿擾時段、每小時提醒上限 |
| GET／PUT | `/api/v1/devices/{id}/lamp` | 檯燈模式：off／dim／warm／day（也接受 on、brighter、dimmer） |

對話、通知、排程等其他端點見 [LLM 架構](docs/llm-architecture.md) 第 10 節。

```powershell
$device = @{ id = 'room01'; name = '書房'; location = '二樓' } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/devices -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($device))

$reading = @{
    schema_version = 1
    message_id = [guid]::NewGuid().ToString()
    device_id = 'room01'
    observed_at = [DateTimeOffset]::UtcNow.ToString('o')
    sequence = 1
    metrics = @{ temperature_c = 29.2; humidity_pct = 65; co2_ppm = 1100 }
    quality = 'ok'
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/telemetry -Method Post -ContentType 'application/json' -Body $reading
Invoke-RestMethod http://127.0.0.1:8000/api/v1/devices/room01/readings
```

相同 message_id 與相同內容重送時回傳原資料；同 ID 不同內容回 409。裝置不存在回 404，格式錯誤回 422。時間必須包含時區，落庫及輸出統一 UTC；缺少的指標保留 null。歷史查詢時間範圍含兩端。

每個指標分別驗證：超出合理範圍的指標記為缺值並列在回應的 `rejected_metrics`，同一封包的其他指標照常保存；所有指標都無效才回 422。MQTT 使用同一份驗證。`quality` 不是 `ok` 的讀值會保存，但不參與判斷。觀測時間與接收時間相差超過 30 秒的補送資料只存歷史，不觸發提醒。

## 驗證與資料庫

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run alembic check
```

`uv.lock` 鎖定實際依賴版本。使用 PostgreSQL 時先建立資料庫，再修改 `.env`：

```dotenv
DATABASE_URL=postgresql+psycopg://memo:password@localhost:5432/memo
```

接著執行 `uv run alembic upgrade head`。目前自動測試使用 SQLite；PostgreSQL 需另做環境整合驗證。

## LLM 輸入、人格與文字／情緒契約

已提供 `/api/v1/llm/personality`、`/api/v1/llm/prepare`、`/api/v1/llm/respond`。已實作模型呼叫、格式驗證與模板備援。人格可依請求設定 gentle / lively / concise；輸出包含 text 與 emotion（label、intensity）。

詳見 [LLM 介面說明](docs/llm-api.md)、[OpenAPI YAML](docs/openapi.yaml) 與 [模型輸出 JSON Schema](docs/llm-output.schema.json)。

模型、任務／對話記憶、環境事件與六分鐘 CO₂ 劇本已實作，見 [執行說明](docs/companion-runtime.md)。

## 下一階段

- Mosquitto 帳號、ACL、TLS；MQTT `status`（Last Will）與送往實體裝置的 `commands`／`acks`。
- API 身分驗證與裝置授權（目前是本機開發雛形）。
- 資料保留策略（原始資料 30 天、長期改存彙總）與 PostgreSQL 整合驗證。
- 展示用的注入時鐘、通知 SSE。

以上功能尚未實作，完整規劃仍以 `environment_ai_plan.md` 為準。

架構參考：[FastAPI 多檔案應用](https://fastapi.tiangolo.com/tutorial/bigger-applications/)、[環境設定](https://fastapi.tiangolo.com/advanced/settings/)、[Lifespan](https://fastapi.tiangolo.com/advanced/events/)。
