# Signal Atlas — 資料契約（extractor ⇄ front-end）

來源：一套控制器組態 checkout（路徑在本機 `%LOCALAPPDATA%\dcdas\config.json` 的 `src_root`，不在 repo）。
中間產物：`%LOCALAPPDATA%\dcdas\index.sqlite`（`tools/dcdas/db.py` 的 DDL；不進 repo）。
輸出：GitHub 公開 repo `momobacon-wq/signal-atlas`，GitHub Pages 從 `main:/docs` 發佈；**資料檔全部加密**。

```
tools/dcdas.py                 CLI（build / status / find / show / trace / … / export-web）
tools/dcdas/*.py               萃取器（README_DEV.md 有各模組契約與 XML 事實）
tools/pin_dir_table.csv        手冊抽出的 block 腳位方向表（block_type,pin_name,direction,source_doc,page）
tools/pin_dir_overrides.csv    人工修正（優先於 table）；這兩份方向表各站台共用
tools/xref_manual.csv、tools/value_overrides.csv   預設站台的人工登錄（交互參照、初始值更正）
tools/sites/<site>/*.csv       其他站台（例 u2）各自的 xref_manual.csv、value_overrides.csv（表頭相同）
tools/make_subsite.py          由 docs/index.html 推出子站頁 docs/<site>/index.html
tools/verify_web.py            獨立對帳：SQLite ⇄ docs/data（解密後逐項比對）
tools/stamp_assets.py          index.html 資產加 ?v=<hash>、version.json
docs/index.html                單頁 App（vanilla JS，無 build step，CSP script-src 'self'，noindex）
docs/robots.txt                Disallow: /
docs/assets/                   boot.js、auth.js（員工代號閘門）、app.css、core.js、search.js、signal.js、trace.js、block.js、io.js、browse.js、
                               glyph.js（方塊圖示 D.glyph：列印圖畫法的符號與 RUNG 梯形圖）、diagram.js（方塊圖引擎 D.dg）、dtask.js（Task 圖）、dsignal.js（訊號圖）、dagre.min.js（vendored 3.1.1，MIT，圖頁才載入）
docs/data/                     export-web 的輸出（下）
docs/<site>/                   子站（例 docs/u2/）：index.html（帶 <meta name="atlas-site" content="<site>">，程式讀 ../assets/、
                               閘門設定讀 ../auth-config.json）、自己的 data/ 與 version.json；不放 assets/、404.html、robots.txt
```

**子站**：每個站台一份本機設定（env `DCDAS_CONFIG` 選取；`site`、`web_dir`、`web_base`、`site_label` 見 `tools/dcdas/db.py`），
自己的索引、語料、`data/`（各自的 salt，同一密語）。前端 localStorage 鍵：主站 `atlas.<k>`（`atlas.key`、`atlas.ctrl`、`atlas.fmask`），
子站 `atlas.<site>.<k>`，互不覆蓋；`ams.theme`、`ams.auth`、`atlas.dg.*` 各站共用。延遲載入的程式（`dagre.min.js`）以 core.js 所在資料夾
為基準，不以頁面為基準。`docs/404.html` 的 `SUBS` 列出子站，打錯的 `/<site>/…` 回該子站首頁。

## 加密與封裝（`export-web`）

- `data/meta.json`（唯一明文）：`{"enc":1,"gzip":1,"build":"<10 hex>","kdf":{"name":"PBKDF2","hash":"SHA-256","iter":200000,"salt":"<b64 16B>"},"check":"<b64 iv||ct>"}`；
  `check` = 以 AAD `check` 加密 UTF-8 字串 `signal-atlas-ok`。
- 其他每個資料檔存為原相對路徑加 `.bin`（`manifest.json.bin`、`names/G11.json.bin`、`var/abc.json.bin`…）；
  內容 = 12 bytes 隨機 IV ‖ AES-256-GCM(gzip(JSON UTF-8))（含 16 bytes tag，WebCrypto 原生格式）；AAD = 不含 `.bin` 的相對路徑（UTF-8）。
- 金鑰 = PBKDF2-HMAC-SHA-256(密語 UTF-8, salt, iter) → 256 bit。密語來自本機 `web_key_file` 或 env `DCDAS_WEB_KEY`，絕不進 repo。
- `manifest.build` = 所有資料檔**明文** sha256 摘要（含路徑）+ manifest（不含 build）的 sha256 前 10 碼；決定性（IV 隨機不影響）。
  前端所有請求加 `?v=<build>`（build 從 `meta.json`／`<meta name="dcdas-build">` 取得）；`docs/version.json = {"build"}`。
- 前端流程：員工代號閘門 → 讀 `meta.json` → 有記住的金鑰（localStorage `atlas.key`，b64 原始金鑰）就用，否則密語視窗 →
  WebCrypto PBKDF2 推導（子站的鍵是 `atlas.<site>.key`）→ 解 `check` 驗證 → 載入器 fetch `.bin` → AES-GCM 解密 → `DecompressionStream('gzip')` → JSON。
  `meta.json` 404 = 未加密的本機測試匯出（`export-web --no-encrypt`），載入器退回直接抓 `.json`；此類產物不得 push。
- 通則：JSON 緊湊（`separators=(',',':')`, `ensure_ascii=False`）；**任何檔案不得含本機絕對路徑**（產生器逐檔 regex 自檢，命中即中止）；
  檔案位置一律是相對 checkout 根的路徑（`G11/_LubeOil.xml`）；每檔目標 ≤ 1 MB（超過只警告）；磁碟總量 > 700 MB 中止。
