# Signal Atlas — 資料契約（extractor ⇄ front-end）

來源：一套控制器組態 checkout（路徑在本機 `%LOCALAPPDATA%\dcdas\config.json` 的 `src_root`，不在 repo）。
中間產物：`%LOCALAPPDATA%\dcdas\index.sqlite`（`tools/dcdas/db.py` 的 DDL；不進 repo）。
輸出：GitHub 公開 repo `momobacon-wq/signal-atlas`，GitHub Pages 從 `main:/docs` 發佈；**資料檔全部加密**。

```
tools/dcdas.py                 CLI（build / status / find / show / trace / … / export-web）
tools/dcdas/*.py               萃取器（README_DEV.md 有各模組契約與 XML 事實）
tools/pin_dir_table.csv        手冊抽出的 block 腳位方向表（block_type,pin_name,direction,source_doc,page）
tools/pin_dir_overrides.csv    人工修正（優先於 table）
tools/verify_web.py            獨立對帳：SQLite ⇄ docs/data（解密後逐項比對）
tools/stamp_assets.py          index.html 資產加 ?v=<hash>、version.json
docs/index.html                單頁 App（vanilla JS，無 build step，CSP script-src 'self'，noindex）
docs/robots.txt                Disallow: /
docs/assets/                   boot.js、auth.js（員工代號閘門）、app.css、core.js、search.js、signal.js、trace.js、block.js、io.js、browse.js、
                               diagram.js（方塊圖引擎 D.dg）、dtask.js（Task 圖）、dsignal.js（訊號圖）、dagre.min.js（vendored 3.1.1，MIT，圖頁才載入）
docs/data/                     export-web 的輸出（下）
```

## 加密與封裝（`export-web`）

- `data/meta.json`（唯一明文）：`{"enc":1,"gzip":1,"build":"<10 hex>","kdf":{"name":"PBKDF2","hash":"SHA-256","iter":200000,"salt":"<b64 16B>"},"check":"<b64 iv||ct>"}`；
  `check` = 以 AAD `check` 加密 UTF-8 字串 `signal-atlas-ok`。
- 其他每個資料檔存為原相對路徑加 `.bin`（`manifest.json.bin`、`names/G11.json.bin`、`var/abc.json.bin`…）；
  內容 = 12 bytes 隨機 IV ‖ AES-256-GCM(gzip(JSON UTF-8))（含 16 bytes tag，WebCrypto 原生格式）；AAD = 不含 `.bin` 的相對路徑（UTF-8）。
- 金鑰 = PBKDF2-HMAC-SHA-256(密語 UTF-8, salt, iter) → 256 bit。密語來自本機 `web_key_file` 或 env `DCDAS_WEB_KEY`，絕不進 repo。
- `manifest.build` = 所有資料檔**明文** sha256 摘要（含路徑）+ manifest（不含 build）的 sha256 前 10 碼；決定性（IV 隨機不影響）。
  前端所有請求加 `?v=<build>`（build 從 `meta.json`／`<meta name="dcdas-build">` 取得）；`docs/version.json = {"build"}`。
- 前端流程：員工代號閘門 → 讀 `meta.json` → 有記住的金鑰（localStorage `atlas.key`，b64 原始金鑰）就用，否則密語視窗 →
  WebCrypto PBKDF2 推導 → 解 `check` 驗證 → 載入器 fetch `.bin` → AES-GCM 解密 → `DecompressionStream('gzip')` → JSON。
  `meta.json` 404 = 未加密的本機測試匯出（`export-web --no-encrypt`），載入器退回直接抓 `.json`；此類產物不得 push。
- 通則：JSON 緊湊（`separators=(',',':')`, `ensure_ascii=False`）；**任何檔案不得含本機絕對路徑**（產生器逐檔 regex 自檢，命中即中止）；
  檔案位置一律是相對 checkout 根的路徑（`G11/_LubeOil.xml`）；每檔目標 ≤ 1 MB（超過只警告）；磁碟總量 > 700 MB 中止。
- 方向字母（`dir`）：`I`/`O`/`S`(state/const)/`?`；來源字母（`src`）：`U` 介面腳 Usage、`T` 手冊表/人工覆寫、`C` 常數規則、`L` 連線投票、`H` 命名慣例、`-` 無。
- 連線種類（`conn_kind`）：`V` 變數、`L` 同 task 內 `L:Block.Pin`、`P` 外層巨集介面腳 `L:Pin`、`D` 欄位參照 `Var.FIELD`（找不到同名變數時才用；警報子變數建入索引後只剩少數 `*Crctd.BQ`）、`N` 常數/RUNG 方程式、`E` 列舉、`A` 只有位址、`-` 空。

