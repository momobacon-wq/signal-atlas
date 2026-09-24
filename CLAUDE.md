# signal-atlas — 控制器訊號邏輯索引

這個 repo 把一套控制器組態工具的 checkout 抽成 SQLite 索引，並產出 GitHub Pages 靜態查詢站
<https://momobacon-wq.github.io/signal-atlas/>（資料加密，需站台密語）。使用者是儀控工程師。
**廠名、機組、廠商等識別字樣不得寫進 repo（含 commit 訊息、註解、範例）。** 站台專屬細節在本機的
checkout 資料夾 `CLAUDE.md` 與 `%LOCALAPPDATA%\dcdas\config.json`。

## 路徑

- checkout 根目錄：`%LOCALAPPDATA%\dcdas\config.json` 的 `src_root`（或 env `DCDAS_SRC`）
- 索引 DB（不進 repo）：`%LOCALAPPDATA%\dcdas\index.sqlite`（env `DCDAS_DB` 可覆寫）
- 站台密語：`config.json` 的 `web_key_file` 指向的檔案（或 env `DCDAS_WEB_KEY`）；**絕不可進 repo**
- CLI：`py tools\dcdas.py <cmd>`（在本 repo 根目錄執行；`--json` 全指令可用）
- 模組契約與 XML 事實：`tools/dcdas/README_DEV.md`；網頁資料契約：`CONTRACT.md`

## 回答「訊號 X 接到哪 / X 的邏輯 / X 的來源與去向 / X 接在哪個端子」時的規則

1. 先跑 `py tools\dcdas.py status`。若印出 STALE，告訴使用者索引過期並問要不要重建（`build` 約 1 分鐘），不要自己重建。
2. 用 `show <CTRL.NAME>`（裸名撞多控制器時 CLI 會列出候選，再用 `CTRL.NAME`）。找不到就 `find <片段>`（走 FTS，含別名、DeviceTag、警報文字）。
3. 需要上下游多跳才用 `trace <CTRL.NAME> --up N --down N`（預設 `--max-lines 60`；大扇出訊號先看 show）。
4. 端子 / EGD / 畫面 / 警報：`io <tag|var|module>`、`egd <var|ctrl> [--page X]`、`screen <cim|var>`、`alarm <pattern>`。
5. 要驗證或引用原始 XML 時，用 `where <CTRL.NAME>` 拿到 `file:line`，再用 `Read` 的 offset/limit 只讀那幾十行。
   **不要** grep checkout、不要整檔 Read `_*.xml` 或 `Variables.xml`（單檔可達 18 MB、全案 800 MB）。
6. 回答用中文散文 + 英文訊號名；每個結論引用 `CTRL/Program/Task/Block.Pin (file:line)`，並附網頁深連結
   `https://momobacon-wq.github.io/signal-atlas/#/v/<CTRL.NAME>`；要看圖時附 Task 圖 `#/d/<CTRL>/<Program>/<Task>?sel=<CTRL.NAME>`
   或訊號圖 `#/g/<CTRL.NAME>?up=2&down=2`（`show` 的 WRITERS 行 `CTRL/Program/Task/…` 的前三段就是 Task 圖路徑）。
7. 誠實標示不確定：
   - 方向是推斷值。CLI 印 `O/T` 這種「方向/來源」字母：U=介面腳 Usage、T=手冊表或人工覆寫、C=常數規則、L=連線投票、H=命名慣例、`?`=未知。來源是 L/H 時在回答裡寫「推斷」。
   - **加密 ≠ 未使用**。來源 XML 內加密的程式與巨集無法追蹤；CLI 印 `encrypted: not traceable`，照實轉述，不要編邏輯。
   - CLI 印 `consumer outside checkout` 時照實說去向在 checkout 外。
   - 索引是 checkout 快照（`status` 顯示各控制器 MinorRev），不是現場控制器的即時狀態。

## 路徑慣例

- block 路徑 = `CTRL/Program/Task/UserBlock/…/Block`（第一段 Program、第二段 Task），例如 `G11/LubeOil/Alarm/MOVE_21`；
  原始檔 = `<CTRL>/_<Program>.xml`。`L:` 連線指向同 task 內的 block，`L:Pin`（無點）指向外層巨集/task 的介面腳。
- 勵磁控制器（EX2100e）的 block 型別不在手冊內，方向未知率約 15%，其餘 <4%（`coverage` 可看）。

- 「宣告在腳位上的變數」：腳位沒有 `Connection`（`conn_kind A`）但被發佈成全域變數（例 PID 的 `HpBypToCrhPressCv.CVO`）；索引以
  名稱 `Block.Pin` → 宣告位置 → 同位址唯一 三層規則連結（約 16.7 萬個腳位），`show` 的寫入者行會註明 `(variable declared at this pin)`。
