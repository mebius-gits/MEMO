# MEMO 語音服務

本機語音服務，使用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 在 CPU 上執行，不需要 GPU 或 PyTorch。

| 功能 | 模型 | 說明 |
| --- | --- | --- |
| 語音辨識 STT | SenseVoice-Small（int8） | 中英日韓粵自動判斷，輸出轉為台灣繁體（OpenCC `s2twp`） |
| 語音合成 TTS | Kokoro-82M v1.1-zh | 中文女聲 `zf_001`（speaker 3），可混英文 |

模型在 build 時下載並打包進映像（映像約 1.6 GB，執行時記憶體約 0.9 GB），容器啟動不需連網。

```powershell
docker compose up -d --build speech
docker compose exec speech python smoke_test.py   # 合成一句話再辨識回來，比對一致
```

## API（http://localhost:8001）

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| GET | `/health` | 狀態與目前 speaker |
| POST | `/stt` | body 為 16-bit PCM WAV（任意取樣率，最長 30 秒、4 MB），回傳 `text`、`language`、`emotion`、`event` |
| POST | `/tts` | JSON `{"text": "..."}`（最多 500 字，可選 `speed` 0.5–2），回傳 `audio/wav`；頭尾靜音已修剪 |

TTS 前會把 °C、%、CO₂ 等符號轉成念得出的文字，並轉為簡體字（Kokoro 詞典只收簡體）。服務一次處理一個請求。

## 設定

| 變數 | 預設 | 用途 |
| --- | --- | --- |
| `SPEECH_THREADS` | 2 | 每個模型使用的 CPU 執行緒；4 執行緒只快約 15% |
| `TTS_SPEAKER_ID` | 3 | Kokoro 聲音；0–2 為英文聲音，3 起為中文聲音 |
| `TTS_SPEED` | 1.3 | 語速；1.0 約每秒 4 字，1.3 約每秒 5 字（日常對話速度） |
| `CORS_ORIGINS` | 與 API 相同 | 可從瀏覽器呼叫的網站來源 |

參考效能（WSL2、2 執行緒）：辨識 3 秒語音約 0.15 秒；合成速度約為語音長度的 0.6 倍，3 秒的句子約 1.9 秒。網頁逐句合成，第一句完成就開始播放。
