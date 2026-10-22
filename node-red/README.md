# MEMO Node-RED

在專案根目錄執行（需要 Docker Desktop，使用 Linux containers）：

```powershell
docker compose up -d --build
docker compose ps
```

- Node-RED：http://localhost:1880
- MEMO Swagger：http://localhost:8000/docs

若已有本機 MEMO API 使用 8000，請先停止該程序再啟動 Compose。

API 容器會自動執行 Alembic migration，通過健康檢查後才啟動 Node-RED。首次建立 volume 時，`flows.json` 已包含在 Node-RED 映像中，會自動載入，不需 Import 或額外安裝節點。

## 使用節點

1. 打開 Node-RED，選擇「MEMO 環境感測範例」，展開右側 Debug。
2. 按「送出一筆模擬資料」左側按鈕。
3. 流程自動註冊 `room01`；裝置已存在時繼續執行。
4. 寫入當下 UTC 時間的模擬數值：29.2°C、65%、1100 ppm。
5. 自動查詢最近 10 筆，在 Debug 顯示結果。

也能單獨按「查詢最近 10 筆」或「啟動／手動檢查 API」。尚未建立裝置時直接查詢會回 404，顯示在錯誤 Debug。HTTP 非預期狀態與節點連線錯誤皆接到錯誤輸出；錯誤時不繼續寫入。

啟動只自動檢查 API，不會自動寫入模擬讀值。每次點按產生不同 message_id；sequence 目前固定為 1，這是 HTTP 示範流程。所有節點皆使用 Node-RED 內建功能。

## 設定與保存

「MEMO MQTT 持續感測」分頁可按開始／停止控制每 5 秒的傳送。資料經 MQTT broker → environment-worker 寫入，再到原本的環境感測分頁查詢歷史。MQTT 主機使用 `MEMO_MQTT_HOST=broker`，QoS 1、retain=false。詳見 [MQTT 說明](../docs/mqtt-worker.md)。

「MEMO 文字與情緒」分頁提供 LLM 契約測試。按「測試 MEMO 文字＋情緒」後會送出人格與模擬上下文，分別顯示文字、情緒強度與完整回應。目前來源為 template，不呼叫外部模型，也不將這筆模擬數據寫入資料庫。可在「設定人格與模擬上下文」節點更改 tone 為 gentle / lively / concise。詳細欄位見 [LLM 介面說明](../docs/llm-api.md)。

已有 Node-RED volume 的使用者需依下方說明 Import 新流程；重新 build 不會覆寫已保存的編輯內容。

`compose.yml` 的 Node-RED environment 可設定：

| 變數 | 預設 | 用途 |
| --- | --- | --- |
| MEMO_API_URL | http://api:8000 | Compose 網路內的 API URL |
| MEMO_DEVICE_ID | room01 | 示範裝置 ID |
| TZ | Asia/Taipei | Node-RED 時區 |

裝置 ID 限英文字母、數字、底線或連字號，長度 1–64。

- `memo-data`：API SQLite `/data/memo.db`，與本機 `buddy.db` 分開。
- `node-red-data`：Node-RED 流程、設定與編輯內容。
- `docker compose down` 保留資料，重新啟動會沿用。
- 編輯器修改後按 Deploy 即會保存。既有 volume 不會被重新 build 的範例覆蓋；要更新既有流程，請在編輯器刪除舊範例 tab，再 Import 本目錄的 `flows.json` 並 Deploy。

查看啟動錯誤：

```powershell
docker compose logs --tail 100 api node-red
```

對外連接埠只綁定主機的 127.0.0.1，供本機開發使用。目前同時提供 HTTP 與 MQTT 感測流程；規則判斷、真實 LLM 模型呼叫與正式環境登入機制尚未實作。

官方參考：[Node-RED Docker](https://nodered.org/docs/getting-started/docker)、[流程 JSON 匯入與匯出](https://nodered.org/docs/user-guide/editor/workspace/import-export)。
