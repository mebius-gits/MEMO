# MEMO 模型、記憶與主動回應

## 啟動

更新前先停止舊 API／Worker，執行 `uv run alembic upgrade head`，再啟動服務。新增 migration `0002` 保存環境狀態、任務記憶、對話記憶；原本讀值不會刪除。Docker API 的啟動指令會執行 migration。

```dotenv
OPENAI_API_KEY=
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=你的模型名稱
OPENAI_TIMEOUT_SECONDS=30
OPENAI_REASONING_EFFORT=low         # 選填，推理模型降低思考量以加快回覆（Gemma 4 可用 none 關閉思考）
OPENAI_MAX_COMPLETION_TOKENS=400    # 選填，限制單次輸出 token
OPENAI_SYSTEM_ROLE=true             # 模型不理會 system 角色時設 false，指令改放 user 訊息
OPENAI_JSON_MODE=true               # JSON 模式回空物件時設 false，改由提示詞要求 JSON
NOTIFICATIONS_ENABLED=true
```

變數名稱沿用 OpenAI SDK 慣例。`OPENAI_BASE_URL` 未設定時預設為 `https://api.openai.com/v1`。模型必須支援 Chat Completions 相容介面：後端 POST `{OPENAI_BASE_URL}/chat/completions`，使用 `messages`、`response_format: {type: json_object}`、`stream: false`。參考 [Ollama 官方相容介面](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx)。本機 Ollama 可不填 key；其他服務依其要求設定。Docker 的 localhost 是容器本身，請填容器可連到的模型網址。

`OPENAI_MODEL` 未設定就使用模板；網路錯誤、逾時、無效 JSON 或不合法情緒也會改用備援。每個回覆都有 `source` 和 `fallback_reason`，不會把模板偽裝成模型回覆。輸出 JSON 驗證不代表模型內容必然正確；事件判斷由規則引擎負責。API key 只保存在後端設定。

## 記憶分離

- `conversation_memory`：以 device_id + session_id 隔離，保存每次 user_message 和回覆；資料持續保存，直到清除該 session。request_id 重試回傳相同結果，不再增加回合。相同 ID 配不同訊息回傳 409。超過 60 分鐘沒說話就算新的一次對話，舊的對話會壓縮成摘要，另有長期的「關於你的事」，見 [LLM 架構](llm-architecture.md) 5.4 節。
- `task_memory`：保存事件 ID／版本、規則、觸發或恢復、通知期限、生成及確認狀態，不混入對話紀錄。模型得到最近 10 筆任務摘要。對話說「處理好了」不會自行改動任務或解除事件。
- 網頁會在瀏覽器保存 session_id，重新開啟後載入同一裝置的對話。切換裝置時使用不同記憶範圍。session_id 是分區識別，不是登入或權限機制；本專案目前仍沒有使用者驗證。

## 規則與狀態

預設規則位於 `src/memo/domain/environment.py` 的 `RULES`；門檻可用 `PUT /api/v1/rules/{id}` 修改（見 [LLM 架構](llm-architecture.md) 3.6 節），API 與 Worker 下一筆讀值就生效，不需重啟。濕度依一般室內舒適範圍（約 40–60%）設定上下限；其餘為展示參數，皆不是安全標準。

| 規則 | 觸發 | 恢復 | 嚴重程度（注意／警戒／危險） |
|---|---|---|---|
| CO₂ 偏高 | >1200 持續 120 秒 | <1000 持續 120 秒 | 1200／1500／2000 ppm |
| 溫度偏高（體感溫度） | >30 持續 60 秒 | <28 持續 120 秒 | 30／35／41 °C |
| 溫度偏低 | <18 持續 120 秒 | >20 持續 120 秒 | 18／15／12 °C |
| 濕度偏高 | >70 持續 180 秒 | <65 持續 180 秒 | 70／80 % |
| 濕度偏低（空氣乾燥） | <35 持續 180 秒 | >40 持續 180 秒 | 35／25 % |
| CO₂ 快速上升 | 60 秒窗口增幅 ≥300，持續 10 秒 | 增幅 <100，持續 30 秒 | — |
| 快速升溫 | 300 秒窗口增幅 ≥2，持續 10 秒 | 增幅 <1，持續 120 秒 | — |