## `manifest.json`

```json
{"build":"a1b2c3d4e5","site":"Signal Atlas",
 "source":{"toolbox_version":"V07.10.07C","indexed_at":"2026-09-19T15:00:00+08:00","controllers_minor_rev":{"G11":"2026-08-10T08:05:01"}},
 "controllers":[{"name":"G11","kind":"controller","redundancy":"Triple","product_version":"V07.03.02C","n_vars":53561,"n_blocks":31417,"n_pins":223570,"n_programs":132,"n_encrypted":20,"n_io":11900,"n_tasks":1018}],
 "shards":{"var":4096,"task":4096,"screen":256},
 "dir_legend":{"U":"介面腳 Usage","T":"手冊表/人工覆寫","C":"常數規則","L":"連線投票","H":"命名慣例","R":"回推（不透明巨集）","?":"未知"},
 "opaque":{"n":5082,"recovered":792}, "lib_iface":{"AI_INT":[["Enable","I"],["IN","I"],["DEVICE_STATUS","O"],["OUT","O"]], …},
 "flags":{"1":"has_writer","2":"has_io","4":"has_egd","8":"has_hmi","16":"has_alarm","32":"const","64":"egd_copy","128":"in_encrypted"},
 "encrypted_programs":[["S1","TurbineATSMod"]],
 "block_types":[["MOVE",9802]],
 "related":[{"label":"…","href":"…"}],
 "files":{"names":["names/G11.json"]}}
```

## `names/<CTRL>.json` — 搜尋索引（每控制器一檔，前端依選取懶載入；「全部」逐檔載入並顯示進度）

`{"ctrl":"G11","rows":[[name, desc, flags, alias, datatype], ...]}`；`flags` 位元見 manifest。搜尋在前端做：
名稱 / 別名 / 說明 子字串（`-`、`_`、`.` 保留），排序 exact name > name prefix > alias > desc。

## `var/<hhh>.json` — 訊號卡（shard = sha1(`CTRL.NAME`) 前 3 hex；空值/空陣列/0 的 `const`、`local` 一律省略）

```json
{"v":{"G11.L27QE1_A":{
  "d":{"desc":"...","dt":"BOOL","addr":"01005DE1","val":"0","egd_page":"$Default","alias":"…","fs":"…","units":"…","lo":0,"hi":200,
       "const":1,"local":1,"decl":["LubeOil",null,"G11/Variables.xml",45234],"device_name":"G11","producer":"G11.X","ref":["EGD","LubeOil"],"screen":"…cim"},
  "w":[[ctrl, program, block_path, block_type, pin, src, line]],      // 寫入者（direction O）
  "r":[[...]],                                                       // 讀取者（direction I/S）
  "u":[[...]],                                                       // 方向未知的腳
  "w_more":0,"r_more":0,                                             // 超過 400 筆時的剩餘數
  "io":[{"ctrl","module","cabinet","board","hw","pos","point","tag","dir","type","lo","hi","kind","screws":[[name,no,cable,wire]]}],
  "egd":{"p":[{"page","ex","voffs"}],"c":[{"ctrl","local","ex","voffs","match","page"}],"src":{"ctrl","var","ex","voffs","match"}},
  "hmi":[[screen, menu_path, source]],"watch":[[ctrl, file]],"drg":[[logic_drg, p_id]],"enc":["Program"],
  "hid":["Program"],                                                // ReferencedIn 有列、索引在該程式內卻無此訊號任何腳位（含回推腳）→ 引用在加密方塊內；空則省略
  "alm":{"id","cls","def","area","causes","action","conseq","urg"}
}}}
```
`d.m`（腳位值鏡像）：變數是某個**已接線**腳位的發佈值時，`{"pin":[ctrl, program, block_path, block_type, pin, "-", line], "kind":"I"|"O"|"?",
"src": {"k":"V","var":full} | {"k":"L"|"P","block":key,"pin":name} | {"k":"N"|"E","text":…} | null}`；`kind I` 的源頭 = `src`（腳位的接線來源），
`kind O` 的寫入者 = 該方塊腳。前端「來源」規則在 `w` 為空時先看 `d.m`。task entry 的 `vm` = `{mirror_full_name: pin_name}`（該 task 內有鏡像的腳位）。