- 方向字母（`dir`）：`I`/`O`/`S`(state/const)/`?`；來源字母（`src`）：`U` 介面腳 Usage、`T` 手冊表/人工覆寫、`M` 人工登錄（鏡射或推論）、`C` 常數規則、`L` 連線投票、`H` 命名慣例、`R` 回推、
  `G` 圖面（組態工具列印的邏輯圖：方塊左側 = 輸入、右側 = 輸出；是證據不是推斷，前端徽章實框、標「圖面」）、`-` 無。推斷字母 = `L`/`H`/`R`/`M`（虛框）。
- 連線種類（`conn_kind`）：`V` 變數、`L` 同 task 內 `L:Block.Pin`、`P` 外層巨集介面腳 `L:Pin`、`D` 欄位參照 `Var.FIELD`（找不到同名變數時才用；警報子變數建入索引後只剩少數 `*Crctd.BQ`）、`N` 常數/RUNG 方程式、`E` 列舉、`A` 只有位址、`-` 空。

## `manifest.json`

```json
{"build":"a1b2c3d4e5","site":"Signal Atlas",
 "source":{"toolbox_version":"V07.10.07C","indexed_at":"2026-09-19T15:00:00+08:00","controllers_minor_rev":{"G11":"2026-08-10T08:05:01"}},
 "controllers":[{"name":"G11","kind":"controller","redundancy":"Triple","product_version":"V07.03.02C","n_vars":53561,"n_blocks":31417,"n_pins":223570,"n_programs":132,"n_encrypted":20,"n_io":11900,"n_tasks":1018}],
 "nodes":[{"name":"HMI1","kind":"hmi"}],
 "prog_files":{"L1":{"LibA\\Seq_1":"L1/_LibA~Seq_1.xml"}},
 "shards":{"var":4096,"task":4096,"screen":256},
 "dir_legend":{"U":"介面腳 Usage","T":"手冊表/人工覆寫/工具查證","M":"人工登錄：鏡射或推論（未在工具確認）","C":"常數規則","L":"連線投票","H":"命名慣例","R":"回推（不透明巨集）","G":"圖面（列印邏輯圖）","?":"未知"},
 "opaque":{"n":5082,"recovered":792}, "lib_iface":{"AI_INT":[["Enable","I"],["IN","I"],["DEVICE_STATUS","O"],["OUT","O"]], …},
 "flags":{"1":"has_writer","2":"has_io","4":"has_egd","8":"has_hmi","16":"has_alarm","32":"const","64":"egd_copy","128":"in_encrypted","256":"alarm_sub"},
 "encrypted_programs":[["S1","TurbineATSMod"]],
 "block_types":[["MOVE",9802]],
 "related":[{"label":"…","href":"…"}],
 "files":{"names":["names/G11.json"]}}
```

- `controllers` = **只列索引內的控制器**（DB `controller` 表；同一份清單決定 names/program/io/alarm 檔）。`kind` ∈ `controller`（一般控制器）｜`safety`（安全）｜`exciter`（勵磁）｜`drive`（驅動器）；
  前端標籤 `D.KIND`，未知值原樣顯示。`n_io` 為 0 的控制器（例如驅動器），首頁瀏覽不給 I/O 連結。沒有任何變數的控制器可以沒有 `names/<CTRL>.json`。
- `nodes` = **非控制器節點**登錄（DB `node` 表）：`[{"name","kind"}]`，`kind` ∈ `hmi`（HMI／工作站、精簡型終端）｜`server`（伺服器／虛擬機）｜`network`（交換器、時間同步等網路設備）｜`external`（外部資料集中器等）｜`other`。
  **只有名稱與種類**（不含位址、主機名或其他欄位）；節點沒有變數、程式、I/O、警報、訊號卡，**永遠不會有** `names/`、`program/`、`io/`、`alarm/` 檔；名稱與 `controllers` 不重疊。
  部分節點送出 EGD 給控制器，所以訊號卡的 `egd.src.ctrl` 可能是節點名；節點的 ConsumedData（HMI 訂閱）刻意**不索引**。前端頁尾只列各種類數量，不列名稱。
- `site_label`（選用）= 子站人看的站名，來自本機設定 `site_label`，**只在加密 manifest 裡**；前端用在標題、`.brand-sub` 與首頁標題，
  沒有時子站顯示站名大寫、主站不顯示。`related`（選用，預設 `[]`）來自本機設定 `related`。兩者沒設時不出現／為空，主站 build 雜湊不變。
- `prog_files`（選用）= `{ctrl: {program: file}}`，**只列**原始檔不是 `<ctrl>/_<program>.xml` 的程式（例：程式庫程式 `LibA\Seq_1` → `L1/_LibA~Seq_1.xml`）。
  前端 `D.progFile(ctrl, program)` = 查此表，否則 `<ctrl>/_<program>.xml`；方塊記錄的 `file` 欄已是正確檔名（= DB `program.file_path`）。
