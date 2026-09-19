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
docs/assets/                   boot.js、auth.js（員工代號閘門）、app.css、core.js、search.js、signal.js、trace.js、block.js、io.js、browse.js
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
- 連線種類（`conn_kind`）：`V` 變數、`L` 同 task 內 `L:Block.Pin`、`P` 外層巨集介面腳 `L:Pin`、`D` device pin `Block.Pin`、`N` 常數/RUNG 方程式、`E` 列舉、`A` 只有位址、`-` 空。

## `manifest.json`

```json
{"build":"a1b2c3d4e5","site":"Signal Atlas",
 "source":{"toolbox_version":"V07.10.07C","indexed_at":"2026-09-19T15:00:00+08:00","controllers_minor_rev":{"G11":"2026-08-10T08:05:01"}},
 "controllers":[{"name":"G11","kind":"controller","redundancy":"Triple","product_version":"V07.03.02C","n_vars":53561,"n_blocks":31417,"n_pins":223570,"n_programs":132,"n_encrypted":20,"n_io":11900}],
 "shards":{"var":4096,"block":4096,"screen":256},
 "dir_legend":{"U":"介面腳 Usage","T":"手冊表/人工覆寫","C":"常數規則","L":"連線投票","H":"命名慣例","?":"未知"},
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
  "alm":{"id","cls","def","area","causes","action","conseq","urg"}
}}}
```
`block_path` = `Program/Task/UserBlock/.../Block`（第一段 Program、第二段 Task）；原始檔 = `<ctrl>/_<program>.xml`。
邏輯圖號（`drg`）取自 block 本身或其所屬 task 的 `LogicDrg`/`P_ID`。HMI 畫面名不分大小寫合併（以選單的拼法為準）。
前端「來源」判定：`w` 非空 → 邏輯寫入者；否則 `io` 有 `dir:"I"` → 「現場 I/O」；否則 `egd.src` → 「EGD 來自 …」；
否則 `enc` 非空 → 「可能在加密程式內（不可追蹤）」。`egd.p` 非空而 `egd.c` 空 → 「consumer outside checkout」。

## `block/<hhh>.json` — 方塊（key = `CTRL|block_path`，shard = sha1(key) 前 3 hex；空欄位省略）

```json
{"b":{"G11|Trip/FNCTN_ControlTripMaster/RUNG":{"ctrl":"G11","program":"Trip","path":"...","name":"RUNG","type":"RUNG","kind":"block|userblock|task",
  "ver":"…","opaque":1,"desc":"…","drg":"…","pid":"…","device":"…","hmi":"…","attrs":{"Type":"TRP_OVR"},
  "pins":[[name, dir, src, conn_kind, connection, var_full_name|null, tgt_block_key|null, tgt_pin|null, address, alias]],
  "line":1234,"file":"G11/_Trip.xml"}}}
```

## `program/<CTRL>.json`、`io/<CTRL>.json`、`screen/<hh>.json`、`screens.json`、`alarm/<CTRL>.json`

- `program`: `{"ctrl","programs":[{"name","lib","file","enc","help","n_blocks","n_tasks","tasks":[{"name","type","drg","is_task","line","blocks":[[key,name,type,kind,opaque]]}]}]}`
- `io`: `{"ctrl","modules":[{"name","id","cabinet","red","boards":[{"name","hw","pos","points":[[name,dir,conn,tag,addr,type,lo,hi,screws,var_full_name]]}],"internal":[[name,conn,addr,var_full_name]]}]}`（不含 IP）
- `screen`: `{"s":{"X.cim":{"menu":["Block1 / … "],"points":[[full_name, source]]}}}`；`screens.json = {"rows":[[screen, n_points, menu_path]]}`
- `alarm`: `{"ctrl","rows":[[name, desc, cls, def, area, urgency]]}`

## 前端路由（hash）

`#/` 搜尋（`?q=&c=`）、`#/v/<CTRL.NAME>` 訊號、`#/t/<CTRL.NAME>?dir=up|down&hops=3` 追蹤、`#/b/<CTRL>/<block_path>` 方塊/Task、
`#/p/<CTRL>?prog=` 程式瀏覽、`#/io/<CTRL>[/<module>]` I/O、`#/s` 畫面列表、`#/s/<screen.cim>` 畫面、`#/a/<CTRL>?q=` 警報清單。
追蹤在前端 BFS：由訊號卡的 `w`/`r` 取 ref → 載入 `block/` 分片取該 block 其他腳 → 再載入相連變數的 `var/` 分片；
預設深度 3、節點上限 200、記憶體快取分片、顯示載入進度、可中止；加密邊界標「加密 — 無法追蹤」；`?` 方向的腳不追。

## 對帳（`tools/verify_web.py docs`）

以同一密語解密；names 列數 = variable 數；每張訊號卡在正確分片；抽樣 200 卡 + 300 方塊與 SQLite 逐欄相等；無絕對路徑；
`manifest.build` 可重現且與 `meta.json` 一致；除 `meta.json` 外無明文 `.json`；磁碟總量；exit 0 才可 push。