`d.sub`（警報子變數）：變數名 `<訊號>.<後綴>` 是組態工具附在 `<訊號>` 上的警報屬性（`AlarmGlobalSubVariable`）時，`d.sub` = 母訊號名（同控制器）。
母訊號卡另有 `subs` = `[[後綴, 型別, 警報等級, 方向, 來源變數 full|null, 來源值|null, EGD 頁, 別名]]`：設定值／延時／遲滯（`.H_SP/.HH_SP/.H_T/.HYST…`）
是 I 腳接常數（子變數 = 該常數的鏡像，`d.m` kind I）；`.H/.HH/.L/.LL/.BQ…` 旗標是 O 腳、警報本身（多發佈在 HMI EGD 頁、帶 AlarmClass）。子腳在 task 分片裡就是母
task／方塊的腳位 `<訊號>.<後綴>`（方向來源 `U`；組態工具的 Where Used 顯示為 `程式.Task.訊號.後綴`）。names 旗標位元 16（警報）= 有 alarm id，或子變數帶 AlarmClass。
設定值鏡像子變數的 `hid`（加密引用）不列：工具把常數的宣告程式列在它下面，不是子變數自己的隱藏引用。
`block_path` = `Program/Task/UserBlock/.../Block`（第一段 Program、第二段 Task）；原始檔 = `<ctrl>/_<program>.xml`。
邏輯圖號（`drg`）取自 block 本身或其所屬 task 的 `LogicDrg`/`P_ID`。HMI 畫面名不分大小寫合併（以選單的拼法為準）。
前端「來源」判定：`w` 非空 → 邏輯寫入者；否則 `io` 有 `dir:"I"` → 「現場 I/O」；否則 `egd.src` → 「EGD 來自 …」；
否則 `enc` 非空 → 「可能在加密程式內（不可追蹤）」；否則 `hid` 非空 → 「可能在加密方塊內」（區段「加密引用」列出程式）。`egd.p` 非空而 `egd.c` 空 → 「consumer outside checkout」。

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
- 方塊記錄欄位同前（`ctrl program path name type kind ver opaque desc drg pid device hmi attrs pins line file`），新增可選 `lay`（`BlockLayoutData`，同層的 1-based 繪圖順序，含 UserBlock；缺值省略）。空欄位/空陣列/0 一律省略。
- 腳位 tuple 第 11 欄 `desc` = 腳位自身的描述（`Pin@Description` 第一行；無則 `null`），例如產生器方塊 `L4TTRP_OVR.IN1` = "Generator LCI Trip"。
- 腳位 tuple 第 12 欄 `org` = 腳位來源：`null` = XML 明文；`"d"` = **回推自宣告在該腳位的變數**；`"l"` = **回推自鄰近方塊的 `L:` 連線**；`"p"` = **同編號配對**（不透明 `AI_INT_k` 的 `IN` 接同層 `AI_k` 的 `{Device}` 輸出或 `FF_AI_k` 的 `OUT` 變數；`AI_k` 配對再依命名補 `OUT`→`ai_<stem>`、`DEVICE_STATUS`→`<stem>_DS`，且該變數的 `ReferencedIn` 須含此程式而程式內無可見引用；命名推斷、方向 I/O／`R`）；`"v"` = **三取二表決推斷**（不透明 `2oo3_Basic`，task `FNCTN_<stem>`：`INA/INB/INC` 接程式內只在加密方塊引用的 `(ai_)<stem>[_Alt]{A,B,C}[Crctd]` 變數、`BQA/BQB/BQC` 接 `<輸入>.BQ` 警報子變數或 `<輸入>_BQ` 變數（程式內加密引用者優先；皆無時才是無變數的 `conn_kind D` 欄位列）、`HYST` 接唯一的 `k_…<stem>…_HYST`；task 內只有一顆表決器時再補 `HI_LIMIT` ← 唯一的 `k_…_SP`、`OUT` → 唯一無寫入者的 `PRO_<stem>*Hi|Lo`；一顆實例經工具確認、其餘為推斷，方向來源 `R`）；`"x"` = **人工查證**（使用者在組態工具的交互參照看到、XML 沒有的連線，登錄於 `tools/xref_manual.csv`，方向來源 `T`、徽章「證」）。只出現在不透明巨集
  （`opaque:1`，整個 UserBlock 含介面腳都加密）上：索引把「`Variables.xml` 宣告位置 = 該方塊.腳位」的變數與「`L:Block.Pin` 指向該方塊」的連線還原成腳位列，
  方向以證據決定（宣告變數：可調常數→I；位址＝某輸出腳／另一變數→I 並接該來源（後者同時登記為鏡像 `m`）；有讀取者／警報／HMI 且無其他寫入者→O；否則 `?`；
  連線：與 referrer 相反）。方向來源字母 `R`（回推）或 `L`。**清單只有證據看得到的部分，可能不完整**；宣告變數也可能是巨集內部變數。
  這類方塊的記錄多 `rc:[nDecl,nLink,nPair,nXref,nVote]`（舊資料可能只有兩～四欄）。沒有回推腳位的不透明方塊若型別在 `manifest.lib_iface` 中，前端顯示「目錄介面」（只有腳位名與 Usage 方向 I/O/C/S，無接線）；
  否則顯示「介面加密，無可見腳位」。`vu` 也涵蓋回推腳位參照的變數。