- 程式名與方塊路徑可含反斜線（`LibA\Seq_1/Task/…`）；`/` 仍是唯一的路徑分隔。hash 路由的路徑片段以 `D.enc` 跳脫 `%`、`?`、`#`、`&`、空白與 `\`（反斜線 → `%5C`），
  `D.dec`（`decodeURIComponent`）還原；分片鍵（`CTRL|Program/Task`、`CTRL.NAME` 的 sha1）一律用原字串（含反斜線），不跳脫。

## `names/<CTRL>.json` — 搜尋索引（每控制器一檔，前端依選取懶載入；「全部」逐檔載入並顯示進度）

`{"ctrl":"G11","rows":[[name, desc, flags, alias, datatype, xref?], ...]}`；`flags` 位元見 manifest。第 6 欄 `xref`（選用）＝
本機設備對照檔（`config.json` 的 `device_xref`）連到這個訊號的圖面編號與位號，空白分隔；沒有就省略（列仍是 5 欄）。搜尋在前端做：
名稱 / 別名（含 xref）/ 說明 子字串（`_`、`.` 保留），排序 exact name > name prefix > alias > desc；查詢字帶 `-`、`/` 時另比一次
兩邊都去掉 `-`、`/`、空白的版本（`AB-CD-1` 命中 `ABCD1`）。

## `var/<hhh>.json` — 訊號卡（shard = sha1(`CTRL.NAME`) 前 3 hex；空值/空陣列/0 的 `const`、`local` 一律省略）

```json
{"v":{"G11.L27QE1_A":{
  "d":{"desc":"...","dt":"BOOL","addr":"01005DE1","val":"0","egd_page":"$Default","alias":"…","fs":"…","units":"…","lo":0,"hi":200,
       "xref":[[drawing_tag, plant_tag, service, source]],            // 選用：本機設備對照檔連到這個訊號（位號依控制器換機組前綴）
       "const":1,"local":1,"decl":["LubeOil",null,"G11/Variables.xml",45234],"device_name":"G11","producer":"G11.X","ref":["EGD","LubeOil"],"screen":"…cim"},
  "w":[[ctrl, program, block_path, block_type, pin, src, line, kind]], // 寫入者（direction O）；kind 'b' = 一般方塊（含不透明巨集的回推／登錄腳），'t' = task／巨集介面腳（與內部寫入者同一條路徑，不算多寫入）
  "r":[[...]],                                                       // 讀取者（direction I/S）
  "u":[[...]],                                                       // 方向未知的腳
  "w_more":0,"r_more":0,                                             // 超過 400 筆時的剩餘數
  "io":[{"ctrl","module","cabinet","board","hw","pos","point","tag","dir","type","lo","hi","kind","screws":[[name,no,cable,wire]]}],
  "egd":{"p":[{"page","ex","voffs"}],"c":[{"ctrl","local","ex","voffs","match","page"}],"src":{"ctrl","var","ex","voffs","match"}}, // c 只列控制器消費者；src.ctrl 可能是節點（無訊號卡）
  "hmi":[[screen, menu_path, source]],"watch":[[ctrl, file]],"drg":[[logic_drg, p_id]],"enc":["Program"],
  "hid":["Program"],                                                // ReferencedIn 有列、索引在該程式內卻無此訊號任何腳位（含回推腳與腳位值鏡像）→ 引用在加密方塊內；空則省略
  "alm":{"id","cls","def","area","causes","action","conseq","urg"}
}}}
```
`d.m`（腳位值鏡像）：變數是某個**已接線**腳位的發佈值時，`{"pin":[ctrl, program, block_path, block_type, pin, "-", line], "kind":"I"|"O"|"?",
"src": {"k":"V","var":full} | {"k":"L"|"P","block":key,"pin":name} | {"k":"N"|"E","text":…} | null}`；`kind I` 的源頭 = `src`（腳位的接線來源），
`kind O` 的寫入者 = 該方塊腳。前端「來源」規則在 `w` 為空時先看 `d.m`。task entry 的 `vm` = `{mirror_full_name: pin_name}`（該 task 內有鏡像的腳位）。

`d.vo`（人工更正）：`tools/value_overrides.csv` 有這個變數、而且更正**生效中**（`value_override.status = applied` 且
`variable.value` = 登錄的新值且 ≠ `variable.snap_value`；`--no-post` build 會把值還原成快照值而不重新套用）時，`d.val` 已是更正後的值，
`d.vo` = `[快照值, 日期, 依據]`；其他情況（沒有登錄、`merged`／`conflict`／`missing`、applied 但未生效）省略，`d.val` 就是快照值。前端「初始值」在值後加警示標籤「人工更正」，
下面一行灰字「快照值 X · 日期 依據」。

`d.sub`（警報子變數）：變數名 `<訊號>.<後綴>` 是組態工具附在 `<訊號>` 上的警報屬性（`AlarmGlobalSubVariable`）時，`d.sub` = 母訊號名（同控制器）。
母訊號卡另有 `subs` = `[[後綴, 型別, 警報等級, 方向, 來源變數 full|null, 來源值|null, EGD 頁, 別名(, 1)]]`（第 9 欄只在來源值是人工更正時出現，值 1）：設定值／延時／遲滯（`.H_SP/.HH_SP/.H_T/.HYST…`）
是 I 腳接常數（子變數 = 該常數的鏡像，`d.m` kind I）；`.H/.HH/.L/.LL/.BQ…` 旗標是 O 腳、警報本身（多發佈在 HMI EGD 頁、帶 AlarmClass）。子腳在 task 分片裡就是母
task／方塊的腳位 `<訊號>.<後綴>`（方向來源 `U`；組態工具的 Where Used 顯示為 `程式.Task.訊號.後綴`）。names 旗標位元 16（警報）= 有 alarm id，或子變數帶 AlarmClass。
設定值鏡像子變數的 `hid`（加密引用）不列：工具把常數的宣告程式列在它下面，不是子變數自己的隱藏引用。
`block_path` = `Program/Task/UserBlock/.../Block`（第一段 Program、第二段 Task；Program 可含 `\`）；原始檔 = `<ctrl>/_<program>.xml`，例外列在 `manifest.prog_files`（方塊記錄的 `file` 已是正確檔名）。
邏輯圖號（`drg`）取自 block 本身或其所屬 task 的 `LogicDrg`/`P_ID`。HMI 畫面名不分大小寫合併（以選單的拼法為準）。
前端「來源」判定：`w` 非空 → 邏輯寫入者；否則 `io` 有 `dir:"I"` → 「現場 I/O」；否則 `egd.src` → 「EGD 來自 …」；
否則 `enc` 非空 → 「可能在加密程式內（不可追蹤）」；否則 `hid` 非空 → 「可能在加密方塊內」（區段「加密引用」列出程式）。
`egd.c` = **只有控制器消費者**（`consumer_ctrl` 在 `manifest.controllers`；全部列出、不設上限；前端 `D.egdCons` 另以 `D.ctrls` 防呆過濾）。
`egd.src` = `{ctrl, var, ex, voffs, match}`：`ctrl` 可能是 `manifest.nodes` 的節點，或兩份清單都沒有的名稱——這兩種情況**沒有訊號卡可連**，
前端（訊號卡、追蹤、訊號圖、Task 圖、圖側欄）一律畫成等寬文字＋種類標籤（「EGD 來自節點 X（HMI／工作站節點，無訊號卡）」），不做 `#/v/` 連結、不當成變數節點展開。
`egd.p` 非空而 `egd.c` 空 → 「EGD 送出，沒有控制器消費者（HMI／工作站節點的訂閱未索引）」：灰色說明，不是警告（去向可能是節點，但節點的訂閱不在索引內）。

