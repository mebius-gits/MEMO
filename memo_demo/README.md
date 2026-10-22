# MEMO 3D Demo

獨立的 Vite + Three.js 網頁：白底畫面只放一個坐著的 MEMO（呼吸、眨眼與情緒表情），右側控制列包含環境讀值、對話與可調參數（裝置、人格設定、環境干擾）。

## 本機啟動

```powershell
cd memo_demo
npm ci
Copy-Item .env.example .env.local
npm run dev
```

開啟 http://localhost:5173。後端網址只從 env 讀取，網頁上沒有設定視窗。

`.env.local` 的 `VITE_API_URL` 指定後端網址，例如 `http://localhost:8000`；只填 origin，不加 `/api/v1`。未設定時，右上角狀態會顯示「未設定 VITE_API_URL」。

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

網站每 5 秒讀取裝置與環境資料，過期資料會標示狀態。尚未設定網址時不會顯示假讀值。

對話呼叫 `POST /api/v1/devices/{device_id}/interaction`，由後端查詢資料庫最新讀值，再輸出文字及情緒。後端設定模型後會實際呼叫；未設定或失敗時回傳明確標示的備援。

Node-RED 啟動後會自動持續送出隨機漂移的正常讀值（見 [Node-RED 說明](../node-red/README.md)）。「參數」分頁的「環境干擾」用滑桿設定溫度、濕度、CO₂ 的偏移，按「套用干擾」後 Node-RED 會在數筆資料內逐步加上偏移，按「恢復正常」逐步回到平常範圍；標題旁的狀態可暫停或恢復傳送。網頁本身不寫入讀值。`VITE_NODE_RED_URL` 指定 Node-RED 位址，Node-RED 的 `CORS_ORIGINS` 須包含網站來源。

## 瀏覽器驗證

先啟動前端與 API，再執行 `npm run test:browser`。預設使用 Windows Chrome；其他環境設定 `CHROME_PATH`。`TEST_API_URL`（預設 localhost:8000）需與前端的 `VITE_API_URL` 相同。測試會寫入 demo 裝置資料。

## 主動回應與記憶

網頁會為本瀏覽器保存 session_id，載入裝置的對話記憶；任務記憶獨立保存在後端。每 5 秒讀取通知，主動呈現 MEMO 的文字與情緒並確認收到。

一般對話不會逐筆報數據；想看數字可問「現在數值多少？」。

## 語音

`VITE_SPEECH_URL` 指向本機語音服務（Compose 的 `speech`，http://localhost:8001）。輸入框旁的麥克風按一下開始、再按一下（或 15 秒後）停止，錄音在瀏覽器轉成 16 kHz WAV 送去辨識，再當成訊息送出。MEMO 的回覆與主動提醒會逐句合成並朗讀，朗讀時 BMO 的嘴巴會動；舞台右上的喇叭按鈕可靜音（記在瀏覽器）。瀏覽器在使用者第一次點擊頁面前會擋自動播放。未設定 `VITE_SPEECH_URL` 時隱藏語音按鈕。

後端規則、模型設定與 API 詳見 [執行說明](../docs/companion-runtime.md)。
