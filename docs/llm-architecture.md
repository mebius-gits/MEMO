# MEMO LLM 架構

從感測資料進入系統，到 MEMO 開口說話的完整路徑：Node-RED／MQTT 輸入、規則引擎、關注策略、排程器、提示組裝、模型呼叫、驗證、投遞到網頁泡泡與語音。

**核心原則**：規則引擎決定「該不該說、多嚴重」，模型只決定「怎麼說」。模型慢或失敗時，提醒照樣產生，改用模板文字。

## 1. 全貌

```mermaid
flowchart LR
    subgraph 輸入
        NR["Node-RED<br/>隨機漂移＋手動干擾"] -->|MQTT QoS1<br/>memo/v1/{id}/telemetry| BR["Mosquitto"]
        SIM["Python 模擬器"] --> BR
        HTTP["POST /api/v1/telemetry"]
    end
    BR --> W["environment-worker"]
    W --> P["project_reading<br/>規則＋關注策略"]
    HTTP --> P
    P -->|同一交易| DB[("SQLite／PostgreSQL<br/>readings · environment_states<br/>task_memory · scheduled_jobs")]
    SCH["排程器（每秒）"] -->|到期 → 任務| DB
    DB --> D["派送器（dispatcher）"]
    D --> PR["提示組裝<br/>InteractionService.prepare"]
    PR --> M["OpenAI 相容模型<br/>／模板備援"]
    M --> D
    D -->|status=ready| DB
    WEB["網頁（每 5 秒輪詢）"] -->|GET notifications<br/>POST ack| DB
    WEB --> UI["BMO 泡泡＋表情"]
    WEB -->|POST /tts| SP["speech 服務<br/>Kokoro"]
    MIC["麥克風"] -->|POST /stt| SP
    SP -->|文字| CHAT["POST …/interaction"]
    CHAT --> PR
```

| 元件 | 位置 | 職責 |
| --- | --- | --- |
| Node-RED | `node-red/flows.json` | 每 5 秒送出隨機漂移的讀值，並提供 `/memo/disturbance` 讓網頁疊加干擾 |
| environment-worker | `src/memo/workers/environment.py` | 訂閱 MQTT、驗證封包、寫入資料庫（呼叫 `project_reading`） |
| 規則引擎 | `src/memo/domain/environment.py` | 純函式：超標、趨勢、持續時間、恢復、嚴重程度、重複提醒 |
| 體感溫度 | `src/memo/domain/comfort.py` | 美國氣象局熱指數，溫度與濕度合併判斷 |
| 關注策略 | `src/memo/domain/attention.py` | 勿擾時段、每小時發話上限；危險等級一律放行 |
| 事件投影 | `src/memo/infrastructure/environment_events.py` | 把讀值套用到所有規則，產生 `task_memory` 通知 |
| 排程 | `src/memo/domain/schedule.py`、`src/memo/infrastructure/schedules.py` | 解析中文任務、計算時間、到期轉成通知 |
| 派送器 | `src/memo/infrastructure/notifications.py` | 領取通知、組提示、呼叫模型、寫回回覆 |
| 對話 | `src/memo/application/companion.py` | 解析任務意圖、執行動作、呼叫模型、保存對話 |
| 提示組裝 | `src/memo/application/interaction.py` | system 規則＋user JSON 上下文 |
| 模型轉接 | `src/memo/infrastructure/model_reply.py` | OpenAI Chat Completions、相容開關、寬鬆解析、長度控制 |
| 模板備援 | `src/memo/infrastructure/template_reply.py` | 模型未設定或失敗時的固定文字 |
| 網頁 | `memo_demo/src/main.js` | 輪詢通知、泡泡排隊、朗讀、歡迎回來摘要 |
| 語音 | `speech/app.py` | SenseVoice STT、Kokoro TTS |

## 2. 輸入：從 Node-RED 到資料庫

