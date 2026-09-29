# signal-atlas — 控制器訊號邏輯索引

這個 repo 把一套控制器組態工具的 checkout 抽成 SQLite 索引，並產出 GitHub Pages 靜態查詢站
<https://momobacon-wq.github.io/signal-atlas/>（資料加密，需站台密語）。使用者是儀控工程師。
**廠名、機組、廠商等識別字樣不得寫進 repo（含 commit 訊息、註解、範例）。** 站台專屬細節在本機的
checkout 資料夾 `CLAUDE.md` 與 `%LOCALAPPDATA%\dcdas\config.json`。

## 路徑

- checkout 根目錄：`%LOCALAPPDATA%\dcdas\config.json` 的 `src_root`（或 env `DCDAS_SRC`）
- 索引 DB（不進 repo）：`%LOCALAPPDATA%\dcdas\index.sqlite`（env `DCDAS_DB` 可覆寫）
- 列印圖語料 DB（不進 repo）：`%LOCALAPPDATA%\dcdas\print.sqlite`（env `DCDAS_PRINT_DB`）；來源是組態工具匯出的
  `<CTRL>_P.pdf`，位置由 `config.json` 的 `pei_dir`（或 env `DCDAS_PEI`）指定，預設 `src_root\PEI\PEI`
- 站台密語：`config.json` 的 `web_key_file` 指向的檔案（或 env `DCDAS_WEB_KEY`）；**絕不可進 repo**
- CLI：`py tools\dcdas.py <cmd>`（在本 repo 根目錄執行；`--json` 全指令可用）
- 模組契約與 XML 事實：`tools/dcdas/README_DEV.md`；網頁資料契約：`CONTRACT.md`

## 回答「訊號 X 接到哪 / X 的邏輯 / X 的來源與去向 / X 接在哪個端子」時的規則

1. 先跑 `py tools\dcdas.py status`。若印出 STALE，告訴使用者索引過期並問要不要重建（`build` 約 1 分鐘），不要自己重建；
   若只是 `xref_manual.csv` 變了，`status` 會改建議 `xref-reload`（幾秒，只重載人工登錄列與回推規則）。
2. 用 `show <CTRL.NAME>`（裸名撞多控制器時 CLI 會列出候選，再用 `CTRL.NAME`）。找不到就 `find <片段>`（走 FTS，含別名、DeviceTag、警報文字）。
3. 需要上下游多跳才用 `trace <CTRL.NAME> --up N --down N`（預設 `--max-lines 60`；大扇出訊號先看 show）。
4. 端子 / EGD / 畫面 / 警報：`io <tag|var|module>`、`egd <var|ctrl> [--page X]`、`screen <cim|var>`、`alarm <pattern>`。
5. 要驗證或引用原始 XML 時，用 `where <CTRL.NAME>` 拿到 `file:line`，再用 `Read` 的 offset/limit 只讀那幾十行。
   **不要** grep checkout、不要整檔 Read `_*.xml` 或 `Variables.xml`（單檔可達 18 MB、全案 800 MB）。
5a. 索引說看不到或只是推斷時（`encrypted: not traceable`、`no visible pins`、`[decl]/[link]/[pair]/[vote]`、來源 L/H/R/C、`?`、
   沒有寫入者），而且 `print.sqlite` 已建立：跑 `print-show <CTRL.NAME>` 或 `print-show <CTRL> <Program/Task/.../Block>`，
   看列印圖怎麼畫（`show` 結尾的 `PRINTED SHEETS:` 行也會提示）。回答時把它**另起一段**當「列印圖證據（尚未併入索引）」，
   引用 `<CTRL>_P.pdf p<頁> Sh.<圖號> <圖格>`；`INDEX PINS NOT DRAWN AS WIRED` 不是反證（沒接線的腳不畫）。
6. 回答用中文散文 + 英文訊號名；每個結論引用 `CTRL/Program/Task/Block.Pin (file:line)`，並附網頁深連結
   `https://momobacon-wq.github.io/signal-atlas/#/v/<CTRL.NAME>`；要看圖時附 Task 圖 `#/d/<CTRL>/<Program>/<Task>?sel=<CTRL.NAME>`
   或訊號圖 `#/g/<CTRL.NAME>?up=2&down=2`（`show` 的 WRITERS 行 `CTRL/Program/Task/…` 的前三段就是 Task 圖路徑）。
7. 誠實標示不確定：
   - 方向是推斷值。CLI 印 `O/T` 這種「方向/來源」字母：U=介面腳 Usage、T=手冊表、人工覆寫或工具查證、M=人工登錄但只是鏡射／推論、C=常數規則、L=連線投票、H=命名慣例、R=回推（不透明巨集）、G=列印邏輯圖（方塊左側入、右側出）、`?`=未知。來源是 L/H/R/M 時在回答裡寫「推斷」；G 是組態工具自己的圖面，不是推斷。
   - **加密 ≠ 未使用**。來源 XML 內加密的程式與巨集無法追蹤；CLI 印 `encrypted: not traceable`，照實轉述，不要編邏輯。
     但**組態工具列印出來的邏輯圖看得到加密內容**：不透明巨集的腳位與接線已由 `build` 的 `[print]` 階段寫進索引（origin `print`、
     方向來源 `G`，網頁徽章「圖」，引用 `<CTRL>_P.pdf p<頁> <圖格>`）。加密**程式**本身的方塊不在索引裡（沒有 XML 方塊可掛），
     仍只能用 `print-show` 看圖面，回答時另起一段說明。
   - CLI 印 `consumer outside checkout` 時照實說去向在 checkout 外。
   - 索引是 checkout 快照（`status` 顯示各控制器 MinorRev），不是現場控制器的即時狀態。