## `task/<hhh>.json` — 一個 Task 的全部方塊（key = `CTRL|Program/Task`，shard = sha1(key) 前 3 hex；取代舊的 `block/` 分片）

```json
{"t":{"G11|LubeOil/Alarm":{"n":76,"vd":{"G11.L27QE1":"27QE1 - DC Emergency Lube Oil Pump motor Undervoltage Relay","G11.L27QE1_A":"Emergency lube oil pump motor undervoltage"},"b":{
  "G11|LubeOil/Alarm":          {"kind":"task","pins":[…介面腳…],…},
  "G11|LubeOil/Alarm/_COMMENT": {"kind":"block","type":"_COMMENT","lay":1,"attrs":{"Description":"…"}},
  "G11|LubeOil/Alarm/MOVE_21":  {"ctrl":"G11","program":"LubeOil","path":"LubeOil/Alarm/MOVE_21","name":"MOVE_21","type":"MOVE","kind":"block",
     "lay":60,"attrs":{},"pins":[[name, dir, src, conn_kind, connection, var_full_name|null, tgt_block_key|null, tgt_pin|null, address, alias, desc|null]],
     "line":16036,"file":"G11/_LubeOil.xml"}
}}}}
```
- `b` 的鍵順序 = 原始 XML 文件順序，第一筆是 task 根（`kind:"task"`，其 `pins` 為介面腳）；巢狀 UserBlock 內的方塊同在此 entry（同 task）。
- 方塊記錄欄位同前（`ctrl program path name type kind ver opaque desc drg pid device hmi attrs pins line file`），新增可選 `lay`（`BlockLayoutData`，同層的 1-based 繪圖順序，含 UserBlock；缺值省略）；可選 `eo`（執行順序，取自列印邏輯圖標題旁的數字）與 `pc`（`[pdf, page, cell]`，該方塊在列印圖上的位置），只有列印圖通過 gate 且建置相符、圖頁路徑確定（exact／repair）的方塊才有。空欄位/空陣列/0 一律省略。
- 腳位 tuple 第 11 欄 `desc` = 腳位自身的描述（`Pin@Description` 第一行；無則 `null`），例如產生器方塊 `L4TTRP_OVR.IN1` = "Generator LCI Trip"。
- 腳位 tuple 第 12 欄 `org` = 腳位來源：`null` = XML 明文；`"d"` = **回推自宣告在該腳位的變數**；`"l"` = **回推自鄰近方塊的 `L:` 連線**；`"p"` = **同編號配對**（不透明 `AI_INT_k` 的 `IN` 接同層 `AI_k` 的 `{Device}` 輸出或 `FF_AI_k` 的 `OUT` 變數；`AI_k` 配對再依命名補 `OUT`→`ai_<stem>`、`DEVICE_STATUS`→`<stem>_DS`，且該變數的 `ReferencedIn` 須含此程式而程式內無可見引用；命名推斷、方向 I/O／`R`）；`"v"` = **三取二表決推斷**（不透明 `2oo3_Basic`，task `FNCTN_<stem>`：`INA/INB/INC` 接程式內只在加密方塊引用的 `(ai_)<stem>[_Alt]{A,B,C}[Crctd]` 變數、`BQA/BQB/BQC` 接 `<輸入>.BQ` 警報子變數或 `<輸入>_BQ` 變數（程式內加密引用者優先；皆無時才是無變數的 `conn_kind D` 欄位列）、`HYST` 接唯一的 `k_…<stem>…_HYST`；task 內只有一顆表決器時再補 `HI_LIMIT` ← 唯一的 `k_…_SP`、`OUT` → 唯一無寫入者的 `PRO_<stem>*Hi|Lo`；一顆實例經工具確認、其餘為推斷，方向來源 `R`）；`"x"` = **人工登錄**（`tools/xref_manual.csv`；`grade` 欄 tool = 使用者在組態工具的交互參照或邏輯圖看到 → 方向來源 `T`；mirror／infer = 從另一台控制器鏡射或依規律推論 → 方向來源 `M`，徽章同樣「登錄」但來源字母不同）；`"g"` = **圖面**（腳位列取自組態工具**列印的邏輯圖**：XML 隱藏、圖面畫在不透明巨集上的腳，或被圖面更正的推斷列；
  是權威證據而非推斷，方向通常為 `G`（左側入、右側出），前端徽章「圖」、圖面色；出處見 entry 的 `pv`）。只出現在不透明巨集
  （`opaque:1`，整個 UserBlock 含介面腳都加密）上：索引把「`Variables.xml` 宣告位置 = 該方塊.腳位」的變數與「`L:Block.Pin` 指向該方塊」的連線還原成腳位列，
  方向以證據決定（宣告變數：可調常數→I；位址＝某輸出腳／另一變數→I 並接該來源（後者同時登記為鏡像 `m`）；有讀取者／警報／HMI 且無其他寫入者→O；否則 `?`；
  連線：與 referrer 相反）。方向來源字母 `R`（回推）或 `L`。**清單只有證據看得到的部分，可能不完整**；宣告變數也可能是巨集內部變數。
  這類方塊的記錄多 `rc:[nDecl,nLink,nPair,nXref,nVote,nPrint]`（第 6 欄 = org `g` 圖面腳數；舊資料可能只有兩～五欄）。前端說明行：只有推斷欄 →「介面回推 N 腳（可能不完整）」；
  `nPrint>0` →「圖面 N 腳＋回推 M 腳」（圖面腳不算「可能不完整」的推斷，但圖面只畫該頁有接線的腳，tooltip 保留此提醒）。沒有回推腳位的不透明方塊若型別在 `manifest.lib_iface` 中，前端顯示「目錄介面」（只有腳位名與 Usage 方向 I/O/C/S，無接線）；
  否則顯示「介面加密，無可見腳位」。`vu` 也涵蓋回推腳位參照的變數（見下）。
