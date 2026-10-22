# MEMO LLM 介面契約

本階段提供 OpenAPI 3.1、人格設定、模型輸入組裝與模板輸出。尚未呼叫外部 LLM，無需 API key。OpenAPI 是 HTTP 介面規格，不是 OpenAI 供應商 API。

- 完整規格：[openapi.yaml](openapi.yaml)
- 模型文字／情緒 JSON Schema：[llm-output.schema.json](llm-output.schema.json)
- 執行中的 Swagger：`http://localhost:8000/docs`
- 執行中的 OpenAPI JSON：`http://localhost:8000/openapi.json`

## API

| Method | Path | 用途 |
| --- | --- | --- |
| GET | `/api/v1/llm/personality` | 讀取 MEMO 預設人格 |
| POST | `/api/v1/llm/prepare` | 產生 system_prompt、user_prompt 與 output_schema；不呼叫模型 |
| POST | `/api/v1/llm/respond` | 產生文字與情緒；模型生成，失敗時模板備援 |

兩個 POST 共用 `InteractionRequest`。成功回 200，欄位不合法回 422。

## 人格設定

```json
{
  "name": "MEMO",
  "language": "zh-TW",
  "tone": "gentle",
  "persona": ""
}
```

角色名稱固定為 MEMO，語言目前支援繁體中文。`persona` 是選填的人格描述（最多 300 字），例如「愛吐槽的損友，語氣輕鬆幽默」，模型依此調整角色口吻；`tone` 仍決定基本語氣，也用於備援模板。

| tone | 表現 |
| --- | --- |
| gentle | 溫柔、清楚，預設人格 |
| lively | 輕快、友善 |
| concise | 簡潔、直接 |

人格透過請求中的 `personality` 覆寫，**僅影響本次請求**，不存入資料庫、不改寫預設。人格只影響措辭，不改變規則、環境事實或事件嚴重程度。固定角色邊界放在 system_prompt，不允許請求直接指定 system role。

## 輸入要求

| 欄位 | 必填 | 限制／用途 |
| --- | --- | --- |
| user_message | 是 | 1–2000 字，不能空白 |
| request_id | 否 | UUID，省略時產生；只用於追蹤，不提供去重或保存 |
| personality | 否 | 省略時使用預設人格 |
| context.device_id | 否 | 裝置識別碼；目前不查資料庫驗證 |
| context.observation | 否 | observed_at、metrics、quality；時間必須含時區 |
| context.event | 否 | 有提供時必須包含 event_id、event_version、status、rule_evidence |
| context.approved_suggestions | 否 | 最多 5 條、每條 1–300 字；由呼叫端核准 |
| history | 否 | 最多 10 則；role 僅 user / assistant，每則 1–2000 字 |

`metrics` 支援 temperature_c、humidity_pct、co2_ppm，至少一項非 null。採用現有感測資料範圍驗證，缺值不補零。未知欄位會被拒絕。

資料來源是 **caller_supplied**，代表由呼叫端提供；本 API 尚未從資料庫讀取環境事實，也不核對事件是否存在或版本是否最新。`approved_suggestions` 亦不是後端核准結果。

輸入組裝使用伺服器 UTC 時間，只保留 30 秒內且 quality=ok 的讀值。缺資料、過期、未來時間、品質異常會分別回傳 missing_observation、stale_observation、future_observation、unreliable_observation，並排除不可用讀值。這只是本次 LLM 輸入篩選，並非背景 stale 偵測或規則引擎。

最小請求：

```json
{
  "user_message": "MEMO，現在環境如何？",
  "personality": { "tone": "gentle" }
}
```

包含當下時間的 PowerShell 範例：

```powershell
$body = @{
    user_message = 'MEMO，請摘要這筆模擬環境資料。'
    personality = @{ tone = 'gentle' }
    context = @{
        device_id = 'room01'
        observation = @{
            observed_at = [DateTimeOffset]::UtcNow.ToString('o')
            metrics = @{ temperature_c = 29.2; humidity_pct = 65; co2_ppm = 1100 }
            quality = 'ok'
        }
    }
} | ConvertTo-Json -Depth 8
Invoke-RestMethod http://localhost:8000/api/v1/llm/respond -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

## 文字與情緒輸出

模型預期輸出的核心契約：

```json
{
  "text": "我是 MEMO，我陪你看看環境資料。",
  "emotion": { "label": "friendly", "intensity": 0.4 }
}
```

`text` 為 1–2000 字。`emotion.intensity` 為 0–1；`emotion.label` 限定：

| label | 顯示情緒 |
| --- | --- |
| neutral | 中性 |
| friendly | 友善 |
| concerned | 關切 |
| happy | 開心 |
| confused | 困惑／資訊不足 |

情緒代表 **MEMO 的顯示表情**，不代表使用者心理狀態或環境警報等級。目前模板在有近期數值時使用 friendly，缺有效資料時使用 confused，提供 active / recovering 事件時使用 concerned。

`/respond` 另外包含追蹤與來源欄位，以下為無環境資料的完整回應範例：

```json
{
  "text": "我是 MEMO，我陪你看看環境資料。目前沒有可用的近期環境讀值，無法判斷目前環境狀態。此為模板預覽，尚未啟用模型問答，也未執行任何裝置或事件操作。",
  "emotion": { "label": "confused", "intensity": 0.3 },
  "request_id": "4e8248dc-802b-4be9-a037-f5b69565ac91",
  "character": "MEMO",
  "source": "template",
  "fallback_reason": "model_not_configured",
  "context_source": "caller_supplied",
  "event_id": null,
  "event_version": null,
  "warnings": ["missing_observation"]
}
```

事件 ID 與版本由伺服器從請求附回，未交由文字生成器改寫。模板不理解任意問句、不執行確認／延後等動作，也不做環境異常判斷；history、user_message 與 approved_suggestions 會包含在 `/prepare` 的 user_prompt 中，目前模板不使用它們生成自由文字。

## 網頁裝置互動

`POST /api/v1/devices/{device_id}/interaction` 接受 `user_message`、`request_id` 與 `personality`。後端自行查詢該裝置最新讀值，沿用資料品質與時效檢查，回傳 `text`、`emotion`、`warnings`，以及 `context_source: stored_readings`。不接受瀏覽器提交環境 context；不存在的裝置回傳 404。

獨立 3D 網頁的啟動及 CORS 設定見 [memo_demo](../memo_demo/README.md)。裝置互動已支援模型、持久化對話與獨立任務記憶，見 [執行說明](companion-runtime.md)。

## 模型與事件串接

`infrastructure/model_reply.py` 實作 Chat Completions 相容呼叫、逾時與輸出驗證；`main.py` 注入 `ReplyGenerator`。API 回傳 `source: model | template` 和 `fallback_reason`，設定與完整行為見 [執行說明](companion-runtime.md)。

`/prepare` 保留 system/user 資料分離與輸出 schema。裝置互動從後端讀取環境與記憶；主動通知以任務形式生成，投遞前重查事件版本與時效。無狀態 `/llm/respond` 的呼叫端事件仍不視為後端已核對事件。

修改 schema 後重新匯出：

```powershell
uv run python scripts/export_openapi.py
uv run pytest -p no:cacheprovider
```

測試會檢查匯出規格與執行時 OpenAPI 一致。

官方參考：[FastAPI OpenAPI](https://fastapi.tiangolo.com/how-to/extending-openapi/)、[Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)。
