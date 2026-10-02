# dcdas package — module contracts (for developers and Claude)

Source checkout: `%LOCALAPPDATA%\dcdas\config.json` → `src_root` (or env `DCDAS_SRC`). Index DB: `%LOCALAPPDATA%\dcdas\index.sqlite`
(env `DCDAS_DB`). Schema: `db.py` (DDL string). Never write absolute local paths into any published output; store
`decl_file`/`file_path` as paths RELATIVE to the checkout root with `/` (`inventory.rel_to_root`). Never put plant, unit or
vendor names into the repo (code, comments, commit messages); site-specific facts live in the local config and the local
checkout folder's `CLAUDE.md`.

Pipeline (`tools/dcdas.py build`): vars → logic → lib → io → egd → hmi → ledger → resolve → direction → opaque (recover_opaque_pins, refresh_mirror_kinds) → fts. A `schema_version` mismatch in `meta` deletes the DB and rebuilds in full.
Each stage module exposes `run(conn, root, ctrls, log)` (`ctrls` = list of `inventory.Controller`; project-wide stages
take `run(conn, root, log)`). A stage must be idempotent for its scope: delete the rows it owns for that controller,
then insert. Use `db.Batch` for bulk inserts, commit per controller/file, print one line per controller with count + time.
Full build of 19 controllers (+66 registered nodes) takes ~60 s (~25 min when the checkout sits on a cloud-streamed drive that has not cached it yet); `export-web` ~45 s; `verify_web` ~1 min.

## Facts about the XML (verified)

* Logic file `<CTRL>/_<Program>.xml`, PI `GeCss.Config.Blockware.Programme`. Tree:
  `Program` → `Variable*` (program-scope decls) + `TopUserBlock*` / `FFTask*` (= task; attrs Name, BlockType, Version,
  BlockTypeIsTask, DiagramXML(base64, ignore)) → children `Heartbeat/Enable/BlockCPUTicks` (auto pins),
  `Pin*` (interface pins of the task/userblock, attr `Usage` = Input|Output|Const|State|...),
  `Attribute*` (block params; `Name`,`Value`,`Description`; child `Enumeration*`),
  `UserBlock*` (macro instance, same shape as TopUserBlock, nested arbitrarily),
  `Block*` (`Name`,`BlockType`,`Description`,`BlockLayoutData`,`BlockDataType`) → `Pin*`.
  `ZK2188310901404A` = encrypted blob (skip, count). A Program with zero `Block` and ≥1 ZK = encrypted program.
  A UserBlock with only ZK children (no Block/Pin) = opaque macro (`is_opaque=1`, zero XML pins; 5,082 instances / 123 types).
  `resolve.recover_opaque_pins` (after `direction.run`) re-creates the visible part of their interface as real `pin` rows with
  `origin='decl'` (a variable whose `decl_connection` = block path + pin; direction from evidence, `dir_source='R'`) or
  `origin='link'` (a sibling `L:Block.Pin` target; direction opposite to the referrer, `dir_source='L'`) or
  `origin='pair'` (opaque `AI_INT_<k>` next to `AI_<k>` / `FF_AI_<k>` in the same parent: pin `IN` reads that block's
  device-named output variable; for `AI_<k>` pairs also `OUT`→`ai_<stem>` and `DEVICE_STATUS`→`<stem>_DS` when the
  variable's `ReferencedIn` lists the block's program and nothing visible there references it; naming-pair inference,
  `dir_source='R'`, ~2,000 rows). `resolve.recover_vote_pins` then adds `origin='vote'` rows for opaque `2oo3_Basic`
  voters in `FNCTN_<stem>` tasks: `INA/B/C` from the program's encrypted-only ('hidden') references named
  `(ai_)<stem>[_Alt]{A,B,C}[Crctd]`, `BQA/B/C` from the `<input>.BQ` alarm sub-variable or `<input>_BQ` (the hidden one
  first; the `.BQ` field as `conn_kind 'D'` only when neither exists), the task's single
  `HYST` constant, and for single-voter tasks the single `_SP` constant (`HI_LIMIT`) and the single writer-less
  `PRO_<stem>*Hi|Lo` (`OUT`); one instance was confirmed in the tool, the rest is inference (`dir_source='R'`).
  `resolve.load_xref` then adds `origin='xref'` rows from `tools/xref_manual.csv`
  (connections the user verified in the configuration tool's Where-Used but the XML cannot show, e.g. pins of a fully
  encrypted voter instance; `dir_source='T'` when the row's `grade` is tool, `'M'` for mirror/infer rows (`resolve.xref_grade`,
  derived from the note prefix when the column is empty); opaque blocks only; verified beats inferred). `dcdas.py xref-paste <txt>
  --ctrl X` turns a pasted Where-Used tree into such rows: new pins of opaque blocks, and recovered (decl/link/pair) pins
  the tool confirms, which the next build then replaces with the verified row. `query.show` / the web card also report `hidden_ref` / `hid`:
  ReferencedIn programs (not encrypted) in which no pin of the variable is indexed at all, i.e. the reference sits inside an
  encrypted block (~90k variables). `resolve.run`
  purges recovered rows for the controllers being built first (post-only builds). `lib_pin_usage` gives a catalogue
  (names + usage, no wiring) for 12 more types; ~1,216 instances have no visible interface at all.