## 列印邏輯圖（`print-scan` / `print-check` / `print-show`，以及 build 的 `[print]` 升格）

組態工具可把整台控制器的邏輯圖列印成 PDF。那份圖是**原廠真值**，而且畫得出 XML 看不到的東西：加密程式的方塊、
不透明巨集的介面腳與內部。既有的 `tools/xref_manual.csv` 就是工程師逐張拍照手抄這些圖的結果。

- `print-gate`：逐檔檢查可不可信（`%%EOF` 完整、三種讀取器頁數一致、**標題欄 Device Name 必須等於檔名所指的控制器**、
  印出的 Build Major/Minor 與索引的 `controller.major_rev/minor_rev` 必須相差同一個整刻鐘時區位移、工具版本相符）。
  任何一項不過就拒收並記下原因，不會半途吃進去。檔名不能當身分依據——實際遇過一支檔名寫 A、內容整本是 B 的列印。
- `print-scan`：把圖面解析進 `print.sqlite`（`print_pdf` / `print_sheet` / `print_pin`）。**完全不動 `index.sqlite`。**
  語料另存一個 DB 是刻意的：索引會因改 schema 或 checkout 變動被刪掉重建，重解析上萬張圖要好幾分鐘。
  語料只存方塊路徑與變數**名稱**，不存索引的 row id，所以重建索引後仍然對得上。
- `print-check`：唯讀差異報告。分類由重到輕：牴觸人工查證（tool 級）＞索引猜成輸出但圖上是輸入＞索引猜成輸入但圖上是輸出
  ＞兩邊變數名不同＞牴觸 XML 明文腳位（先懷疑解析器）＞圖上有而索引沒有的新腳位＞索引只有巨集自身介面變數的佔位值。
- `print-show`：單一變數或方塊的列印圖內容，逐腳對照索引。衝突類別直接用 `print-check` 的分類（同一個 `_pc_bucket`，
  兩者不會分歧），一致再細分 `agree`／`agree_dir`／`fills_dir`，另有 `new_pin`／`placeholder`／`drawing_only`／`unnamed`。
  - 圖上沒印腳名的方塊（MOVE、CALC、比較器…）用接線向索引借腳名（該方塊上**唯一**接同一條線的腳），借不到時用刪去法
    （這一側只剩一支沒命名、索引這一側也只剩一支沒畫到；只用於一般方塊），兩者都會註記。
  - 灰色字是「這支腳本身沒有變數或常數」，**不是**沒接線：方塊間連線只記在來源腳，所以會回索引查有沒有線拉進來。
  - 解析器沒掛上路徑的圖頁（SFC 動作頁少一層、標題欄截斷或讀不到）用標籤定位，而且只接受**唯一**命中：先限定在印出的路徑底下，
    再看接線，最後才看整台控制器是否只有這一顆同名方塊（有可讀路徑卻對不上時不用最後這招）。
  - `Block.PIN` 形式的標籤是方塊之間的連線，就算剛好有同名變數也**不算**該變數的腳。
  - 其他控制器只經 EGD（`producer_var_id`）找對應，並用該訊號在**那台**的名字查；同名不同訊號（`S1.L4` 與 `G11.L4`）不混。
  - 方塊路徑也可給索引裡沒有的加密程式方塊（用圖上的標籤比對），或給 Program 或 `Program/Task` 容器列出圖上的所有方塊。
  - 列印檔被 gate 拒收或根本沒有時只說原因。`show` 在語料存在時會多印一行 `PRINTED SHEETS:` 指過來；語料壞掉或不存在時這行靜默略過，
    絕不讓 `show` 失敗。讀語料一律 `open_print_ro()`（唯讀 URI、不跑 DDL）。
- **升格進索引**（`resolve.load_print`，`build` 與 `xref-reload` 都跑，位置在回推規則之後、`load_xref` 之前，所以人工登錄仍然優先）：
  只收三類，而且腳名必須是圖上印的、或由該腳自己的接線向索引借來（刪去法命名、只靠「全控制器唯一同名」定位的列都不收）：
  ① 不透明巨集上圖有而索引沒有的腳，接的是索引認得的變數（V）、同層方塊連線（L）或字面常數（N）——灰色預設值與無標籤短線不收（沒有關聯可加）；
  ② 索引方向為 `?` 的腳，依圖上左右側定方向（明文腳就地改 `dir_source='G'`）；
  ③ 推斷腳（origin 或來源 R/M/H/L/C）被圖面推翻方向、或回推腳的推斷接線被圖面推翻時，以圖面列取代。
  **XML 明文的接線與人工查證列永不覆寫**（`xml_conflict`／`xref_*` 只列報告）；同一支腳在兩張圖上答案不同就都不動。
  每支升格的腳在 `pin_cite` 記 PDF／頁／圖格，網頁 task 分片的 `pv` 對照表拿來做 tooltip。語料重掃後 `status` 會印 STALE，跑 `xref-reload` 即可。
- `print-check`／`print-show` 比對的是「**不含**圖面升格列」的索引（origin `print` 排除、來源 `G` 當 `?`），避免拿圖面跟自己比；
  `print-show` 在已升格的腳上註記 `promoted into the index from this drawing`。
- 未標腳名的閘符號（AND/OR/NOT/LATCH/計時器）**不猜腳名**（依位置對 IN1/IN2/IN3 實測只有七成多），留空白。

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