溫度偏高使用體感溫度（溫度與濕度合併計算的熱指數）：34 °C、濕度 60 % 約等於 42 °C，直接判為危險。

狀態：normal → pending → active → recovering → resolved。資料不足或過期為 unknown；unknown 不算恢復。待確認期間條件消失會重新計時。每個 device + rule 保存一份狀態。active 期間等級升高會立即提醒（escalated），等級不變則依間隔重複提醒（reminder）：注意 30 分、警戒 15 分、危險 5 分；說「知道了」可延後重複提醒，但等級升高仍會提醒。`QUIET_HOURS` 勿擾時段內與每小時上限（`MAX_PROACTIVE_PER_HOUR`）只放行危險等級。排程任務、追蹤回報與完整流程見 [LLM 架構](llm-architecture.md)。

時間以 observed_at 為準；超過 30 秒的補送與未來資料只存歷史、不驅動提醒。最大採樣間隔 15 秒，無效／缺值會中斷該指標的持續時間。趨勢窗口至少三筆資料且覆蓋 window − 15 秒；間隔過大清空窗口。趨勢用窗口兩端的平均值相減（CO₂ 各取 10 秒、溫度各取 30 秒），單筆異常讀值不會造成假趨勢。

沒有新資料時也會判斷：API 背景每秒檢查一次，超過 30 秒沒有有效讀值，進行中的事件轉為 unknown（不算恢復），並開啟裝置層級的 `sensor_offline` 事件，MEMO 會說一句「收不到感測資料」；之後連續收到 3 筆有效讀值才算恢復。後端自己停機很久後才發現的中斷只記錄、不說出來。

## 投遞與人格

HTTP 與 MQTT 共用同一個交易：儲存讀值、更新事件狀態、寫入通知任務。API 背景 dispatcher 領取任務，再呼叫模型；MQTT 不等待模型。任務有 120 秒期限、可恢復的生成租約，生成前後均核對事件 ID／版本、狀態與資料時效。過期回覆會取消。

`queued → generating → ready → acknowledged`；狀態改變可 cancelled，超時為 expired。ack 僅表示網頁收到，不等於環境恢復。網頁每 5 秒抓通知，自動顯示文字與表情，收到後 ack。停止網頁不會停止後端判斷。

MEMO 平常簡短陪伴，不逐項報數字；明確詢問數據才引用讀值。active 使用 concerned，resolved 使用 happy。恢復文字會指出是哪個條件恢復，避免把「上升變慢」說成「CO₂ 已經不高」。目前主動提醒使用預設 gentle 人格；對話可逐次指定 gentle / lively / concise。

## API

| 方法 | 路徑（前綴 `/api/v1/devices/{device_id}`） | 用途 |
|---|---|---|
| POST | `/interaction` | `session_id`、`request_id`、`user_message`、`personality` |
| GET | `/memory/conversation?session_id=...` | 對話回合（預設 20、最多 100） |
| DELETE | `/memory/conversation?session_id=...` | 清除指定 session，不刪任務 |
| GET | `/memory/tasks` | 任務記憶及狀態 |
| GET | `/events` | 當前規則狀態 |
| GET | `/notifications` | 尚未確認且仍有效的提醒 |
| POST | `/notifications/ack` | body `{"task_id":"..."}` |

完整 response schema 見 `docs/openapi.yaml`。`/llm/prepare` 與 `/llm/respond` 保留為無狀態、由呼叫端提供 context 的整合端點；網頁使用 `/devices/{id}/interaction` 從資料庫取得事實。

## CO₂ 劇本

網頁點「開始 CO₂ 劇本」，每 5 秒寫入獨立 `memo-scene-*` 裝置，約六分鐘完成。前 20 秒平穩，20–60 秒逐步升高，60–210 秒維持偏高，再回穩。提醒及恢復由後端時間窗觸發，不由前端假造。

MQTT 可用：

```powershell
uv run python -m memo.workers.simulator --device-id memo-co2-scene --scenario co2-rise-recover --count 73
```

或先啟動 Compose，再執行 `docker compose run --rm simulator python -m memo.workers.simulator --api-url http://api:8000 --device-id memo-co2-scene --scenario co2-rise-recover --count 73`。

瀏覽器測試：`node tests/browser.mjs`（在 memo_demo 目錄）。CO₂ 情境請使用上方的 simulator 指令，恢復狀態以 `/events` 和任務結果為準。