* `Pin` attrs: `Name`, `Connection`, `Address`, `Value`, `Description`, `Access`, `Alias`, `AliasOverride`, `LibName`,
  `StatusAddress`, `EgdPage`, `FormatSpecification`, `PermanentConnection`, `NovRam`, `LibName`, `Usage`.
* `Connection` forms (classify → `pin.conn_kind`):
  `V` bare name → variable (program-local `<Variable>` first, then controller `variable(ctrl,name)`; `NAME[n]` resolves
  via the base name);
  `L` `L:Block.Pin` → another block's pin in the same task (search the enclosing UserBlock scope, then outward);
  `P` `L:Pin` (no dot) → interface pin of the nearest enclosing UserBlock/TopUserBlock (its `Usage` decides the
  holder's direction: Output → holder is O, Input → holder is I);
  `D` `Block.Pin` (dotted, no prefix) not found as a variable → device-block pin reference (store raw);
  `N` `N:...` constant / RUNG equation; `E` `E:...` enum constant; `A` no Connection but has Address; `-` nothing.
  Dotted names ARE often real variables (`G11.L27QE1_A`, `HpStmBypGrp.OFF`, `1-HS-CW011-3.ON`, and every alarm
  sub-variable `X.BQ` / `X.HH` / `X.INH`) — check the variable table before classifying as `D` (54 `D` pins remain:
  `.BQ` of `*Crctd` inputs that have no sub-variable).
* Alarm sub-pins: `<AlarmSubPinVariable Name="<var>.<SUFFIX>" …/>` inside a task or `Block` (H11 1,886; checkout
  ~10.5k, 1:1 with `<AlarmGlobalSubVariable>` in `Variables.xml`) are the alarm attributes the configuration tool
  attaches to a variable: set-points `.H_SP/.HH_SP/.HHH_SP/.L_SP…`, delays `.H_T…`, `.HYST`, inhibit `.INH`, and the
  flags `.H/.HH/.HHH/.L/.LL/.LLL/.BQ` (BOOL, `AlarmClass`, EGD page HMI, KKS alias). The tool's Where-Used shows them
  as `Program.Task.<var>.<SUFFIX>`. Indexed as pins of the enclosing task/block named `<var>.<SUFFIX>` with
  `lib_name='{Alarm}.<SUFFIX>'` (template key for the direction table): with `Connection` → `V` to the constant,
  Usage Input → I, and the global sub-variable (same address as the constant) becomes a `pin_mirror` kind I; address-only
  with `AlarmClass` = the flag the alarm publishes → stored `usage_declared='Output'` → O, `conn_kind 'A'`,
  `var_id` = the sub-variable; unconnected `.INH` (and one delay) stay Input/A/self. `variable.sub_of` = the parent
  name. Effects: `X.BQ`/`X.HH` connections on plaintext pins resolve to `V` (1,556 → 54 `D` pins), the HMI-page EGD
  points `X.H` resolve (5,308 → 0 unresolved produced points), the flags join the alarm lists (`alarm_class`, no
  `Alarm` id), and `show <parent>` prints an ALARM SUB-PINS section.
* Address-only pins (`conn_kind='A'`) are often published as global variables declared AT the pin (`GlobalNamePrefix`
  Block/Task/Full; same address). `resolve.link_declared_pins` links `pin.var_id` by (1) name `Block.Pin`, (2)
  `decl_connection == Program.Task….Pin`, (3) unique same-address variable (non-`DistributedIO.` preferred); conn_kind
  stays 'A'. ~167k pins. Multi-writer lint (`query.MULTI_WRITER_SQL`): >= 2 DISTINCT ordinary blocks (kind 'block', or a
  recovered/xref pin of an opaque macro) write the variable; task/userblock interface pins never count (interface pin +
  inner block = one path) and writes to different array elements (`X[0]`, `X[1]`) are not the same variable. Patterns:
  plain (check, cross-program first), duplicate (same block type, identical inputs), sfc (SFC scaffolding). The web card
  applies the same rule through the ref tuple's 8th field (kind b/t). `lint` also compares each consumer's exchange
  signature (SigMajor/DataLength from ConsumedData, stored on egd_consumed) with the producer's exchange.
* Template pins: a `Pin` whose `LibName` contains `{...}` (`{Device}`, `{Device}{Type}`, `{Device}{BlockSuffix}`) gets its
  instance `Name` expanded from the block's `Device`/`Type`/`BlockSuffix` attributes (the `Device` attribute itself may be a
  literal `{Unit}{Device}` expanded by the enclosing macro, so never match on `block.device`). Stored in `pin.lib_name`
  (NULL otherwise). Seen only on AI (`{Device}` = the scaled output the manual calls OUT), the selectors
  (DUALSEL/MEDSEL/QUADSEL), the device macros (M_O_V/S_O_V/STARTER/GRP/BREAKER/PID_MA_ENH/OVR_ST_ENH), LOGIC_BUILDER(_SC)
  (`{Device}{Type}`) and FF_AI/FF_AO/FF_DO. `direction.py` keys every decision on `coalesce(lib_name, name)` and
  `tools/pin_dir_overrides.csv` lists them under the template (`AI,{Device},O`); FF_AI's template pin is self-wired to its
  own OUT (`L:FF_AI_n.OUT`) and is left to the link vote.