1. Node-RED「MEMO MQTT 持續感測」分頁啟動後每 5 秒產生一筆讀值：溫度、濕度、CO₂ 各自在正常範圍內隨機漂移，再加上手動干擾偏移（網頁「環境干擾」或 Node-RED 按鈕），以 QoS 1 發布到 `memo/v1/room01/telemetry`。
2. environment-worker 驗證 JSON schema、主題與 `device_id` 是否一致，用 `message_id` 去重後寫入 `readings`。
3. 在**同一個資料庫交易**裡呼叫 `project_reading`：更新每條規則的狀態，需要時新增 `task_memory` 通知。讀值與通知不會只寫一半。

封包格式見 [MQTT Worker 說明](mqtt-worker.md)。觀測時間與接收時間相差超過 30 秒的資料（補送或來自未來）只存成歷史，不驅動即時提醒。

## 3. 規則引擎

### 3.1 規則

| 規則 | 指標 | 觸發 → 恢復 | 持續 | 嚴重程度（注意／警戒／危險） |
| --- | --- | --- | --- | --- |
| `temperature_high` | **體感溫度** | > 30 → < 28 | 60 s／120 s | 30／35／41 °C |
| `temperature_low` | 溫度 | < 18 → > 20 | 120 s／120 s | 18／15／12 °C |
| `humidity_high` | 濕度 | > 70 → < 65 | 180 s／180 s | 70／80 % |
| `humidity_low` | 濕度 | < 35 → > 40 | 180 s／180 s | 35／25 % |
| `co2_high` | CO₂ | > 1200 → < 1000 | 120 s／120 s | 1200／1500／2000 ppm |
| `co2_rising` | CO₂ 60 秒增幅 | ≥ 300 → < 100 | 10 s／30 s | 趨勢，無分級 |
| `temperature_rising` | 溫度 300 秒增幅 | ≥ 2 → < 1 | 10 s／120 s | 趨勢，無分級 |

體感溫度用美國氣象局熱指數計算，而且不低於實際氣溫。34 °C、濕度 60 % 的體感約 42 °C，會直接判為「危險」；Node-RED 正常範圍的上限（27.5 °C、68 %）體感約 29.6 °C，不會觸發。

### 3.2 生命週期與通知

```mermaid
stateDiagram-v2
    [*] --> normal
    normal --> pending: 超過觸發門檻
    pending --> normal: 持續時間內回落
    pending --> active: 持續達 hold → 通知 active
    active --> active: 等級升高 → 通知 escalated
    active --> active: 到達重複間隔 → 通知 reminder
    active --> recovering: 低於恢復門檻
    recovering --> active: 再次超標
    recovering --> resolved: 持續達 recover_hold → 通知 resolved
    resolved --> pending: 再次超標
    active --> unknown: 資料中斷
```

| 通知 | 時機 | MEMO 被要求做什麼 |
| --- | --- | --- |
| `active` | 首次確認超標 | 主動提醒剛觀察到的狀況 |
| `escalated` | 嚴重程度升高（距上次通知至少 120 秒，避免在門檻附近來回） | 更明確地提醒，可提一個關鍵數字 |
| `reminder` | 狀況持續、等級不變，到達重複間隔：注意 30 分、警戒 15 分、危險 5 分 | 換個說法再提醒，語氣隨等級加重 |
| `resolved` | 恢復並穩定 | 一句話告知已恢復 |

等級下降時不通知，只記下目前等級，之後再升高時會重新觸發 `escalated`。趨勢規則只通知 `active`／`resolved`，不重複提醒。

狀態以 JSON 存在 `environment_states`（每個裝置 × 規則一筆）：`status`、`level`、`active_since`、`notified_at`、`notified_level`、`reminders`、`snoozed_until`、`event_id`、`version`。每次通知 `version` 加一，同一規則還沒送出的舊通知會被取消。

### 3.3 關注策略

`project_reading` 產生通知後，再由 `AttentionPolicy.allows()` 決定要不要真的說出來：

1. 非恢復類的「危險」等級一律放行。
2. 勿擾時段（`QUIET_HOURS`，例如 `23:00-07:00`，台北時間）內不說。
3. 同一裝置過去一小時的環境通知達到 `MAX_PROACTIVE_PER_HOUR`（預設 6）時不說。

被擋下時，規則狀態一樣會前進（`notified_at` 照樣更新），所以不會每 5 秒重試一次；之後的提醒照樣依重複間隔進行。

### 3.4 延後（snooze）

