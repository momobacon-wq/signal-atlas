# 登入閘門（員工代號）與登入紀錄

網站是 GitHub Pages 靜態站，沒有伺服器可以驗證帳號，所以把「對照 Users 分頁、寫入紀錄」交給綁定在 Google 試算表上的
Apps Script 網頁應用程式；前端（`docs/assets/auth.js`）在載入資料前先跟它確認。

- 使用者清單：試算表「物料管理系統 的副本」的 **Users** 分頁（`EMPLOYEE_ID`、`EMPLOYEE_NAME`）。改清單只要改分頁，不用重新部署。
- 登入紀錄：同一份試算表自動建立 **AMS_Log** 分頁：`Timestamp | EmployeeID | EmployeeName | ActionType | Site | Page | UserAgent`
  - `LOGIN` 登入成功、`LOGIN_FAIL` 代號不在清單、`LOGIN_BLOCKED` 10 分鐘內失敗 ≥20 次、`VISIT` 沿用工作階段再次開站、`LOGOUT`
  - Site 分「AMS 匯出檔解析（20260910）」與「AMS 資料庫解析（20260912）」；Page 是開站時的 #/… 路徑
- 工作階段 12 小時（`SESSION_HOURS` 與 `docs/auth-config.json` 的 `sessionHours`）；token 是 HMAC 簽章，密鑰存在指令碼屬性。30 分鐘內驗證過的工作階段再開站不會重打端點（所以 VISIT 不會每次重新整理都記一筆）。
- 比對規則：只憑員工代號（去空白、全形轉半形、去前導零）；姓名由 Users 分頁帶出，顯示在右上角並寫入紀錄。
- 端點故障（5xx／逾時／Apps Script 內部錯誤）時，已登入者沿用快取工作階段放行；未登入者看到「無法連線」可重試。`docs/auth-config.json` 若存在但格式錯誤，網站會鎖住並顯示錯誤（避免手滑把站台打開）。
- 這是**軟性閘門**：資料檔本身仍是公開的靜態檔案，閘門只擋一般瀏覽並留下紀錄，不是資安防線。

## 部署（一次，約 3 分鐘）

0. 試算表 檔案 → 設定 → 時區 設為 (GMT+08:00) 台北（AMS_Log 的時間依此顯示）。
1. 開啟試算表 → 擴充功能 → Apps Script，把 `Code.gs` 的內容貼進去（取代預設內容），存檔；左側「專案設定」的時區也設 Asia/Taipei。
2. 部署 → 新增部署作業 → 齒輪選「網頁應用程式」→ 說明隨意、執行身分「**我**」、誰可以存取「**所有人**」→ 部署。
   第一次會要求授權：選你的帳號 → 出現「Google 尚未驗證這個應用程式」→ 點「進階」→「前往 <專案名稱>（不安全）」→ 允許。
   存取權一定要是「所有人」；選「任何擁有 Google 帳戶的使用者」時瀏覽器會被導到 Google 登入頁，網站只會顯示「無法連線」。
3. 複製「網頁應用程式網址」（`https://script.google.com/macros/s/…/exec`），填到 `docs/auth-config.json` 的 `endpoint`，push 到 GitHub（GitHub Pages 的 CDN 最多約 10 分鐘後生效）。
   端點留空 = 閘門關閉，網站照舊。
4. 驗證：瀏覽器開 `…/exec` 應看到 `{"ok":true,"service":"ams-auth"}`；開網站應出現登入畫面，登入後 AMS_Log 多一列。
5. 之後修改 `Code.gs` 要「部署 → 管理部署作業 → 編輯 → 版本：新版本 → 部署」，網址不變。

## 本機測試（不用部署）

```bash
py tools/auth/mock_server.py 8766        # 同時提供 docs/ 靜態檔與 /mock-auth（使用者見 mock_users.csv，紀錄寫 mock_log.jsonl）
# 開 http://127.0.0.1:8766/ 或 /db/ → 輸入 900001 登入（測試甲）
```
測試伺服器會把 `auth-config.json` 改指向 `/mock-auth`；`POST /mock-control {"down":true}` 可模擬端點故障、`{"slow":20}` 模擬逾時。