- `conn_kind='A'` 且 `var_full_name` 非空 = **宣告在腳位上的變數**（腳位沒有 `Connection`，但組態工具把它發佈成全域變數，例如 PID 的 `HpBypToCrhPressCv.CVO`）；索引以「名稱 `Block.Pin` → 宣告位置 → 同位址唯一」三層規則連結。前端把它當作變數腳位（可走線、可標籤、可追蹤），
  但只在該變數「有人用」時顯示：`vu[full] = [nW, nR, flags]`（全索引中對該變數的輸出腳數、方向非 O 的腳數、外部旗標），
  顯示條件 = `nW+nR > 1` 或 `flags & 30 ≠ 0`（只看原有位元 2/4/8/16；32/128 是為無下游判定新增，不得讓它們把每個上 EGD 的宣告腳都畫出來）
  或 (PID 家族的關鍵腳 PV/SP/CVO/CV/CVI/AUTO/RSP/OUT)；「全腳位」開關可全顯。
- `vu` 的涵蓋範圍 = 該 task 內 (a) 宣告在腳位上（`conn_kind A` 且有變數）與回推腳位（`org` 非空）參照的變數、(b) 所有方向 `O` 腳位的變數（不限 `conn_kind`）、
  (c) 該 task 內腳位的鏡像變數（`vm` 的鍵，掛在該腳所屬方塊上）；其他變數不列，前端查不到鍵時視為「無資料」（舊匯出），不得報錯。
  `flags` 是 `vu` 專屬的位元空間（與 names 的 flags 無關）：2=I/O 點（含 `LocalIOVariables.xml` 宣告的本機 I/O 通道：勵磁器／驅動器的應用程式與韌體之間的讀寫，另一端不是方塊）、4=EGD 且有**其他已索引控制器**消費（含作為消費端副本）、8=HMI 點、
  16=警報（`alarm_id`、帶 AlarmClass 的警報子變數，以及**警報子變數的母變數**：`variable.sub_of` 指向的同控制器變數）、
  32=有上 EGD 發佈（與 names flags 4 同一來源）但**沒有**已索引的控制器消費者（訂閱者可能是 HMI／伺服器節點，其訂閱刻意不索引；「沒有控制器消費者」≠「未使用」）、
  128=不可見的引用（`ReferencedIn` 含加密程式，或含某個明文程式卻在該程式內沒有任何可見腳位／鏡像，即訊號卡 `enc`／`hid` 非空）。
  前端的「無下游方塊」判定（見方塊圖規則）用 `flags≠0` 當「有去向」的保守證據。
- `pv`（選用）= 列印邏輯圖出處：`{"<blockKey>#<pin>": [pdf, page, cell]}`，每支 org `g` 或方向來源 `G` 的腳一筆，blockKey 同 `b` 的鍵，
  例 `"G11|FFBInputs_7HA03/FF_IN_S18/AI_INT_12#IN": ["G11_P.pdf", 163, "G16"]`。前端在腳位 tooltip、側欄／方塊頁「來源」欄顯示 `圖面 G11_P.pdf p163 G16`。
