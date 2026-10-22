# MQTT 與環境 Worker

## 架構

```text
Node-RED／Python 模擬器／感測器
  → buddy/v1/{device_id}/telemetry（QoS 1、retain=false）
  → Mosquitto broker
  → 獨立 environment-worker
  → 有上限的接收佇列
  → 共用 TelemetryInput 驗證 + EnvironmentService
  → SQLAlchemy／SQLite
  → HTTP API 查詢
```

API 不啟動 MQTT 訂閱。Worker 使用固定 client_id、MQTT 3.1.1 persistent session，以及 Paho manual_ack；提交資料後才確認訊息。HTTP 與 MQTT 共用欄位驗證、時間處理與 message_id 去重。

## Docker 啟動與持續接收

需要可用的 Docker Engine 與 Compose v2。Docker Desktop 使用 Linux containers。

```powershell
# API、Node-RED、Mosquitto、environment-worker
docker compose up -d --build --wait

# 加入每 5 秒送一筆資料的 Python 模擬器
docker compose --profile demo up -d --build --wait

docker compose logs --tail 100 environment-worker simulator
Invoke-RestMethod http://localhost:8000/api/v1/devices/room01/readings
```

模擬器會先註冊 room01；每次啟動產生新 boot UUID，再搭配 sequence 組成唯一 message_id。模擬器的 publish 成功僅代表 broker 收到，不代表 Worker 已落庫，應透過 API 查詢確認。

停止模擬器，其他服務繼續接收：

```powershell
docker compose stop simulator
```

Node-RED 也提供「MEMO MQTT 持續感測」分頁，按開始後每 5 秒發布，按停止結束。該流程會先透過 HTTP 確認裝置已註冊，再走 MQTT 寫入。預設不自動開始，以免混入模擬資料。已有 Node-RED volume 時，需要匯入新版 flows.json 才會出現新分頁。

## 資料契約與處理語意

- MQTT topic 沿用規劃文件的 `buddy/v1/{device_id}/telemetry`；角色名稱為 MEMO，不改動 wire protocol。
- payload 使用原有 HTTP telemetry JSON 格式；topic 的裝置 ID 必須與 payload 相同。
- 裝置必須先透過 POST /api/v1/devices 註冊。未知裝置訊息會被拒絕。
- 需要 QoS 1。訂閱時重播的 retained 資料會被拒絕，發布者應使用 retain=false。MQTT 3.1.1 無法僅憑即時訂閱訊息判斷發布者原先是否設了 retain。
- 有效資料落庫後 ACK；相同 message_id 與相同內容重送，沿用原紀錄。
- 格式錯誤、topic 不符、未知裝置與 ID 內容衝突會記錄拒絕日誌並 ACK，避免無限重送。目前尚無拒收資料持久化／dead-letter queue。
- 資料庫暫時失敗不 ACK，保留當前訊息並退避重試；其他未預期錯誤會使 Worker 結束，Compose 再啟動。
- 記憶體佇列預設 256 筆；滿載時退出重啟，不 ACK 尚未處理的訊息，交由 broker 的 persistent session 補送。
- broker 容量有限，持久化也有寫盤間隔；這不是端到端 exactly-once，也不保證任意長離線或 broker 突然斷電都不遺失資料。
- 正常停止 Worker 後 session 仍保留。首次訂閱前發生的非 retained 訊息不會補收。
- Worker 目前只接收、驗證與保存歷史。舊資料可以入庫，尚未維護最新狀態、資料過期事件、規則窗口或通知。

目前 Compose 是單一 Worker，固定 client_id；不要直接 scale 多份相同 client_id。SQLite 設定等待寫入鎖，適合此雛形；較高吞吐應改 PostgreSQL 並驗證。

## 設定

| 變數 | 預設 |
| --- | --- |
| BUDDY_DATABASE_URL | sqlite:///./buddy.db；Compose 使用 /data/memo.db |
| MEMO_MQTT_HOST | localhost；Compose 使用 broker |
| MEMO_MQTT_PORT | 1883 |
| MEMO_MQTT_CLIENT_ID | memo-environment-worker |
| MEMO_MQTT_QUEUE_SIZE | 256 |
| MEMO_MQTT_MAX_PAYLOAD_BYTES | 16384 |
| MEMO_MQTT_USERNAME / PASSWORD | 未設定 |
| MEMO_MQTT_TLS | false |

本機手動啟動 Worker：`uv run python -m memo.workers.environment`。先執行 migration 並啟動 broker。

Compose 的 broker 僅將主機 127.0.0.1:1883 對外綁定，目前使用匿名開發設定；正式設備接入需另建帳號、ACL 與 TLS。mqtt-data volume 保存 broker session 資料；`docker compose down` 保留，`down -v` 會刪除 volumes。

## 驗證

2026-10-22 驗證結果：42 個 pytest 測試通過；Ruff 與 Alembic schema 檢查通過；Compose YAML 通過官方 JSON Schema 靜態檢查；Aedes 真實 MQTT 傳輸、去重與離線補收測試通過。後續已在 WSL Ubuntu 安裝 Docker Engine，實際 build/start、四個服務健康檢查、Node-RED 預載流程、Mosquitto 訊息落庫及 Worker 離線補收均通過 `verify_docker.py`。Windows 的 localhost:8000 與 localhost:1880 也確認可存取。操作方式見 [WSL Docker](docker-wsl.md)。

完整 Docker smoke test：

```powershell
uv run python scripts/verify_docker.py
```

依序驗證 Compose 設定、build/start、API readiness、Node-RED 預載流程、發布 3 筆資料並查詢、停止 Worker 再發布、重啟後補收。會保留服務及唯一命名的 smoke 測試裝置／資料。Docker 不可用時明確失敗，不會假裝已驗證。

不依賴 Docker 的真實 MQTT 傳輸測試：

```powershell
npm --prefix scripts/mqtt-broker ci
uv run python scripts/smoke_mqtt_local.py
```

啟動本機暫時 Aedes broker 與獨立 Worker 執行緒，使用臨時 SQLite，驗證 QoS 1、落庫、重送去重及 Worker 離線補收。此測試不驗證 Mosquitto 或容器，測試後停止 broker 並刪除臨時資料。

一般測試：`uv run pytest -p no:cacheprovider`。

參考：[Paho manual_ack 與 session](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)、[Mosquitto 設定](https://mosquitto.org/man/mosquitto-conf-5.html)。