說「知道了」「30 分鐘後再提醒我」會把目前所有進行中事件的 `snoozed_until` 往後設。延後期間不發 `reminder`，但 `escalated` 照樣會發，因為等級升高是新資訊，不是重複提醒。

## 4. 排程任務

### 4.1 種類

| 種類 | 例句 | 行為 |
| --- | --- | --- |
| 單次 `once` | 「10 分鐘後提醒我喝水」「提醒我十五分鐘後關火」 | 到期提醒一次，之後結束 |
| 定期 `interval` | 「每小時提醒我起來走走」「每 30 分鐘提醒我喝水」 | 依間隔重複；錯過的次數直接跳過，不會一次補發 |
| 每日 `daily` | 「每天 9 點報環境摘要」「每天下午 3 點半提醒我吃藥」 | 每天台北時間的固定時刻；提到「摘要、環境、狀況」時改為環境摘要 |
| 追蹤 `followup` | 「我開窗了」「我剛剛打開冷氣，10 分鐘後跟我說」 | 建立時記下當下讀值，預設 5 分鐘後比較前後變化並回報 |

管理用語：「取消喝水的提醒」「不用再提醒我喝水了」「取消所有提醒」「暫停提醒」「恢復提醒」。

### 4.2 動作的兩個來源

```mermaid
flowchart TD
    U["使用者訊息"] --> I{"中文句型解析<br/>parse_intent"}
    I -->|命中| A["先執行動作"] --> C["context.actions_done"] --> M["模型回覆並確認"]
    I -->|未命中| M2["模型回覆<br/>可附 action 欄位"] --> V{"validate_action"}
    V -->|合法| E["執行動作"]
    V -->|不合法| X["改用固定說明：<br/>這個提醒我沒設定成功"]
```

- **句型解析**（`domain/schedule.py`）：命中就先執行，不必依賴模型，結果穩定；支援國字數字（十五、兩、半小時）與上午／下午／中午。
- **模型 action**：沒命中時，模型可以在 JSON 裡附上 `action`，例如 `{"type":"schedule","kind":"once","minutes":20,"message":"收衣服"}`。後端用白名單驗證種類和範圍：單次 1–1440 分鐘、定期 5–1440 分鐘、追蹤 1–120 分鐘、延後 5–240 分鐘、時間為 HH:MM。不合法就不執行，並把回覆改成固定說明，避免模型文字說「已設定」卻沒有真的設定。
- **重試**：同一個 `request_id` 重送時，直接回傳已存的回覆，動作不會執行第二次。

### 4.3 排程器

API 程序內有一個背景執行緒（`run_scheduler`），每秒檢查一次：

1. 找出 `status=active` 且 `next_run_at <= 現在` 的工作。
2. 用條件式更新領取（`WHERE id=? AND next_run_at=舊值`），同時寫入下一次時間與執行次數。即使開了多個 API 程序，每次也只會觸發一次。
3. 新增一筆 `task_memory`：`rule_id="schedule"`、`event_id=<工作 id>`、`event_status=remind|summary|followup`。

之後交給既有的派送器處理，泡泡、朗讀、表情都直接沿用。

## 5. 派送器與提示組裝

### 5.1 派送

`run_dispatcher` 迴圈：把過期的通知標成 `expired` → 領取最早的 `queued`（附租約，程序中斷後可由別的程序接手）→ 確認通知仍有效 → 組提示 → 呼叫模型 → 再確認一次仍有效 → 寫入 `ready` 與回覆。

是否「仍有效」（`current_task`）的判斷：
- **環境通知**：事件 ID 與 version 都沒變、狀態仍符合通知種類、最近 30 秒內有新讀值，而且還沒超過 10 分鐘有效期限。
- **排程通知**：工作沒有被取消，而且還沒超過有效期限。

主動通知使用裝置**最後一次對話用的人格**（存在 `device_settings`），所以主動提醒和對話的口吻一致。

### 5.2 提示結構