- `conn_kind='A'` 且 `var_full_name` 非空 = **宣告在腳位上的變數**（腳位沒有 `Connection`，但組態工具把它發佈成全域變數，例如 PID 的 `HpBypToCrhPressCv.CVO`）；索引以「名稱 `Block.Pin` → 宣告位置 → 同位址唯一」三層規則連結。前端把它當作變數腳位（可走線、可標籤、可追蹤），
  但只在該變數「有人用」時顯示：`vu[full] = [nW, nR, flags]`（全索引中對該變數的輸出腳數、輸入腳數、外部旗標 2=I/O 4=EGD（有其他控制器消費）8=HMI 16=警報），
  顯示條件 = `nW+nR > 1` 或 `flags≠0` 或 (PID 家族的關鍵腳 PV/SP/CVO/CV/CVI/AUTO/RSP/OUT)；「全腳位」開關可全顯。`vu` 只列 task 內有此類腳位的變數。
- `vd` = 該 task 所有腳位參照到的變數的描述（第一行；無描述者不列），供圖上標籤與腳位旁顯示說明；變數完整描述仍以訊號卡為準。
- 單一方塊 = `task` entry 的 `b[key]`；前端 `D.block(key)` 先算 `D.taskKeyOf(key)`（路徑前兩段）再查。加密程式的 task 沒有 entry（前端以 `program/<CTRL>.json` 的 `enc` 解釋）。
- 尺寸：6,261 個 entry，明文 p50 9 KB、p95 61 KB；兩個 MIS 資料表 task 約 2 MB（前端以規模分級處理）。

## `program/<CTRL>.json`、`io/<CTRL>.json`、`screen/<hh>.json`、`screens.json`、`alarm/<CTRL>.json`

- `program`: `{"ctrl","programs":[{"name","lib","file","enc","help","n_blocks","n_tasks","tasks":[{"name","type","drg","is_task","line","blocks":[[key,name,type,kind,opaque]]}]}]}`
- `io`: `{"ctrl","modules":[{"name","id","cabinet","red","boards":[{"name","hw","pos","points":[[name,dir,conn,tag,addr,type,lo,hi,screws,var_full_name]]}],"internal":[[name,conn,addr,var_full_name]]}]}`（不含 IP）
- `screen`: `{"s":{"X.cim":{"menu":["Block1 / … "],"points":[[full_name, source]]}}}`；`screens.json = {"rows":[[screen, n_points, menu_path]]}`
- `alarm`: `{"ctrl","rows":[[name, desc, cls, def, area, urgency]]}`

## 前端路由（hash）

`#/` 搜尋（`?q=&c=`）、`#/v/<CTRL.NAME>` 訊號、`#/t/<CTRL.NAME>?dir=up|down&hops=3` 追蹤、`#/b/<CTRL>/<block_path>` 方塊/Task、
`#/p/<CTRL>?prog=` 程式瀏覽、`#/io/<CTRL>[/<module>]` I/O、`#/s` 畫面列表、`#/s/<screen.cim>` 畫面、`#/a/<CTRL>?q=` 警報清單、
`#/d/<CTRL>/<Program>/<Task>?ub=&sel=&b=&f=&page=&all=&pins=&cm=` Task 方塊圖、`#/g/<CTRL.NAME>?up=N&down=N` 訊號方塊圖。

