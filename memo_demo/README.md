# MEMO 3D Demo

獨立的 Vite + Three.js 網頁：一個坐著的 MEMO，支援呼吸、眨眼與情緒表情，以及真實環境資料、對話和模擬資料寫入。

## 本機啟動

```powershell
cd memo_demo
npm ci
Copy-Item .env.example .env.local
npm run dev
```

開啟 http://localhost:5173，右上角設定後端網址，例如 `http://localhost:8000`。網址只填 origin，不加 `/api/v1`。

`.env.local` 的 `VITE_API_URL` 提供預設後端網址。優先序：瀏覽器已儲存的設定（包含主動斷線）→ env 預設值 → 未連線。要套用新的預設值，可在瀏覽器清除 `memo.apiBase` 的 localStorage，或直接從設定視窗更改網址。

Vite 在開發啟動／建置時讀取 env；修改後須重新啟動或重新建置發布。正式環境可使用 `.env.production` 設定 HTTPS 後端網址。`VITE_*` 會公開在網頁程式中，勿放入模型 API key。

```powershell
npm run build
npm run preview
```

`dist/` 可部署至靜態網站服務。Sites 設定位於 `.openai/hosting.json`。

## 後端連線

已發布的 HTTPS 網站需要瀏覽器可連到的 HTTPS API。後端設定 `CORS_ORIGINS` JSON 陣列，允許網站的完整 origin：

```dotenv
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173","https://memo-quiet-room.fancyhen36.chatgpt.site"]
```

網站每 5 秒讀取裝置與環境資料，過期資料會標示狀態。尚未設定網址時不會顯示假讀值。網址只儲存在目前瀏覽器。

對話呼叫 `POST /api/v1/devices/{device_id}/interaction`，由後端查詢資料庫最新讀值，再輸出文字及情緒。後端設定模型後會實際呼叫；未設定或失敗時回傳明確標示的備援。

「模擬環境」按鈕會建立獨立裝置 `memo-web-demo` 並寫入真實後端；載入網頁不會自動寫入資料。

## 瀏覽器驗證

先啟動前端與 API，再執行 `npm run test:browser`。預設使用 Windows Chrome；其他環境設定 `CHROME_PATH`。可用 `TEST_API_URL` 指定測試 API（預設 localhost:8000）。測試會寫入 demo 裝置資料。

## 主動回應與記憶

網頁會為本瀏覽器保存 session_id，載入裝置的對話記憶；任務記憶獨立保存在後端。每 5 秒讀取通知，主動呈現 MEMO 的文字與情緒並確認收到。

「開始 CO₂ 劇本」會建立獨立裝置，約六分鐘持續送資料，驗證上升、提醒與恢復。保持分頁開啟；停止或背景節流可能延後規則確認。一般對話不會逐筆報數據；想看數字可問「現在數值多少？」。

後端規則、模型設定與 API 詳見 [執行說明](../docs/companion-runtime.md)。