**system（固定規則）**
1. 身分、年齡與人格：MEMO 明確知道自己是 MEMO（被問就答 MEMO，不提代理注入的其他身分或開發單位）；個性設定為十五、六歲的少年，平常直率有活力，講到數據時切換成冷靜精準的小專家，形成「專業但還是少年」的反差；使用者選的人格疊加在這個設定上，作為說話風格。
2. 事實邊界：只引用 `usable_metrics` 與事件依據；缺值不當成零；建議只能從 `approved_suggestions` 挑。
3. 動作邊界：只有 `actions_done` 列出的才能說「已完成」；非主動訊息可附 `action` 欄位，並說明格式。
4. 關注語氣：依 `attention` 的等級調整（注意輕聲、警戒明確、危險直接）；重複提醒要換說法。
5. 口語長度：回覆會被念出來，一次一到兩句、約 15–50 字；不用條列、表情符號或 Markdown；不輸出欄位名稱。
6. 輸出：JSON `{"text":…,"emotion":{"label":…,"intensity":…}}`，可附 `action`。

**user（JSON 資料，明確標示「僅為資料」）**

| 欄位 | 內容 |
| --- | --- |
| `user_message` | 使用者的話；主動通知時是後端給的任務說明（見 5.3） |
| `usable_metrics`／`observed_at` | 30 秒內、品質正常的讀值 |
| `event` | 規則引擎核對過的事件 |
| `attention` | `level_label`、`minutes_active`、`reminders`、`value`、`snoozed` |
| `approved_suggestions` | 依規則與等級核准的建議，例如危險高溫：補充水分、開電扇或冷氣、暫停費力活動 |
| `schedule` | 到期的工作；追蹤類另附 `baseline`、`current`、`change` |
| `jobs` | 目前有效的排程（中文描述），用來回答「我有哪些提醒」 |
| `actions_done` | 這則訊息剛完成的動作 |
| `missed` | 使用者離開期間錯過的通知（歡迎回來摘要用） |
| `history`／`task_memory` | 這個 session 的對話記憶與後端任務狀態，兩者分開 |

### 5.3 每種主動訊息給模型的任務說明

| 種類 | user_message |
| --- | --- |
| `active` | 用你的人格主動提醒剛觀察到的狀況，一到兩句 |
| `escalated` | 狀況變得更嚴重，已到「{等級}」程度，比上次更明確地提醒，可提一個關鍵數字並挑一個建議 |
| `reminder` | 已持續約 {N} 分鐘，第 {k} 次提醒，換個說法，嚴重程度越高越緊迫 |
| `resolved` | 狀況已恢復，一句話告知 |
| `remind` | 到了指定的提醒時間，用人格提醒：{訊息} |
| `summary` | 定時環境摘要，一兩句，可引用關鍵數值 |
| `followup` | 根據前後數值回報「{使用者說的話}」之後有沒有改善；沒有資料就直說 |
| 歡迎回來 | 告訴使用者離開期間錯過的事，再說現在的狀況 |

## 6. 模型呼叫

`ModelReplyGenerator` 呼叫 OpenAI Chat Completions（`POST {OPENAI_BASE_URL}/chat/completions`）。

| 設定 | 用途 |
| --- | --- |
| `OPENAI_API_KEY`／`OPENAI_BASE_URL`／`OPENAI_MODEL` | 連線；沒有設定 model 就一律用模板 |
| `OPENAI_TIMEOUT_SECONDS` | 逾時就改用模板，建議 15 |
| `OPENAI_REASONING_EFFORT` | 推理模型的思考量；Gemma 4 設 `none`、gpt-oss 設 `low` |
| `OPENAI_MAX_COMPLETION_TOKENS` | 輸出 token 上限 |
| `OPENAI_SYSTEM_ROLE` | 設 `false` 時，指令改放進 user 訊息（給會忽略 system 角色的代理或模型用） |
| `OPENAI_JSON_MODE` | 設 `false` 時不送 `response_format`（給在 JSON 模式下回 `{}` 的模型用） |

目前的 BIRC-LLM（Gemma 4）實測要用：`OPENAI_REASONING_EFFORT=none`、`OPENAI_SYSTEM_ROLE=false`、`OPENAI_JSON_MODE=false`，回覆約 0.9 秒。

