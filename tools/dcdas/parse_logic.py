# -*- coding: utf-8 -*-
"""<CTRL>\\_<Program>.xml (PI GeCss.Config.Blockware.Programme) -> program / task / block / block_attr / pin
(+ program-local <Variable> declarations -> variable with is_program_local=1).

Tree (verified, see README_DEV.md):
  Program -> Variable* + AlarmSubVariable* (ignored) + (TopUserBlock | FFTask)*  (= task)
    task -> Heartbeat/Enable/BlockCPUTicks (auto pins, Usage=Output/Input/Output), Pin* (interface pins, Usage=...),
            Attribute*, UserBlock* (nested arbitrarily), Block* -> Attribute*, Pin*
  ZK2188310901404A = encrypted blob (never read, only counted); DiagramXML attribute = ignored.
  FFTask (Foundation Fieldbus task, e.g. G11/_FFBInputs_7HA03.xml) has the same shape as TopUserBlock and is
  indexed as a task too (README_DEV only mentions TopUserBlock).

Rows:
  program   one per file
  task      one per TopUserBlock/FFTask; the same element is ALSO a block row with kind='task' so its interface pins
            live in pin(block_id) and 'L:Pin' references can target them.
  block     kind='userblock' (<UserBlock>) / 'block' (<Block>).  path = 'Program/Task/UB1/UB2/Block'.
            NOTE: the plan said 'Task/UB/Block' but task names repeat across programs of one controller (G11: 27
            names, 6,267 duplicate paths), which would break UNIQUE(block.ctrl, block.path); the program name is
            therefore prefixed.
  pin       every <Pin> + the three auto pins.  conn_kind V/L/P/D/N/E/A/-  (README_DEV.md).
            direction / dir_source are left NULL (direction.py fills them later); usage_declared = Pin@Usage.

Connection resolution (done after the whole file is parsed, because 'L:' may point forward):
  V  bare or dotted name found in the variable dict (program-local decls first, then variable(ctrl,name))
  D  dotted name NOT found in the variable dict (device-block pin reference, stored raw)
  L  'L:X.Y'  -> tgt_block_id = block/userblock named X found by walking scopes outward, tgt_pin = Y.
     For a <Block> pin the search starts in the scope that contains the block; for a UserBlock's own interface pin
     it starts in the PARENT scope (that is where the macro is wired; measured 641 parent vs 0 inner in G11) and
     falls back to the macro's inside.  The scope's own name also matches X.
  P  'L:Y' (no dot) -> tgt_block_id = nearest enclosing UserBlock/task block row (the pin owner's parent scope),
     tgt_pin = Y.
  Array element references ('LCY_SH_AR[0]', 'L:Action_Status[2]') resolve var_id / tgt_pin by the base name before
  '[' (536 distinct names in G11, all of them arrays declared in Variables.xml); `connection` keeps the raw text.
"""
import time
from pathlib import Path
from typing import Iterable, Optional

from lxml import etree

from .db import Batch
from .inventory import Controller, head_info, rel_to_root
from .parse_vars import row_from_attrib, var_index

TASK_TAGS = ("TopUserBlock", "FFTask")
SCOPE_TAGS = TASK_TAGS + ("UserBlock", "Block")
AUTO_PINS = {"Heartbeat": "Output", "Enable": "Input", "BlockCPUTicks": "Output"}
ZK_TAG = "ZK2188310901404A"
LIFTED_ATTRS = {"LogicDrg": "logic_drg", "P_ID": "p_id", "Device": "device", "HMILinkedObject": "hmi_linked_object",
                "Desc": "description"}
# elements whose subtree is complete at their 'end' event and can be dropped from memory
CLEAR_TAGS = SCOPE_TAGS + ("Pin", "Attribute", "Variable", "AlarmSubVariable", ZK_TAG, "BlockwareGeneratorData",
                           "ExecutionGroup", "Heartbeat", "Enable", "BlockCPUTicks")

INSERT_PROGRAM = """INSERT INTO program(id,ctrl,name,library_type,file_path,encrypted,block_count,task_count,help_file)
VALUES(?,?,?,?,?,?,?,?,?)"""
INSERT_TASK = "INSERT INTO task(id,program_id,name,block_type,logic_drg,is_task,line_no) VALUES(?,?,?,?,?,?,?)"
INSERT_BLOCK = """INSERT INTO block(id,ctrl,program_id,task_id,parent_id,path,name,block_type,kind,version,is_opaque,
description,logic_drg,p_id,device,hmi_linked_object,line_no,layout) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
INSERT_ATTR = "INSERT OR IGNORE INTO block_attr(block_id,name,value) VALUES(?,?,?)"


def _layout(v):
    """BlockLayoutData = 1-based drawing order of a Block/UserBlock inside its diagram (0 on the task root)."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None
