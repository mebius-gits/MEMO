# WSL Docker 操作

本機 Ubuntu 24.04 的 WSL 2 已安裝 Docker Engine 29.9.2、Compose 5.6.1 與 Buildx。使用 Docker 官方 apt repository；systemd 的 docker 服務已啟用，mebius 帳號已加入 docker 群組。舊的 Ubuntu 終端若仍提示權限不足，重新開啟 Ubuntu 終端即可。

Docker CLI 安裝在 Ubuntu 內，PowerShell 使用 `wsl -d Ubuntu -- docker ...` 呼叫。沒有安裝 Docker Desktop 或 Windows 原生 Docker CLI。

PowerShell 查看版本與服務：

```powershell
wsl -d Ubuntu -- docker version
wsl -d Ubuntu -- docker compose version
wsl -d Ubuntu -- docker compose -f /mnt/c/Users/admin/OneDrive/Desktop/MEMO/compose.yml ps
```

目前 MEMO API、Node-RED、Mosquitto 與 environment-worker 已啟動：

- Node-RED：http://localhost:1880
- Swagger：http://localhost:8000/docs
- MQTT：localhost:1883

開始每 5 秒模擬資料：

```powershell
wsl -d Ubuntu -- docker compose -f /mnt/c/Users/admin/OneDrive/Desktop/MEMO/compose.yml --profile demo up -d --wait
```

重新跑完整容器驗證：

```powershell
wsl -d Ubuntu -- python3 /mnt/c/Users/admin/OneDrive/Desktop/MEMO/scripts/verify_docker.py
```

2026-10-22 已通過 hello-world、Compose build/start、四個服務健康檢查、Node-RED 流程載入、MQTT 寫入三筆、Worker 停止期間發布另外三筆並重啟補收。測試保留 smoke 裝置與六筆資料。Windows localhost 轉送也確認正常。

停止專案並保留資料：

```powershell
wsl -d Ubuntu -- docker compose -f /mnt/c/Users/admin/OneDrive/Desktop/MEMO/compose.yml down
```

參考：[Docker Engine Ubuntu 官方安裝說明](https://docs.docker.com/engine/install/ubuntu/)。