**回覆處理**
1. 寬鬆解析：容忍程式碼區塊標記、前後說明文字、多餘欄位、字串型的 intensity、不認得的表情（改為 neutral）；只有完全沒有可用文字時才判為無效。
2. 長度保護：只保留前面完整的句子，最多 80 字。
3. 繁體保護：模型偶爾混入簡體字。只轉換「僅簡體才有」的字（例如 记→記），兩岸共用的字（干、台、里）保持原樣；但若相鄰是簡體字（表示整個詞是簡體），就跟著整詞轉換（这里→這裡、干净→乾淨），所以正確的「干擾」「台灣」不會被改錯。
4. 表情：模型可選 neutral、friendly、happy、concerned、confused、surprised、proud（解說數據的專業時刻）、sleepy、alert。主動事件通知的表情由事件決定：恢復為 happy，危險等級為 alert，其他為 concerned。網頁另有 thinking（等待回覆）、listening（錄音中）、error（請求失敗）三個狀態。螢幕只會顯示臉或符號其中一種，不混用：confused 整面顯示「?」、thinking 為轉圈的 loading、listening 為跳動的音量條、alert 為暖紅底大紅「!」並輕微顫動、error 為「ERROR!」；其餘為只有表情的臉。
5. 失敗時改用模板，並在回覆標記 `fallback_reason`（`model_timeout`／`model_unavailable`／`invalid_model_output`），不會把模板偽裝成模型回覆。

## 7. 輸出與投遞

1. 網頁每 5 秒呼叫 `GET /notifications`，拿到 `ready` 的通知後放進泡泡佇列（每則至少顯示 6 秒；朗讀中或使用者正在對話時會等），然後 `POST /notifications/ack`。
2. 泡泡出現在 BMO 頭部右上方，跟著 3D 視角移動；同時更新 BMO 的表情。
3. 朗讀：逐句呼叫 `speech` 服務的 `/tts`（Kokoro，語速 1.3，約每秒 5 字），播放時 BMO 的嘴巴會動。
4. **歡迎回來**：網頁記下最後在場時間；隔超過 2 分鐘再打開時，呼叫 `POST /digest`，用一兩句說明錯過了什麼。只統計已經過期或被取代、無法再送達的通知，所以不會和網頁剛拿到的通知重複。
5. 語音輸入：按麥克風開始錄音；瀏覽器先用 0.3 秒量測環境噪音，偵測到說話後停頓約 0.9 秒就自動結束並送出（也可以再按一次，最長 15 秒；6 秒內都沒聲音就取消）。錄音轉成 16 kHz WAV → `/stt`（SenseVoice，約 0.1 秒）→ 當成一般訊息送進對話流程。speech 服務的辨識與合成各自排隊，MEMO 念長句時也不會拖慢辨識。

## 8. 時序

### 8.1 持續高溫

```mermaid
sequenceDiagram
    participant NR as Node-RED
    participant W as Worker
    participant DB as DB
    participant D as Dispatcher
    participant LLM as 模型
    participant Web as 網頁
    NR->>W: 每 5 秒讀值（體感 36 °C）
    W->>DB: 持續 60 秒 → active（警戒）＋通知
    D->>DB: 領取通知
    D->>LLM: attention{警戒, 0 分鐘}
    LLM-->>D: 「這裡悶熱起來了，記得喝水」
    D->>DB: ready
    Web->>DB: 輪詢 → 泡泡＋朗讀 → ack
    Note over W,DB: 15 分鐘後仍是警戒
    W->>DB: reminder（第 1 次）＋通知
    Note over W,DB: 體感升到 42 °C
    W->>DB: escalated（危險）＋通知，之後每 5 分鐘提醒
```

### 8.2 單次提醒

```mermaid
sequenceDiagram
    participant Web as 網頁
    participant API as 對話 API
    participant DB as DB
    participant S as 排程器
    participant D as Dispatcher
    Web->>API: 「10 分鐘後提醒我喝水」
    API->>DB: parse_intent → 建立 once 工作
    API-->>Web: 「好，10 分鐘後叫你喝水」actions=[已設定…]
    S->>DB: 10 分鐘後：領取並建立 schedule 通知
    D->>DB: 生成「該喝水囉！」→ ready
    Web->>DB: 輪詢 → 泡泡＋朗讀
```