* Pin direction is NOT in the XML (except `Usage` on interface pins). `direction.py` infers it post-hoc
  (U > T manual table/overrides > C constants > L link votes > H name heuristics > `?`).
* `block.path` = `Program/Task/UserBlock/…/Block` (task names repeat across programs, so the program is part of the key).
* I/O point direction (`parse_io.direction_and_source`): terminal-board family rules and generic name prefixes
  (source `N`, incl. pack/module status words such as `Can<n>_Health`, `PS28vStat`, `IOPackTmpr`, `LINK_OK_`), channel
  parameters (`P`), a Modbus point's own Direction (`X`); points still `?` are decided after `direction.run` by
  `resolve.vote_io_directions` from the logic (only readers -> I, only ordinary-block writers -> O, source `V`; both or
  neither stays `?`). `io` / `show` print `[direction by logic vote]` for `V`.
* HMI navigation points (`parse_hmi.nav_full_point`): exact full name (`resolved_via` full), unit prefix + name
  (prefix), `CTRL.<alias>` when the KKS alias is unique in that controller (alias, ~2.2k points). A point whose first
  segment is an indexed controller or a registered node keeps its name (a node's point is that node's own produced EGD
  point, even when the CSV lists it under a controller's UnitPrefix; the same point under two units is one row).
  Unresolved points of a registered node (any number) or of another non-controller name (>= 5 points) are marked
  `external` and the name is listed in `external_node` (also EGD producers the consumers bind to that are not indexed
  controllers; `node` tells a registered node from a name with no folder). SFC `*_Array` pins of TRANSITION_CONTROL /
  SFC_CONTROL_INTERFACE / TRANSITION_ACTIVATION_CONTROL are shared state (`S`) via `tools/pin_dir_overrides.csv`.
* Trace / signal diagram: a writer or reader that is a task/userblock interface pin is followed only through the inner
  `L:<pin>` pins (`_inner_pins`, web `D.innerPins`); when none is visible the branch ends with "inner driver not
  visible (encrypted block inside the task)" instead of walking every interface pin of the task.
* CLI maintenance commands: `xref-reload [--dry-run]` re-runs purge_recovered → recover_* → load_xref → refresh_mirror_kinds
  → vote_io_directions on the existing index (seconds) after editing `tools/xref_manual.csv`; `--dry-run` only validates the
  rows (`resolve.xref_validate`). `build` and `xref-reload` store `resolve.csv_fingerprints` in meta `csv_sha1`; `status`
  compares and prints STALE with the right command when a hand-maintained CSV changed. `diff-units A B [--what
  constants|alarms|all]` lists control constants and alarm set-points/delays/hysteresis that differ between two
  controllers of the same kind (`query.diff_units`, values compared by meaning).
* Manual value corrections: `variable.snap_value` = the initial value as the snapshot states it (set by every variable
  insert and by the vars upsert); `variable.value` = the value in use. `resolve.load_value_overrides` (build post pass and
  `xref-reload`, right after `load_xref`; always over the whole index) first restores `value = snap_value` everywhere and
  clears `value_override`, then reads `tools/value_overrides.csv` (`resolve.read_value_overrides`: the header must be
  exactly `ctrl,name,value,was,date,basis`, otherwise nothing applies and `status` prints ERROR) and classifies every row
  once per (ctrl, name) (`resolve.classify_value_overrides` / `classify_override`: applied / merged / conflict / missing
  plus a `reason`; blank value or was, value == was, duplicate (ctrl, name) and rows of other than 6 fields are conflicts,
  a controller that is not indexed is missing; values compare as stripped strings or, when both match a strict numeric
  regex, as numbers) into table `value_override`; only `applied` changes `value`. A row is IN EFFECT only while
  `resolve.VO_IN_EFFECT_SQL` holds (applied AND value = the corrected value AND value IS NOT snap_value): a `--no-post`
  build re-inserts variables with the snapshot value but leaves `value_override` as it was. `show` prints the correction
  on the value / source lines only when in effect (`def.value_override.in_effect` in JSON; an applied row not in effect
  gets an `override ... NOT in effect` line), `trace` on the constant leaf, `status` prints the counts, the reasons and a
  WARN (no `fresh`) for applied rows not in effect, export-web the card's `d.vo` (in-effect rows only; `verify_web`
  checks the cards carrying `d.vo` in every var shard equal the in-effect set and the set of variables whose value differs
  from the snapshot).
