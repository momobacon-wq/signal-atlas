# Signal Atlas — 訊號邏輯查詢

把一套控制器組態工具的 checkout（15 個控制器、752 個程式檔、約 32 萬個訊號、110 萬個 block 腳位）
抽成可搜尋的靜態網頁：查一個訊號在哪裡被寫、被讀、接在哪個端子、經 EGD 送到哪個控制器、出現在哪張 HMI 畫面、
警報說明是什麼、對應哪張邏輯圖，並可做上下游追蹤。

**線上瀏覽：** https://momobacon-wq.github.io/signal-atlas/ （需要員工代號登入，再輸入站台密語才能解密資料）

## 功能

- 搜尋：訊號名 / 別名 / DeviceTag / 說明子字串，可依控制器篩選；結果標示有 I/O、EGD、HMI、警報、加密。
- 訊號頁：定義（型別、位址、單位量程、EGD 頁）、**來源（寫入者）**、**去向（讀取者，依 Program/Task 分組）**、跨控制器 EGD、
  I/O 端子（機櫃、模組、端子板、端子號、線號、量程）、HMI 畫面與選單路徑、警報說明、邏輯圖號與 P&ID、Watch、出現於哪些程式。
- 追蹤：上游 / 下游 N 跳的樹狀展開（預設 3 跳），加密程式與常數處停下並標示。
- **邏輯方塊圖**：`#/d/<控制器>/<程式>/<Task>` 把一個 task 畫成方塊圖（腳位在兩側、`L:` 接線與同 task 變數用線連、外部變數用 xref 標籤）；
  `#/g/<訊號>` 以訊號為中心畫上下游鄰域圖（跨 task／控制器，EGD 用虛線）。可縮放平移（iPad 雙指）、點方塊看腳位、點變數高亮同名連線、
  雙擊變數跳到寫入者的 task 圖、列印與匯出 SVG。工具列「說明」開關（預設開）在標籤與腳位旁顯示變數／腳位的描述文字。原始組態工具的圖面座標不可得，版面為自動排版，讀圖順序依原繪圖順序。
- 方塊頁 / Task 頁 / 程式瀏覽 / I/O 機櫃樹 / 畫面 → 訊號 / 警報清單；深連結可分享（`#/v/G11.L27QE1_A`）。
- 淺色 / 深色主題、繁體中文介面、iPad 與手機可用。

## 資料保護

- 所有資料檔（`docs/data/**/*.bin`）都是 **gzip 後以 AES-256-GCM 加密**；金鑰由站台密語經 PBKDF2-SHA256（20 萬次）推導，
  密語不在 repo、不在網頁程式裡。沒有密語的人（包含搜尋引擎與爬蟲）只拿得到亂碼。`data/meta.json` 只放鹽值與驗證值。
- 網頁有 `robots.txt` 與 `noindex`；repo 與網頁外殼不含廠名、機組、廠商字樣。
- 員工代號閘門與姊妹站相同：軟性閘門，只擋一般瀏覽並留下紀錄。
- 密語請向站台管理者索取；瀏覽器可選擇記住（存本機 localStorage），「清除密語」可移除。

## 誠實聲明（讀結果前必看）

- **腳位方向是推斷值**。組態 XML 沒有記錄 block 腳位是輸入還是輸出；本站依「介面腳 Usage → 廠商手冊表 → 常數規則 →
  連線投票 → 命名慣例」推斷，每個腳位都顯示來源字母（U/T/C/L/H），推不出來就顯示 `?`。目前已接線腳位的未知率：
  燃氣輪機控制器 3.7%、HRSG 1.8%、循環水 3.9%、BOP 0–1%、勵磁 約 15%（其 block 型別不在手冊內）。
- **加密程式 ≠ 未使用**。部分程式在來源 XML 內即為加密（蒸汽輪機控制器 92 個程式有 88 個），巨集內部多為加密；
  這些只能索引變數宣告與 EGD，追蹤到邊界會標「加密 — 無法追蹤」。
- 部分控制器未在 checkout 內；訊號送出去而站內找不到消費者時顯示「consumer outside checkout」。
- 本站是 checkout 快照（頁尾顯示各控制器修訂時間與建置時間），不是現場控制器的即時狀態。

## 重建（維護者）

本機設定在 `%LOCALAPPDATA%\dcdas\config.json`（checkout 路徑 `src_root`、手冊 PDF 清單 `manual_pdfs`、密語檔 `web_key_file`），
不在 repo 內。

```bash
py tools/dcdas.py build                 # checkout -> %LOCALAPPDATA%\dcdas\ 的 SQLite 索引（全建約 1 分鐘；之後增量）
py tools/dcdas.py status                # 索引是否落後 checkout
py tools/dcdas.py lint / coverage       # 多寫入者 / 方向未知比例
py tests/golden_test.py                 # 驗證鏈 + 計數
py tools/dcdas.py export-web docs       # SQLite -> docs/data（gzip + AES-GCM 加密；先清舊輸出；自動 stamp）
py tools/verify_web.py docs             # 獨立對帳（用同一密語解密），0 錯誤才 exit 0
py tools/auth/mock_server.py 8766       # 本機開 http://127.0.0.1:8766/（閘門 mock；帳號見 tools/auth/mock_users.csv）
```
手冊方向表：`py tools/dcdas.py pindir-import`（從廠商 Block Library PDF 重抽 `tools/pin_dir_table.csv`）；
人工修正寫進 `tools/pin_dir_overrides.csv`（`block_type,pin_name,direction,note`），下次 build 生效。
`export-web --no-encrypt` 只供本機測試，產物不得 push。

## 給 Claude Code

repo 根的 `CLAUDE.md` 說明查詢規則；CLI `py tools/dcdas.py show <CTRL.NAME>` / `trace` / `io` / `egd` / `screen` / `alarm` / `where`
（全部支援 `--json`）。資料契約見 `CONTRACT.md`，萃取器模組契約與 XML 事實見 `tools/dcdas/README_DEV.md`。