## 9. 資料表

| 資料表 | 主鍵 | 內容 |
| --- | --- | --- |
| `readings` | message_id | 原始讀值與品質 |
| `environment_states` | device_id + rule_id | 規則狀態 JSON（見 3.2） |
| `task_memory` | id | 每則主動訊息：規則或排程、種類、狀態、有效期限、租約、回覆 |
| `scheduled_jobs` | id | 種類、目的、訊息、間隔或時刻、追蹤基準值、狀態、下次時間、執行次數 |
| `device_settings` | device_id | 最後使用的人格 |
| `conversation_memory` | id | 依 session 保存的對話（request_id 用來防止重送） |

通知狀態：`queued → generating → ready → acknowledged`；被取代或失效時為 `cancelled`，過期為 `expired`。
排程狀態：`active ⇄ paused → done | cancelled`。

## 10. API

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| POST | `/api/v1/devices/{id}/interaction` | 對話；回應含 `actions` |
| GET | `/api/v1/devices/{id}/notifications` | 待送出的主動訊息 |
| POST | `/api/v1/devices/{id}/notifications/ack` | 確認收到 |
| GET | `/api/v1/devices/{id}/events` | 規則狀態，含 level、reminders、snoozed_until |
| GET／POST | `/api/v1/devices/{id}/schedules` | 列出或建立排程（給 Node-RED 等整合用） |
| PATCH／DELETE | `/api/v1/devices/{id}/schedules/{job}` | 暫停或恢復；取消 |
| POST | `/api/v1/devices/{id}/digest` | `{since}` → 錯過的摘要；沒錯過時 `text` 為 null |

完整規格見 [openapi.yaml](openapi.yaml)。

## 11. 設定一覽

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `TIMEZONE` | Asia/Taipei | 每日排程與勿擾時段使用的時區 |
| `QUIET_HOURS` | （空） | 例如 `23:00-07:00`；只有危險等級會說 |
| `MAX_PROACTIVE_PER_HOUR` | 6 | 每個裝置每小時的環境通知上限（危險不計） |
| `NOTIFICATIONS_ENABLED` | true | 是否啟動派送器與排程器 |
| `OPENAI_*` | — | 見第 6 節 |

Compose 會把前三項同時傳給 api 和 environment-worker：規則在 worker 裡執行，兩邊的判斷必須一致。

## 12. 失敗與邊界

| 情況 | 行為 |
| --- | --- |
| 模型逾時、錯誤或格式無效 | 改用模板；標記 `fallback_reason` |
| 模型自稱設定了提醒，但動作不合法 | 不執行，回覆改為固定說明 |
| 生成期間事件已恢復 | 丟棄過期的說法，改說「狀態剛剛有變化」 |
| 網頁沒開 | 通知 10 分鐘後過期；下次打開時用 digest 補說 |
| 感測資料中斷 | 事件轉為 unknown；持續時間不跨越缺口；不當成已恢復 |
| 程序重啟 | 通知、排程、規則狀態都存在資料庫；租約到期後由別的程序接手 |
| 多個 API 程序 | 排程用條件式更新，派送用租約，同一筆不會處理兩次 |

## 13. 測試

- `tests/test_attention_schedule.py`：體感溫度、嚴重程度與升級、重複間隔、延後、勿擾與上限、句型解析、時間計算，以及「對話 → 排程 → 到期 → 派送 → 通知 → digest」整條流程。
- `tests/test_environment_events.py`：規則的持續時間、遲滯、資料缺口、交易一致性、生成期間事件變化。
- `tests/test_model_reply.py`：OpenAI 參數、相容開關、寬鬆解析、長度控制。
- `tests/test_node_red.py`：Node-RED 流程的漂移、干擾與網頁介面。
- `memo_demo/tests/browser.mjs`：網頁、Node-RED 干擾、人格、語音輸入輸出。

## 14. 尚未涵蓋

- 人體感測：目前不知道有沒有人在，只能靠勿擾時段與發話上限。
- 實體裝置輸出：MQTT `commands`／`acks` 還沒做，提醒只送到網頁。
- PM2.5：接上之前，不產生依賴室外空氣品質的開窗建議。