* `Variables.xml` `Connection` = declaration site, never the writer. `<AlarmGlobalSubVariable>` rows are parsed like
  `<Variable>` plus `sub_of` (their `Connection` = the sub-pin `Program.Task.<var>.<SUFFIX>`).
* `DistributedIO.Xml`: `DistributedIO` → `HardwareGroups/...` → `LanModules/LanModule`(Name, ModuleId, GroupName=cabinet,
  BarCodeR, IoRedundancy, LibraryVersion, ...Port*IPAddress) → `IoPacks/IoPack`(HostName="TBTYPE-Jxx-barcode"),
  `Parameters/Parameter`, `InternalPoints/Point`, `TerminalBoards/TerminalBoard`(Name, HardwareForm, PositionInGroupR)
  → `TerminalBoardPoints/Point`(Name, Connection, Address, DeviceTag) → `Screw*`(Name, Number, CableNumber,
  WireNumber, InterposingTB, Note), `Jumper*`(Name, SelectedValue), `Parameter*`(Name, Value: InputType,
  Low_Input/High_Input/Low_Value/High_Value, ...). Skip `FF*` subtrees (fieldbus). IP addresses are indexed but never exported.
* EGD (namespace `http://geindustrial.com/EGD`): `ProducedData.xml` `Producer`(Name, ProducerId) → `IPAddress*` →
  `Exchange`(ExchangeId, SigMajor, PeriodSecs, PeriodNSecs, DataLength, Page) → `Destination`, `Var`(Name, DType,
  Address, VOffs). `ConsumedData.xml` `Consumer` → `RequiredProducer`(Name, ProducerId) → `ConsumedExchange`(ExchangeId,
  SigMajor, Page, ...) → `TransferAddress`, `BoundVar`(Name, DType, Address, Writable, VOffs). Consumer-side variable
  is named `<Producer>.<Name>` with `DeviceName=<Producer>`. Match producer var by name AND by (producer,
  ExchangeId, VOffs) → `match_method`.