## 方塊圖規則（`diagram.js` / `dtask.js` / `dsignal.js`）

- 原始組態工具的圖面座標不可得（`DiagramXML` 為專有壓縮格式），用 dagre 自動排版；`lay`（缺值則文件順序）決定同層排序與「頁」的閱讀順序；孤立方塊依連通群組分別排版再依序打包成欄。
- 走線只畫資料裡確定的關係：同 task 內 `L:` 接線（消費端指向來源）、同 task 內一寫（≤2）多讀（≤4）的變數；其他變數以腳位旁的 xref 標籤呈現（左入右出），點標籤高亮同名所有端點；`P` 介面腳標籤 `⟨pin⟩`；`N/E` 常數為腳位行內文字；`A/D` 預設隱藏。
- 方向來自索引的推斷值（腳位徽章顯示來源字母）；`?` 方向腳以虛線/灰色；多寫入者變數走線為紅色虛線；不透明 UserBlock 斜紋框、不可展開。
- 不透明巨集：回推腳位（`org` 非空）顯示規則同「宣告在腳位」的變數腳（有夥伴／有人用／`?pins=1` 才畫），腳位名旁標「推」；有 `rc` 的方塊副標「介面回推 N 腳（可能不完整）」；
  沒有回推腳但 `lib_iface` 有型別 → 側欄／方塊頁列「目錄介面」；都沒有 → 框內「介面加密，無可見腳位」。訊號圖／追蹤圖遇到有回推腳位的不透明方塊照常經由其腳位擴展，只有無腳位者才是「無法追蹤內部」葉。
- 規模：Task 圖 ≤300 方塊全畫，301–1000 依連通群組分頁，>1000 先篩選；訊號圖由 BFS 的 200 節點上限保護。
- 說明顯示三段密度（工具列「說明」循環 完整 / 關 / 精簡；`?desc=full|brief|off`（或 `2|1|0`）；偏好存 `localStorage atlas.dg.desc`，預設完整）：
  完整 = xref 標籤為固定寬 190px 說明卡（名稱一行 + 描述換行最多 3 行）、腳位描述在腳位名下方換行最多 2 行（方塊寬上限 220px）、方塊描述為底部 caption 最多 3 行、訊號圖變數為 200px 卡片；
  精簡 = 各處一行截斷；關 = 只有名稱。任何模式下右側面板顯示全文；tooltip 亦含全文。
- 腳位名顯示三段（工具列「腳位」循環 換行 / 完整 / 精簡；`?pin=wrap|full|cut`（或 `1|2|0`）；偏好存 `localStorage atlas.dg.pin`，預設換行）：
  精簡 = 每側單行 80px（約 12 字）；換行 = 每側 120px 內依真實寬撐寬方塊（上限 300px、精簡說明 340px），超過在 `_`／駝峰邊界換行最多 2 行；
  完整 = 不換行、方塊加寬到 480px 內。三者放不下都以 `…` 截尾；所有腳位 `<title>` 含全名；port／走線對齊腳位名第一行，列高加 12px×(行數−1)。
  方塊名／型別行同樣依方塊寬像素截尾（節點 `<title>` 含全名）；型別行只放型別，訊號圖方塊的 Program/Task 路徑另起副標行、在 `/`／`_` 邊界換行最多 2 行（撐寬方塊至多 170px）。
- 版面：圖面高度 = 視窗高 − 頁首 − 8px（`100dvh` 優先）；狀態列（分片／節點／連線／時間）浮在畫布左下角、不佔高度、超長截斷（完整文字在 `title`）；圖面頁（`body.wide`）的頁尾只留第一行小字，MinorRev 表與注意事項只在其他頁面顯示。
追蹤在前端 BFS：由訊號卡的 `w`/`r` 取 ref → 載入 `block/` 分片取該 block 其他腳 → 再載入相連變數的 `var/` 分片；
預設深度 3、節點上限 200、記憶體快取分片、顯示載入進度、可中止；加密邊界標「加密 — 無法追蹤」；`?` 方向的腳不追。

## 對帳（`tools/verify_web.py docs`）

以同一密語解密；names 列數 = variable 數；每張訊號卡在正確分片；抽樣 200 卡 + 300 方塊與 SQLite 逐欄相等；無絕對路徑；
`manifest.build` 可重現且與 `meta.json` 一致；除 `meta.json` 外無明文 `.json`；磁碟總量；exit 0 才可 push。
