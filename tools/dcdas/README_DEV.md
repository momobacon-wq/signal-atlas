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
Full build of 15 controllers takes ~35 s; `export-web` ~45 s; `verify_web` ~1 min.

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
* Golden numbers (see `tests/golden_test.py`): variable 319,081; G11 blocks 31,417 / tasks 1,018 / encrypted 20;
  S1 encrypted 88/92; WSC1 encrypted 10; hmi navcsv 17,323 after dedup; format_spec 3,060.

## Testing a stage in isolation

```
set DCDAS_DB=%TEMP%\dcdas_test_<stage>.sqlite
py tools\dcdas.py build --vars --no-post          # 8 s, fills variable (319,081 rows)
py tools\dcdas.py build --<stage> --no-post --ctrl WSC1 BOPE1     # then your stage on small controllers
```
Use `sqlite3` in Python to inspect. Never write to the default DB from a stage test. `export-web --no-encrypt` produces
plain JSON for local front-end testing; never publish it.