* Devices (`inventory`): the PI class token of `Device.xml` (between the last `.` and the first `,`) decides. The four
  controller classes (suffix `VIeDevice` → controller, `VIeSDevice` → safety, `EX2100*` → exciter, `LS2100*` → drive)
  are indexed; every other class (workstation, thin client, VM, virtualization server, field agent, network switch,
  time server, external device) is a NODE: one `node` row (name, kind hmi/server/network/external/other, class token),
  nothing else read from its `Device.xml` (the bodies hold addresses, host names, SNMP secrets, personnel fields).
  An unknown class is a node unless the folder has logic/variables (then a controller, with a WARN). Nodes' EGD:
  `ProducedData.xml` only (`parse_egd.run_nodes`; `var_id` NULL, no variables; ProducerId not read — it is an encoded
  adapter address); their `ConsumedData.xml` is never opened (each HMI server subscribes to ~200k points: 3M+ rows).
  The ledger tracks a node's `Device.xml` and `ProducedData.xml` only. A drive program name may hold `\` (`LS21eC\SEQ_1`);
  its file is `_LS21eC~SEQ_1.xml` — always use `program.file_path`.
* HMI: `HmiScreens/navigation/tp_actPt_navPointSearchDbStd.csv` (utf-8-sig, no header, rows
  `FullPoint,UnitPrefix,Screen`; some FullPoint rows lack a prefix → prepend UnitPrefix; duplicates exist);
  `HmiScreens/navigation/CIMNavigationMenuItemsStd.csv` (header `Block Name,Menu Item Name,Sub Menu Item Name,
  Item Name,Cim Screen FileName,Unit,ScreenVariables`). `variable.display_screen` is a second source. Screen names are
  case-insensitive (the exporter canonicalises on the menu spelling).
* `FormatSpecifications.xml`: `FormatSpecificationSet` → `FormatSpec`(Name, Units, EngMin, EngMax, Prec, DisplayLow,
  DisplayHigh, MeasurementSystem). `AlarmClasses.xml`: `AlarmClass`(Name, Description, Priority).
* `Watches/*.Watch`: `ToolConfig` → `ToolElements/ToolElement`(Name, DataSourceName).
* Library folders (`<dir>/Library.xml` exists): `_*.xml` PI `GeCss.Config.Blockware.UserBlockLibrary`; may contain
  `ProgramDef`/`UserBlock` definitions with `Pin Usage=…`; `Program@LocalHelpFile="X.mht"` in controller programs
  points at `<library dir>/X.mht`. Do NOT index library internals (instances are expanded in the controllers).
* Golden numbers (see `tests/golden_test.py`): variable 325,064 + 10,532 alarm sub-variables = 335,596; controllers 19
  (controller 11 / safety 3 / exciter 3 / drive 2), nodes 66; G11 blocks 31,417 / tasks 1,018 / encrypted 20;
  S1 encrypted 88/92; WSC1 encrypted 10; hmi navcsv 17,321 after dedup; format_spec 3,069; egd_consumed 3,132.

## Testing a stage in isolation

```
set DCDAS_DB=%TEMP%\dcdas_test_<stage>.sqlite
py tools\dcdas.py build --vars --no-post          # 8 s, fills variable (335,596 rows)
py tools\dcdas.py build --<stage> --no-post --ctrl WSC1 BOPE1     # then your stage on small controllers
```
Use `sqlite3` in Python to inspect. Never write to the default DB from a stage test. `export-web --no-encrypt` produces
plain JSON for local front-end testing; never publish it.

## parse_pei.py — 組態工具列印報表（唯讀證據，不寫 index.sqlite）

匯出的每台控制器有三種 PDF：`_P` 邏輯圖、`_C` 變數交叉表、`_D` 裝置摘要。只吃 `_P`。
（`_C` 比對過 327k 個變數，型別／描述／初值／單位**零筆**與索引不符，所以它只是確認來源，不值得吃進去。）