- `vd` = 該 task 所有腳位參照到的變數的描述（第一行；無描述者不列），供圖上標籤與腳位旁顯示說明；變數完整描述仍以訊號卡為準。
- 單一方塊 = `task` entry 的 `b[key]`；前端 `D.block(key)` 先算 `D.taskKeyOf(key)`（路徑前兩段）再查。加密程式的 task 沒有 entry（前端以 `program/<CTRL>.json` 的 `enc` 解釋）。
- 尺寸：6,382 個 entry，明文 p50 15 KB、p95 98 KB（`vu` 擴到所有輸出腳變數後約 +36% 列）；兩個 MIS 資料表 task 約 2.2 MB（前端以規模分級處理）。

## `program/<CTRL>.json`、`io/<CTRL>.json`、`screen/<hh>.json`、`screens.json`、`alarm/<CTRL>.json`

- `program`: `{"ctrl","programs":[{"name","lib","file","enc","help","n_blocks","n_tasks","tasks":[{"name","type","drg","is_task","line","blocks":[[key,name,type,kind,opaque]]}]}]}`
- `io`: `{"ctrl","modules":[{"name","id","cabinet","red","boards":[{"name","hw","pos","points":[[name,dir,conn,tag,addr,type,lo,hi,screws,var_full_name]]}],"internal":[[name,conn,addr,var_full_name]]}]}`（不含 IP）
- `screen`: `{"s":{"X.cim":{"menu":["Block1 / … "],"points":[[full_point, source, st, card?]]}}}`；`screens.json = {"rows":[[screen, n_points, menu_path]]}`
  點的 `st` = `1` 對到索引內變數（有訊號卡，前端連結）｜`2` 標為外部（節點的點，或不在索引內的控制器；前端文字＋節點種類標籤）｜`0` 未解析（文字＋「未解析」）。舊的兩欄列視同 `st` = 1。
  第 4 欄 `card` 只在 `st` = 1 且卡片名稱與 `full_point` 不同時出現（畫面點以 KKS 別名或不同大小寫對到變數）：連結一律指向 `card`，否則指向 `full_point`。
- `alarm`: `{"ctrl","rows":[[name, desc, cls, def, area, urgency]]}`

## 前端路由（hash）

`#/` 搜尋（`?q=&c=`）、`#/v/<CTRL.NAME>` 訊號、`#/t/<CTRL.NAME>?dir=up|down&hops=3` 追蹤、`#/b/<CTRL>/<block_path>` 方塊/Task、
`#/p/<CTRL>?prog=` 程式瀏覽、`#/io/<CTRL>[/<module>]` I/O、`#/s` 畫面列表、`#/s/<screen.cim>` 畫面、`#/a/<CTRL>?q=` 警報清單、
`#/d/<CTRL>/<Program>/<Task>?ub=&sel=&b=&f=&page=&all=&pins=&cm=` Task 方塊圖、`#/g/<CTRL.NAME>?up=N&down=N` 訊號方塊圖。

## 方塊圖規則（`diagram.js` / `dtask.js` / `dsignal.js`）

- 原始組態工具的圖面座標不可得（`DiagramXML` 為專有壓縮格式），用 dagre 自動排版；`lay`（缺值則文件順序）決定同層排序與「頁」的閱讀順序；孤立方塊依連通群組分別排版再依序打包成欄。
- 走線只畫資料裡確定的關係：同 task 內 `L:` 接線（消費端指向來源）、同 task 內一寫（≤2）多讀（≤扇出上限）的變數——扇出上限由工具列「扇出」循環 4 / 8 / 全部（`?fan=4|8|all` 只影響本次；偏好存 `localStorage atlas.dg.fan`；預設 8），只因上限而改用標籤的變數數寫在狀態列（「N 個變數超過扇出上限改用標籤」）；其他變數以腳位旁的 xref 標籤呈現（左入右出），點標籤高亮同名所有端點；`P` 介面腳標籤 `⟨pin⟩`；`N/E` 常數為腳位行內文字（已由方塊圖示畫出的常數腳除外，見下）；`A/D` 預設隱藏。
- 方向來自索引的推斷值（腳位徽章顯示來源字母）；`?` 方向腳以虛線/灰色；多寫入者變數走線為紅色虛線；不透明 UserBlock 斜紋框、不可展開。
- 圖面（org `g` 或方向來源 `G`）：port、腳位標籤與任一端為圖面腳的走線（`L:` 與同變數走線）用圖面色（青綠，class `src-g`，自有箭頭；多寫入者紅色仍優先）；腳位名旁徽章「圖」（回推「推」、人工登錄「錄」）；腳位 `<title>` 與側欄／方塊頁「來源」欄附 `圖面 <pdf> p<page> <cell>`（`pv`）。Task 圖中 org `g` 且 `conn_kind` 為 V/L/N/E 的腳一律顯示（不受 `vu`／夥伴條件限制）。工具列「圖例」開浮動面板（`#legend-pop`）說明線型、斜紋框與徽章。
- 不透明巨集：回推腳位（`org` 非空）顯示規則同「宣告在腳位」的變數腳（有夥伴／有人用／`?pins=1` 才畫），腳位名旁標「推」；有 `rc` 的方塊副標「介面回推 N 腳（可能不完整）」（有圖面腳時為「圖面 N 腳＋回推 M 腳」）；
  沒有回推腳但 `lib_iface` 有型別 → 側欄／方塊頁列「目錄介面」；都沒有 → 框內「介面加密，無可見腳位」。訊號圖／追蹤圖遇到有回推腳位的不透明方塊照常經由其腳位擴展，只有無腳位者才是「無法追蹤內部」葉。