INSERT_LOCAL_VAR = """INSERT INTO variable(id,
  ctrl,name,full_name,description,datatype,address,scope,value,decl_connection,decl_program,decl_task,global_prefix,
  egd_page,alias,format_spec,units,disp_low,disp_high,display_screen,control_constant,device_name,referenced_in,
  alarm_id,alarm_class,alarm_definition,plant_area,potential_causes,operator_action,consequence,urgency,
  normal_severity,active_severity,is_program_local,decl_file,decl_line)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
INSERT_PIN = """INSERT INTO pin(id,block_id,name,conn_kind,connection,var_id,tgt_block_id,tgt_pin,address,value,alias,
alias_override,usage_declared,description,line_no,lib_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

STAT_KEYS = ("programs", "tasks", "fftasks", "blocks", "userblocks", "opaque", "pins", "attrs", "encrypted", "zk",
             "local_vars", "unres_V", "unres_L", "unres_P", "skipped_files")


class _Ids:
    """Running id counters seeded from max(id) of each table (rows are inserted after the file is parsed)."""

    def __init__(self, conn):
        self.n = {}
        for t in ("program", "task", "block", "pin", "variable"):
            self.n[t] = conn.execute(f"SELECT coalesce(max(id),0) FROM {t}").fetchone()[0]

    def next(self, table):
        self.n[table] += 1
        return self.n[table]


class _Frame:
    """One open TopUserBlock/FFTask/UserBlock/Block scope while parsing (and after, for L: resolution)."""
    __slots__ = ("tag", "kind", "id", "name", "path", "parent", "task_id", "names", "attrs", "lifted",
                 "n_block", "n_pin", "n_ub", "n_zk", "line", "a")

    def __init__(self, tag, bid, name, path, parent, task_id, line, attrib):
        self.tag, self.id, self.name, self.path, self.parent, self.task_id, self.line = tag, bid, name, path, parent, task_id, line
        self.kind = "task" if tag in TASK_TAGS else ("userblock" if tag == "UserBlock" else "block")
        self.names = {}          # direct child Block/UserBlock name -> block id
        self.attrs = []          # (name, value) not lifted
        self.lifted = {}         # column -> value
        self.n_block = self.n_pin = self.n_ub = self.n_zk = 0
        self.a = dict(attrib)    # element attributes (DiagramXML dropped)
        self.a.pop("DiagramXML", None)


def classify(connection: Optional[str], address: Optional[str]):
    """-> (conn_kind, x, y): x/y only for L (block, pin) and P (None, pin)."""
    c = connection or ""
    if not c:
        return ("A" if address else "-"), None, None
    if c.startswith("L:"):
        body = c[2:]
        if "." in body:
            x, y = body.rsplit(".", 1)
            return "L", x, y
        return "P", None, body
    if c.startswith("N:"):
        return "N", None, None
    if c.startswith("E:"):
        return "E", None, None
    return "V", None, None      # bare or dotted name: decided against the variable dict later (V or D)


def _find_block(frame: Optional[_Frame], x: str):
    """Walk scopes outward: direct children named x, or the scope itself named x."""
    f = frame
    while f is not None:
        bid = f.names.get(x)
        if bid is not None:
            return bid
        if f.name == x:
            return f.id
        f = f.parent
    return None


def _base(name: str) -> str:
    """'ARR[3]' -> 'ARR' (array element reference); other names unchanged."""
    if name and name.endswith("]") and "[" in name:
        return name[:name.index("[")]
    return name


def _clear(el):
    el.clear()
    par = el.getparent()
    if par is not None:
        while el.getprevious() is not None:
            del par[0]


def parse_program_file(conn, ctrl: str, path: Path, root: Path, ids: _Ids, vardict: dict, stats: dict,
                       batches: dict, log=print) -> None:
    rel = rel_to_root(path, root)
    prog_id = ids.next("program")
    prog_name = prog_lib = prog_help = ""
    n_block_file = n_zk_file = n_task = 0
    stack = []                  # open frames
    frames = []                 # all frames of this file (kept for resolution)
    pins = []                   # [pin_id, owner_frame, name, connection, address, value, alias, alias_override, usage, desc, line]
    tasks = []
    local_vars = []

    for ev, el in etree.iterparse(str(path), events=("start", "end"), huge_tree=True):
        tag = el.tag
        if ev == "start":
            if tag in SCOPE_TAGS:
                name = el.get("Name", "")
                parent = stack[-1] if stack else None
                if tag in TASK_TAGS:
                    tid = ids.next("task")
                    fr = _Frame(tag, ids.next("block"), name, f"{prog_name}/{name}", None, tid, el.sourceline, el.attrib)
                    n_task += 1
                    if tag == "FFTask":
                        stats["fftasks"] += 1
                else:
                    if parent is None:      # Block/UserBlock outside any task (not seen in the checkout, be safe)
                        fr = _Frame(tag, ids.next("block"), name, f"{prog_name}/{name}", None, None, el.sourceline, el.attrib)
                    else:
                        fr = _Frame(tag, ids.next("block"), name, f"{parent.path}/{name}", parent, parent.task_id,
                                    el.sourceline, el.attrib)
                        parent.names[name] = fr.id
                        if tag == "Block":
                            parent.n_block += 1
                        else:
                            parent.n_ub += 1
                    if tag == "Block":
                        n_block_file += 1
                stack.append(fr)
                frames.append(fr)
            elif el.getparent() is None:    # root <Program>
                prog_name = el.get("Name", "")
                prog_lib = el.get("LibraryType", "")
                prog_help = el.get("LocalHelpFile", "")
            continue

        # ---- end events
        if tag == "Pin":
            par = el.getparent()
            if stack and par is not None and par.tag in SCOPE_TAGS:
                fr = stack[-1]
                fr.n_pin += 1
                a = el.attrib
                ln = a.get("LibName")       # template pin name ('{Device}', '{Device}{Type}', ...): the instance name is expanded per block
                pins.append([ids.next("pin"), fr, a.get("Name", ""), a.get("Connection"), a.get("Address"),
                             a.get("Value"), a.get("Alias"), a.get("AliasOverride"), a.get("Usage"),
                             a.get("Description"), el.sourceline, ln if ln and "{" in ln else None])
            _clear(el)
        elif tag in AUTO_PINS:
            if stack:
                fr = stack[-1]
                a = el.attrib
                pins.append([ids.next("pin"), fr, a.get("Name", "_" + tag), a.get("Connection"), a.get("Address"),
                             a.get("Value"), a.get("Alias"), a.get("AliasOverride"), a.get("Usage") or AUTO_PINS[tag],
                             a.get("Description"), el.sourceline, None])
            _clear(el)
        elif tag == "Attribute":
            par = el.getparent()
            if stack and par is not None and par.tag in SCOPE_TAGS:
                fr = stack[-1]
                an, av = el.get("Name", ""), el.get("Value")
                col = LIFTED_ATTRS.get(an)
                if col:
                    fr.lifted[col] = av
                else:
                    fr.attrs.append((an, av))
            _clear(el)
        elif tag == ZK_TAG:
            n_zk_file += 1
            if stack:
                stack[-1].n_zk += 1
            _clear(el)
        elif tag == "Variable":
            par = el.getparent()
            if par is not None and par.tag == "Program":
                name = el.get("Name", "")
                if name and name not in vardict:
                    vid = ids.next("variable")
                    row = list(row_from_attrib(ctrl, el.attrib, rel, el.sourceline, 1))
                    row[9] = prog_name            # decl_program (no Connection attr on program-local decls)
                    local_vars.append((vid, row))
                    vardict[name] = vid
            _clear(el)
        elif tag in SCOPE_TAGS:
            fr = stack.pop()
            a = fr.a
            if fr.kind == "task":
                ld = fr.lifted.get("logic_drg")
                tasks.append((fr.task_id, prog_id, fr.name, a.get("BlockType"), ld,
                              1 if a.get("BlockTypeIsTask", "").lower() == "true" else 0, fr.line))
            desc = fr.lifted.get("description")
            if desc is None:
                desc = a.get("Description") if fr.kind == "block" else a.get("Desc")
            opaque = 1 if (fr.kind == "userblock" and fr.n_block == 0 and fr.n_pin == 0 and fr.n_ub == 0
                           and fr.n_zk > 0) else 0
            batches["block"].add((fr.id, ctrl, prog_id, fr.task_id, fr.parent.id if fr.parent else None, fr.path,
                                  fr.name, a.get("BlockType"), fr.kind, a.get("Version"), opaque, desc,
                                  fr.lifted.get("logic_drg"), fr.lifted.get("p_id"), fr.lifted.get("device"),
                                  fr.lifted.get("hmi_linked_object"), fr.line, _layout(a.get("BlockLayoutData"))))
            for an, av in fr.attrs:
                batches["attr"].add((fr.id, an, av))
            stats["attrs"] += len(fr.attrs)
            stats["opaque"] += opaque
            _clear(el)
        elif tag in CLEAR_TAGS:
            _clear(el)

    # ---- resolve connections (whole file is known now) and insert
    seen = set()
    for pid, fr, name, connection, address, value, alias, alias_ov, usage, desc, line, lib_name in pins:
        key = (fr.id, name)
        if key in seen:             # UNIQUE(block_id,name); not observed in the checkout
            continue
        seen.add(key)
        kind, x, y = classify(connection, address)
        var_id = tgt = None
        if kind == "V":
            var_id = vardict.get(connection)
            if var_id is None and connection.endswith("]"):
                var_id = vardict.get(_base(connection))
            if var_id is None:
                if "." in connection:
                    kind = "D"
                else:
                    stats["unres_V"] += 1
        elif kind == "L":
            y = _base(y)
            tgt = _find_block(fr.parent, x) if fr.parent is not None else None
            if tgt is None and fr.kind != "block":
                tgt = fr.names.get(x)
            if tgt is None:
                stats["unres_L"] += 1
        elif kind == "P":
            y = _base(y)
            tgt = fr.parent.id if fr.parent is not None else None
            if tgt is None:
                stats["unres_P"] += 1
        batches["pin"].add((pid, fr.id, name, kind, connection, var_id, tgt, y, address, value, alias,
                            1 if (alias_ov or "").lower() == "true" else (0 if alias_ov else None),
                            usage, desc, line, lib_name))
    encrypted = 1 if (n_block_file == 0 and n_zk_file >= 1) else 0
    conn.execute(INSERT_PROGRAM, (prog_id, ctrl, prog_name, prog_lib, rel, encrypted, n_block_file, n_task, prog_help))
    for t in tasks:
        batches["task"].add(t)
    for vid, row in local_vars:
        batches["var"].add(tuple([vid] + row))
    for b in batches.values():
        b.flush()
    conn.commit()
    stats["programs"] += 1
    stats["tasks"] += n_task
    stats["blocks"] += n_block_file
    stats["userblocks"] += sum(1 for f in frames if f.kind == "userblock")
    stats["pins"] += len(seen)
    stats["encrypted"] += encrypted
    stats["zk"] += n_zk_file
    stats["local_vars"] += len(local_vars)


def delete_controller(conn, ctrl: str) -> None:
    conn.execute("DELETE FROM pin WHERE block_id IN (SELECT id FROM block WHERE ctrl=?)", (ctrl,))
    conn.execute("DELETE FROM block_attr WHERE block_id IN (SELECT id FROM block WHERE ctrl=?)", (ctrl,))
    conn.execute("DELETE FROM block WHERE ctrl=?", (ctrl,))
    conn.execute("DELETE FROM task WHERE program_id IN (SELECT id FROM program WHERE ctrl=?)", (ctrl,))
    conn.execute("DELETE FROM program WHERE ctrl=?", (ctrl,))
    conn.execute("DELETE FROM variable WHERE ctrl=? AND is_program_local=1", (ctrl,))
    conn.commit()


def run(conn, root: Path, ctrls: Iterable[Controller], log=print) -> dict:
    """Parse every <CTRL>/_*.xml Programme file. Deletes that controller's logic rows (and local variables) first."""
    ids = _Ids(conn)
    all_stats = {}
    t_all = time.time()
    for c in ctrls:
        t0 = time.time()
        delete_controller(conn, c.name)
        vardict = var_index(conn, c.name)
        stats = {k: 0 for k in STAT_KEYS}
        batches = {"task": Batch(conn, INSERT_TASK), "block": Batch(conn, INSERT_BLOCK),
                   "attr": Batch(conn, INSERT_ATTR), "pin": Batch(conn, INSERT_PIN),
                   "var": Batch(conn, INSERT_LOCAL_VAR)}
        for p in sorted(c.folder.glob("_*.xml")):
            try:
                if "Programme" not in head_info(p)["class"]:
                    stats["skipped_files"] += 1
                    log(f"    skip (not a Programme file): {rel_to_root(p, root)}")
                    continue
                parse_program_file(conn, c.name, p, root, ids, vardict, stats, batches, log)
            except etree.XMLSyntaxError as e:
                stats["skipped_files"] += 1
                log(f"    XML error in {rel_to_root(p, root)}: {e}")
        conn.commit()
        s = stats
        log(f"  logic {c.name:7s} prog {s['programs']:3d} (enc {s['encrypted']:2d})  task {s['tasks']:4d}"
            f"{' (+ff ' + str(s['fftasks']) + ')' if s['fftasks'] else ''}  block {s['blocks']:6d}  ub {s['userblocks']:5d}"
            f" (opaque {s['opaque']})  pin {s['pins']:7d}  attr {s['attrs']:6d}  localvar {s['local_vars']:5d}"
            f"  unresolved V/L/P {s['unres_V']}/{s['unres_L']}/{s['unres_P']}  zk {s['zk']}  {time.time()-t0:5.1f}s")
        all_stats[c.name] = stats
    log(f"  logic total {time.time()-t_all:.0f}s")
    return all_stats
