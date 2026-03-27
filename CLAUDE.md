# Claude Code 專案指引

## 行為規則

- 不要問確認，不要說「要繼續嗎？」，直接做
- 發現問題立刻深入 debug，絕對不找藉口（「市場休市」「夜盤無交易」都不是藉口）
- 沒說 push 就不要 push；commit 可以，push 要等明確授權
- 在 Git Bash 環境殺 Windows process，必須用 PowerShell：
  `powershell -Command "Stop-Process -Id <PID> -Force"`
  （bash 的 `kill` 打不到 Windows PID）
- Claude 在對話沒有新訊息時完全無法主動做任何事；用戶說「剩下來交給你」時，必須說清楚這個限制，並建議用 /loop

## 啟動 / 重啟規則

| 改了什麼 | 需要做什麼 |
|---------|-----------|
| `static/*.js` / `*.css` / `*.html` | 瀏覽器 Ctrl+Shift+R |
| `main.py` / `calculator.py` | 重啟 uvicorn |
| `xqfap_feed.py` / `config_xqfap.py` | 重啟整套（stop.bat → start.bat xqfap） |

從 bash 啟動背景進程必須用 PowerShell `Start-Process -WindowStyle Hidden`（nohup & 在 bash tool 裡無效，session 結束時背景進程會被 kill）。

## TAIFEX 選擇權合約命名規則

完整邏輯定義在 `taifex_calendar.py`，包含：
- 10 個前綴代碼（TX1/TX2/TXO/TX4/TX5 週三；TXU/TXV/TXX/TXY/TXZ 週五）
- XQFAP DDE 命名格式（`{前綴}N{月}` 全日盤 / `{前綴}{月}` 日盤）
- 結算日計算（含 TWSE 休市順延）
- 有效合約掃描邏輯（120 組測試）

不要死背合約清單或結算日，用 `taifex_calendar.py` 的函數動態推導。