- 方塊圖示（`glyph.js`，Task 圖、訊號圖、方塊頁共用）：依型別與字面常數畫出列印邏輯圖的符號——RUNG 依 `EQN` 畫梯形圖（排法與全部列印的 RUNG 逐格相同；接點標腳位字母、斜線 = 常閉、線圈在第 0 列；字母必須正好等於方塊的輸入腳，否則退回 `EQN = …` 文字）、
  COMPARE 依 `FUNC` 畫 ≥ > < = ≤ ≠（GT/LT/EQ… 型別同）（數值 `N:1` 列印為 >，其他數值不畫）、HI_LO_MON 的 H/L 方框、NOT／AND／OR 閘、LATCH 的 S/R（圈出優先的一邊：`RDOM` True → R、False 或未設定（程式庫預設 FALSE）→ S，接變數不圈）、
  MIN_MAX 的 MAX/MIN（未設定 = 預設 MIN，同列印圖）、SELECT 的 CASC 接點加每對 INn／SELn 一個開關（兩者個數不同就不畫）、MOVE 箭頭、ON_OFF_DELAY 的 IN/OUT 波形（`PU_DEL`／`DO_DEL` 未設定 = 0 不畫延時；字面常數標數值、接變數只畫延時不標值）、PULSE 脈波與寬度（ms）等。
  被圖示畫出的字面常數腳（`FUNC`、`EQN`、ON_OFF_DELAY 的常數延時、PULSE 的常數寬度）不再畫成腳位，側欄與方塊頁腳位表照列；`?pins=1` 時照畫。圖示擺在左右腳位名之間（放不下或完整說明模式有腳位描述時放在腳位下方）。
  梯形圖接點 `<title>` 列字母、常開／常閉與所接變數，點接點 = 點該變數（高亮）；訊號圖只畫出部分輸入腳時，畫出的那幾個接點加亮。常數不是字面值、或型別沒有圖示時不畫（寧缺勿錯）。
- 執行順序：方塊有 `eo` 時標題列右側畫紅底黃字小框（數字同列印圖；列印圖上 RUNG、UserBlock 與安全控制器的方塊用白框，網頁一律紅底黃字），tooltip／側欄／方塊頁附列印圖位置（`pc`）；
  沒有列印圖的方塊不標（不用 `lay` 或檔案順序代替：兩者在部分 task 與列印的數字不同）。安全控制器的列印圖只畫方框不畫符號，網頁仍畫同型別的符號。
- 走線上的變數名標籤沿所在線段找不壓到任何方塊的位置（避免蓋住圖示、常閉斜線或執行順序），找不到就不畫（名稱仍在走線 tooltip）。
- 規模：Task 圖 ≤300 方塊全畫，301–1000 依連通群組分頁，>1000 先篩選；訊號圖由 BFS 的 200 節點上限保護。
- 無下游方塊（Task 圖；`?dead=0|1`，0 = 隱藏、預設 1 顯示；`?dead=` 只影響本次，否則偏好存 `localStorage atlas.dg.dead`）：用原始 `pins` 與 task 分片 `b` 全體判定、不用畫出來的 port，範圍是當前根層的直接子方塊（註解方塊除外），只看**直接**下游、不遞移。
  **不判定**（永不隱藏，計入 `nDeadSkip`）：不透明方塊、型別 ANALOG_ALARM／APP_STATE／TEMP_STATUS（作用是輸入變數上的警報子變數或控制器狀態，輸出腳一律 `-`／address-only，輸出腳規則看不到；精確比對型別名，MOVE_STATUS 等一般運算方塊不豁免）、`kind` userblock（內部可能寫全域變數）、任一腳位方向 `?`、沒有任何 `O` 腳、有 `device` 或 `hmi` 的方塊。
  其餘方塊的每支 `O` 腳「有下游」⇔ 任一成立：分片內任何腳位 `conn_kind L` 且 `tgt_block_key`/`tgt_pin` 指向本腳（反向 `L:`）；本腳 `conn_kind` 為 L/P/D/N/E（保守）；
  本腳接變數（`V`，或 `A` 且有變數）且 `vu[full]` 的 `flags≠0` 或 `nR − 本方塊自身讀同一變數的腳數 > 0`；本腳的鏡像變數（`vm`）任一有 `flags≠0` 或 `nR>0`。
  `vu` 缺鍵（本腳或鏡像變數）→ 整個方塊「無資料」不判定（計入 `nDeadNoData`，表示分片是舊匯出）。方塊「無下游」⇔ 全部 `O` 腳都無下游且沒有無資料。
  任一 `O` 腳方向來源為 L/H/R/M → 判定標為推斷（徽章「無下游?」）。顯示模式：虛框灰字、徽章「無下游」、tooltip／側欄列出每支無下游輸出腳與原因；隱藏模式先把這些方塊從成員中移除再建圖
  （寫入者得到正常的 out 標籤、註解黏到下一個方塊；只被隱藏方塊以 `L:` 接走的腳照樣畫出，並補 ext-l 標籤「→ 已隱藏 N」，title 列出被隱藏的方塊腳）。工具列按鈕「隱藏無下游 N」／「顯示無下游 N」，N = 0 時停用；狀態列「無下游 N（推斷 M）」或「已隱藏無下游 N」、「N 個方塊無用量資料」。
  判定只是「索引看不到去向」，不是「邏輯沒有作用」：EGD 只送節點的變數帶 flags 32 視為有去向；加密引用帶 128 視為有去向。