圖面幾何（皆實測）：A3 1190.52×842 pt、圖框 `[21,21,1168.7,713.9]`、欄 A–Z 在 `x=53.00+43.276i`、
列 00–29 在 `y=37.70+22.730j`。解析的關鍵是**腳位短線（pin stub）**：線寬 ≥0.9、長 2–4.5 pt、貼在方塊邊上，
**圖上顯示的每支腳各畫一條，不論有沒有接線**（沒接線的腳是光禿的短線，或帶一個灰色預設值）；左邊＝輸入、右邊＝輸出。
腳名取方塊內距自己那邊 ≤8 pt 且明顯偏該側的字串；接線標籤取短線自由端 ≤6 pt 內最近的自由字串，超過就記「無標籤」
（`none`：沒接線，或是一條拉到別顆方塊、標籤只印在另一端的線）。顏色分類：`0x191970` 明示腳名、
`0xA9A9A9` **腳本身沒有變數或常數時顯示的預設值**（`default`）——這**不等於沒接線**：方塊之間的連線只記在來源腳上
（`L:<目標>.<腳>`），目標腳照樣印灰色預設值，所以 `print-show` 會回索引查「有沒有線從別顆方塊拉過來」；
黑色字面值通常是設在腳上的常數（`const`，含 `MAN-AUTO-LOCK` 這類列舉；例外：ANALOG_ALARM 的 INH 預設值印成黑色）、
`0x008000` 註解／數值、`0xFFFF00` 執行順序徽章；字級 4.6 是方塊實例名。
分類順序：先看顏色，再看字面值，**再查是否剛好是變數名**（`S1.L4` 是 EGD 副本不是跨頁參照、`k_A/B` 是名字不是算式），
最後才看算式／跨頁參照的樣子。`X.PIN` 本身不是變數名、但 `X` 是一顆方塊時，就是方塊間連線（`link`），即使 `X` 剛好是變數。
`OR_8.OUT` 這種標籤落在 `OR_8` 自己的輸出短線旁，其實是對面方塊輸入端的標籤，丟掉。
SFC 動作頁的 Software Path 會剛好截在 `.Action_Logic_<步驟>` 之前，路徑少一層；若圖上的方塊都不在印出的容器底下，
而該容器**只有一個**子容器裝得下這些方塊，就往下一層（`print_sheet.block_prefix` 記的是修正後的值）。

必要的細節（每一條都是實測會出錯的）：`type=='f'` 的填色一律丟掉（虛線段與白色挖空，留著會把整張圖併成一個方塊）；
0 面積矩形先加厚 0.1 pt 再做聯集（PyMuPDF 對 0 面積矩形的 `intersects()` 永遠 False，會把 OR 閘拆兩半）；
高 <4 或寬 <2 且線寬 ≤0.40 才丟（0.48 是真方塊，丟了會少掉整顆巨集的腳）；字串與短線都會重覆送出，要去重。
邊貼邊畫的方塊會被聯集成**同一個**群組，只有最上面的名字分得到，其他方塊的腳全被算到第一顆頭上。沒分到矩形的名字改找
**自己的外框圖元**：左上角在名字左邊 1.5–5.5 pt、名字下方 18 pt 以內（peer-health 類方塊的名字在框內頂端）；原本佔住整個群組的
方塊也縮回自己的外框。裝置方塊的型別字（`DUALSEL_V2`）與符號字（`1+sTC`）離框邊 11–36 pt，不會誤觸。
（用「在名字處把矩形切一刀」會出錯：小方塊貼在大框下緣時，切出來的兩半一個丟腳、一個多拿別人的輸出。）
短線不做全域合併。一支腳有時畫成兩段相接的線段，每顆方塊各自只算一次，**標籤從兩段中最內側的自由端量起**；
從外側端量會丟掉真的標籤，也會把 8 pt 外的參數註記（`MEAS_SYS = SI`）當成線。兩顆方塊在**同一側**搶同一段短線時
（小方塊貼在大框邊上，x 座標相同），歸給列座標落在自己框內、面積最小的那顆。

圖面身分：標題欄的 `Device Name` 是控制器、`Software Path` 是 `Program.Task[.UserBlock…]`，把 `.` 換成 `/`
就是 `block.path` 的前綴。那格會在 76–86 字元處**直接截斷、不加省略號**，所以 `resolve_sheet_path` 分四種：
`exact`（前綴就是索引的容器）、`repair`（截斷；補全必須唯一，否則不猜）、`internals`（前綴指到的是一顆**方塊**，
這張圖畫的是該不透明巨集的內部，索引根本沒有這些子方塊）、`none`（放棄，該張不產生方塊路徑）。