- 「腳位值鏡像」：變數是某個**已接線**腳位的發佈值（例 `H11.HpDistCV2.RSP` = Override Station `RSP` 腳的值，該腳接線到 `HpDistCv2PID11_SP`）；
  索引表 `pin_mirror`，`show` 的 source 會印 `value of pin … <- 來源`，`trace --up` 會穿過腳位追到接線來源；輸出腳的鏡像則寫入者為該方塊。
- 「不透明巨集」（`is_opaque=1`，整個 UserBlock 含介面腳都加密，5,082 個實例／123 種型別）：索引把有證據的腳位**回推**成 `pin` 列並標 `origin`
  （`decl` = 宣告在該方塊腳位上的變數，方向來源字母 `R`；`link` = 鄰近方塊的 `L:` 連線，方向來源 `L`；`pair` = 不透明 `AI_INT_k` 旁同層同編號的 `AI_k`／`FF_AI_k`，回推 `IN` 讀其裝置名輸出，`AI_k` 配對再依 `ai_<名>`／`<名>_DS` 命名加 `ReferencedIn` 證據回推 `OUT`／`DEVICE_STATUS`，**命名配對推斷**，來源 `R`；`xref` = 使用者在組態工具交互參照看到、XML 沒有的連線，登錄 `tools/xref_manual.csv`（可用 `xref-paste` 從貼上的 Where Used 文字產生），來源 `T`、網頁徽章「證」）。`block` 會印
  `OPAQUE MACRO: … interface recovered …`，沒有回推腳但程式庫目錄有型別時列「catalogue（只有名稱與 Usage，無接線）」，都沒有就是
  `interface encrypted: no visible pins`。回答時**一定要說明**：回推清單只有證據看得到的部分、可能不完整；宣告變數也可能是巨集內部變數；
  約 1,216 個實例（2oo3_Basic、TIMER_SEC、SIGNAL_CON、RSLEW、AO_INT…）沒有任何可見腳位，這是加密造成的，不是索引漏掉。
- `audit-type <BLOCK_TYPE>`：逐腳位稽核某型別方塊（方向/來源/連線種類/已連結/被使用）；懷疑某類方塊漏接時用它。
- 「裝置名腳位」：`LibName` 是樣板（`{Device}`、`{Device}{Type}`、`{Device}{BlockSuffix}`）的腳位，每個實例的腳位名都不同（AI 方塊的輸出腳就叫裝置名，
  例 `AI_153.HpOTHeatExOutNearSideTemp6_AI`）；索引存 `pin.lib_name`，方向以樣板為鍵、由 `tools/pin_dir_overrides.csv` 判 O（來源字母 `T`，
  依據是同型別全部實例都沒有其他寫入者、有讀取者或上 EGD/HMI）；`audit-type` 把它們合併成一列顯示樣板名。回答時照規則標示為推斷。

## 命名慣例（判讀訊號名用）

- RDS-PP/KKS：`C10PAC30GP001XB65`（XB=數位、XQ=類比）、`MBP80QN202`、`G11MAN10QN001.AU_SEL`（KKS 別名綁在 block pin 上）
- 廠商舊式：`L4T`（L 開頭=邏輯）、`88QA1`、`k_*`=調諧常數、`_A`/`_ALM`=警報位、`_P`=屬性、`_DS`=裝置狀態、`a_*`/`do_*`=硬體入/出
- ISA：`1-TI-CW011-2AA`；描述式：`HpBypToCrhPressCv.OVR_STPB`
- `CTRL.NAME`（如 BOPE1 內的 `G11.L27QE1_A`）= 由 EGD 從 G11 消費來的副本；`Block.Pin` = device block 的腳；
  `Pin@Connection` 的 `L:` = 同 task 內連線、`N:` = 常數/RUNG 方程式、`E:` = 列舉

## 重建與部署（一般由使用者觸發）

```
py tools\dcdas.py build [--ctrl X] [--full]     # checkout -> SQLite（全建約 1 分鐘；不帶 --ctrl 時增量）
py tools\dcdas.py lint / coverage               # 多寫入者 / 方向未知比例
py tools\dcdas.py export-web docs               # SQLite -> docs/data 加密分片 + meta + stamp
py tools\verify_web.py docs                     # 獨立對帳（解密比對），0 錯誤才 exit 0
py tools\auth\mock_server.py 8766               # 本機測站（含員工代號閘門的 mock）
git add -A && git commit && git push            # GitHub Pages 約 10 分鐘生效
```
push 前要先問使用者。`export-web --no-encrypt` 的產物不得 push。JSON 不得含本機絕對路徑（export-web 會自檢）。