- 訊號圖隱藏分支（只影響顯示，不改資料）：方塊／變數的側欄「隱藏此分支」把該節點藏起來，只畫拿掉隱藏節點後仍與根訊號連通的部分（只經由它連到根的整支一起不畫，另有路徑的共用節點留著）；
  「只看此路徑」把根↔該節點有向路徑上各節點的其他非葉鄰居加入隱藏（該節點後面那一支保留；根不當中繼，經由根繞回的自鎖迴路不算路徑）。被切斷的腳位一律接虛線「⋯ 已隱藏」標記（同一腳上下游各一；多寫入者／EGD 樣式保留；列印／匯出 SVG 也保留，避免被讀成未接線），點標記列清單逐一還原、雙擊全部還原；
  工具列「已隱藏 N」列全部可逐一或全部還原。隱藏集合依根訊號記在分頁記憶體內，改跳數、雙擊展開後照樣套用，重新整理即清空；隱藏的節點仍佔 200 節點名額。
- 說明顯示三段密度（工具列「說明」循環 完整 / 關 / 精簡；`?desc=full|brief|off`（或 `2|1|0`）；偏好存 `localStorage atlas.dg.desc`，預設完整）：
  完整 = xref 標籤為固定寬 190px 說明卡（名稱一行 + 描述換行最多 3 行）、腳位描述在腳位名下方換行最多 2 行（方塊寬上限 220px）、方塊描述為底部 caption 最多 3 行、訊號圖變數為 200px 卡片；
  精簡 = 各處一行截斷；關 = 只有名稱。任何模式下右側面板顯示全文；tooltip 亦含全文。
- 腳位名顯示三段（工具列「腳位」循環 換行 / 完整 / 精簡；`?pin=wrap|full|cut`（或 `1|2|0`）；偏好存 `localStorage atlas.dg.pin`，預設換行）：
  精簡 = 每側單行 80px（約 12 字）；換行 = 每側 120px 內依真實寬撐寬方塊（上限 300px、精簡說明 340px），超過在 `_`／駝峰邊界換行最多 2 行；
  完整 = 不換行、方塊加寬到 480px 內。三者放不下都以 `…` 截尾；所有腳位 `<title>` 含全名；port／走線對齊腳位名第一行，列高加 12px×(行數−1)。
  方塊名／型別行同樣依方塊寬像素截尾（節點 `<title>` 含全名）；型別行只放型別，訊號圖方塊的 Program/Task 路徑另起副標行、在 `/`／`_` 邊界換行最多 2 行（撐寬方塊至多 170px）。
- 版面：圖面高度 = 視窗高 − 頁首 − 8px（`100dvh` 優先）；狀態列（分片／節點／連線／時間）浮在畫布左下角、不佔高度、超長截斷（完整文字在 `title`）；圖面頁（`body.wide`）的頁尾只留第一行小字，MinorRev 表與注意事項只在其他頁面顯示。
追蹤在前端 BFS：由訊號卡的 `w`/`r` 取 ref → 載入 `block/` 分片取該 block 其他腳 → 再載入相連變數的 `var/` 分片；ref 第 8 欄 `kind='t'`（task／巨集介面腳）時不走該 task 的全部腳位，只走 task 分片內 `conn_kind P` 指向該介面腳的內部腳（`D.innerPins`），沒有就畫「內部驅動者在加密方塊」葉節點；
同 task 的 `L:` 連線雙向都走：腳位自己的 `L:`，以及只記在對方腳上的反向 `L:`（`D.linkPeers`：同 task 分片內 `conn_kind L` 且 `tgt_block_key`/`tgt_pin` 指向本腳者；下游由輸出腳找方向非 `O` 的讀取端——多數 `L:` 記在讀取端的輸入腳上——上游由輸入腳找方向 `O` 的寫入端），訊號圖與追蹤頁相同，仍受 12 連鎖與 200 節點上限；
預設深度 3、節點上限 200、記憶體快取分片、顯示載入進度、可中止；加密邊界標「加密 — 無法追蹤」；`?` 方向的腳不追。

## 對帳（`tools/verify_web.py docs`）

以同一密語解密；`manifest.controllers` 名稱 = DB `controller`（雙向）且 `kind` 在四種之內；`manifest.nodes` = DB `node`、與控制器不重疊、節點沒有 names/program/io/alarm 檔；
`prog_files` = DB 中 `file_path ≠ <ctrl>/_<program>.xml` 的程式；names 列數 = variable 數（沒有變數的控制器可缺檔）；抽樣卡的 `egd.c` 只有控制器且筆數 = DB、`egd.src.ctrl` ∈ 控制器 ∪ 節點；
抽樣方塊 `file` = `program.file_path`（含至少一個 `prog_files` 例外程式的方塊）；抽樣畫面 `st`=1 ⇔ `hmi_point.var_id` 非空；每張訊號卡在正確分片；抽樣 200 卡 + 300 方塊與 SQLite 逐欄相等；
抽樣 30 個 task entry 的 `vu`：前 10 列的 `nW`/`nR` = DB 計數，且該 task **所有方向 `O` 腳位的變數都在 `vu` 裡**（缺一個即該 task 不符，訊息寫明缺幾個）；無絕對路徑；
`manifest.build` 可重現且與 `meta.json` 一致；除 `meta.json` 外無明文 `.json`；磁碟總量；頁面 `atlas-site` = 設定的站台；子站頁 = 由主站頁推出的頁（不計 stamp）；
延遲載入的程式都在共用 assets 且沒有以頁面為基準的 `'assets/'` 網址；整個 `docs/` < 1 GB；exit 0 才可 push。不帶參數時對帳設定的 `web_dir`。