標題欄只讀 Device Name / Software Path / Sh. No. / Cont. on Sh. / Module Revision、工具版本格（靠欄位座標定位，不寫廠商字面）與
Build Major/Minor 兩個時間戳。其他格子有客戶、廠址與人員識別資訊，**刻意不讀**，以免流進 repo 或站台。
時間戳不可用 `Last Modified`（某些控制器那格放的是列印當天）。

已知限制：Software Path 讀不出路徑的圖（`map_method='none'`）在第 4 版語料只剩 14 張，全部屬於兩類：
巢狀在不透明巨集裡的子巨集內部（`…/C10QCB20KD100/CW_PB_1`，索引本來就沒有這一層），以及在 SFC 步驟名中間被截斷、
補全不唯一的路徑（`…/Step4/HRHBypPressCvOpe`）。早先以為是「旋轉 90 度的順序圖、直排標題欄」，驗證後證實不是：
那些頁面 rotation 都是 0，真正的原因是 Program 名含 `-`（`ST_LPExhP-TAL`）被路徑格式擋掉，以及標題欄另一格也有
`Software` 字樣、錨點抓錯格。兩者已修（`label("Software","Path")`、SWPATH 允許 `-`/`&`），無法歸屬的腳位 1,803 → 407。

### print-show（單一訊號／方塊的圖面證據）

`query.print_show` 讀 `print.sqlite`（`parse_pei.open_print_ro()`，唯讀）並逐腳呼叫 `_pc_bucket`——與 `print_check` 同一個分類器。
三個會讓證據被說錯的陷阱，驗證時各自抓到過真實案例：
- 閘與 MOVE/CALC 類方塊不印腳名（語料約 9 萬列 `pin_name` 為空）。不命名就比對，會把它們全部列成「索引有、圖上沒畫」。
  `_ps_pair` 只在方塊上**唯一**一支腳接同一條線時借用索引的腳名；線對不上時 `_ps_eliminate` 以「這一側只剩這一支沒命名、
  索引這一側也只剩一支沒被畫到」補名（只用於一般方塊；方向未知的索引腳兩側都算候選，只會擋住、不會促成命名）。
- `print_pin.block_path` 為空不代表索引沒有這顆方塊：SFC 動作頁的 Software Path 少了 `Action_Logic_<step>` 一層、
  標題欄會截斷或讀不到。`_PsIndex.place` 以標籤＋印出的路徑前綴／接線做**唯一**定位，找不到才說 `drawing_only`。
- `wire_kind='field'`（`X.FIELD` 且 `X` 是變數）在本案全是 `Block.PIN` 連線，只是剛好有同名 BOOL 變數。變數模式排除它們。

### load_print — 列印圖升格進索引（schema 12：origin `print`、dir_source `G`、`pin_cite`）

`resolve.load_print` 在 `recover_*` 之後、`load_xref` 之前跑（`build` 與 `xref-reload` 都有），逐列呼叫 `query._ps_verdict`，只做三件事：
不透明巨集上圖有索引沒有的腳（V／L／N；灰色預設值與無標籤不收）、`?` 方向依左右側定、推斷列被圖面推翻時以圖面列取代。
驗證時抓到、已寫成規則的陷阱：
- 圖上 `Block.PIN` 連線若指向 **XML 已接線**（常數、變數、連線）的腳，不升格——G11/G12 `IO_OPT_1.IO_Opt → 88QB1.IO_OPT` 那支腳 XML 與圖面都是常數 `AVAIL-MOM_OUT`。
- 欄位參照列（conn_kind `D`，`X.BQ` 存成文字、沒有 var_id）也要比對；原本 `_pc_bucket` 只比 var，54 支表決器 BQ 推斷列錯了卻被當成一致。
- 同一顆方塊的配對前提只有一個：圖面推翻它的 IN/OUT 配對後，剩下沒被圖面**同線**確認的 `pair` 列一併刪除（只同側不算確認）。
- 取代推斷列時保留 address／value／alias／描述，`pin_mirror` 跟著改指新列（刪掉會讓 94 個變數變成「無可見寫入者」）。
- XML 明文腳只改方向，而且只在圖上的線就是它自己的變數時才改。
`print_check`／`print_show` 用 `_PC_PINS_SQL` 看「不含升格列」的索引；已升格的腳在 print-check 計入 `promoted`，不再算 `new_pin`。
