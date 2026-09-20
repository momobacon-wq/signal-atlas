# -*- coding: utf-8 -*-
"""Read-only queries over the dcdas SQLite index. Every function returns a plain, JSON-serialisable dict with a
top-level 'kind' key; render.py turns it into fixed-width text for Claude / engineers.

Signal resolution (resolve_signal): 'CTRL.NAME' = variable.full_name exact; bare NAME -> variable.name, used if
it exists in exactly one controller, else kind='ambiguous' listing the candidates; then case-insensitive
full_name/name, alias, io_point.device_tag, egd consumed copies ('<producer>.<name>' in a consumer controller).

Reference notation everywhere:  CTRL/Program/Task/.../Block.Pin [BlockType] dir/src file:line
(block.path is 'Program/Task/UB/Block'; program.file_path is relative to the checkout root).
"""
import re
import sqlite3
from collections import OrderedDict

SECTION_CAP = 400          # hard cap per section returned to the caller (render cuts at 40 unless --all)
CONNECTED = ("V", "L", "P", "D")
DCS_PAGES = ("TURB_DCS", "MIS")
_WILD = re.compile(r"[*?%\[]")


# ------------------------------------------------------------------------------------------------- helpers
def _rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]


def _row(conn, sql, args=()):
    r = _rows(conn, sql, args)
    return r[0] if r else None


def _fl(file, line):
    """'file:line' or '' - the address Claude uses with Read --offset."""
    if not file:
        return ""
    return f"{file}:{line}" if line else file


def _ds(direction, source):
    return f"{direction or '?'}/{source or '-'}"


_PIN_SQL = """SELECT p.id, p.name AS pin, p.direction, p.dir_source, p.conn_kind, p.connection, p.var_id,
                     p.tgt_block_id, p.tgt_pin, p.address, p.value, p.alias, p.usage_declared, p.line_no,
                     b.id AS block_id, b.ctrl, b.path, b.name AS block_name, coalesce(b.block_type,'') AS block_type,
                     b.kind, b.is_opaque, b.logic_drg, b.p_id, b.task_id, b.program_id,
                     pr.name AS program, pr.file_path, pr.encrypted
              FROM pin p JOIN block b ON b.id=p.block_id JOIN program pr ON pr.id=b.program_id """


def _pin_ref(p):
    """One-line reference for a pin row from _PIN_SQL."""
    return {"ref": f"{p['ctrl']}/{p['path']}.{p['pin']}", "block_type": p["block_type"],
            "dir": _ds(p["direction"], p["dir_source"]), "at": _fl(p["file_path"], p["line_no"]),
            "block_id": p["block_id"], "pin_id": p["id"], "kind": p["kind"], "opaque": p["is_opaque"],
            "note": "(variable declared at this pin)" if (p.get("decl") or (p["conn_kind"] == "A" and p["var_id"] is not None))
                    else (f"(via interface pin {p['via']})" if p.get("via") else "")}


def _pins_of_var(conn, var_id, directions=None):
    sql = _PIN_SQL + "WHERE p.var_id=?"
    args = [var_id]
    if directions:
        sql += " AND p.direction IN (%s)" % ",".join("?" * len(directions))
        args += list(directions)
    sql += " ORDER BY b.ctrl, b.path, p.name"
    return _rows(conn, sql, args)


def _pins_of_block(conn, block_id):
    return _rows(conn, _PIN_SQL + "WHERE p.block_id=? ORDER BY p.direction, p.name", (block_id,))


def _decl_pin(conn, v):
    """The pin at which variable v is DECLARED (variable.decl_connection = 'Program.Task[.Block..].Pin').
    the configuration tool publishes a block/interface pin as a global variable; such pins carry only an Address (conn_kind 'A')
    or a different Connection, so pin.var_id never points back at v (59,229 of 96,505 variables in G11/BOPE1/WSC1).
    Names may contain dots, so every split point is tried against block(ctrl,path)+pin(name)."""
    decl = v["decl_connection"] or ""
    parts = decl.split(".")
    if len(parts) < 3:
        return None
    for i in range(2, len(parts)):
        path, pin = "/".join(parts[:i]), ".".join(parts[i:])
        r = _row(conn, _PIN_SQL + "WHERE b.ctrl=? AND b.path=? AND p.name=?", (v["ctrl"], path, pin))
        if r:
            return r
    return None


def _var_at_pin(conn, p):
    """Reverse of _decl_pin: the variable declared at pin row p (None if the pin publishes no variable)."""
    decl = p["path"].replace("/", ".") + "." + p["pin"]
    if p["address"]:
        r = _row(conn, "SELECT * FROM variable WHERE ctrl=? AND address=? AND decl_connection=?", (p["ctrl"], p["address"], decl))
        if r:
            return r
    return _row(conn, "SELECT * FROM variable WHERE ctrl=? AND decl_connection=? AND decl_program=?",
                (p["ctrl"], decl, p["program"]))


def _var_pins(conn, v, directions=None):
    """All pins that carry variable v: pins with var_id=v.id, plus
       * the declaring pin when it is an ordinary block pin published as a variable (flag decl=True), or
       * when the declaring pin is a task/macro INTERFACE pin (the pin IS the variable, not a writer of it):
         the inner pins wired to that interface pin through 'L:<pin>' (conn_kind P), flag via=<interface ref>."""
    pins = _pins_of_var(conn, v["id"], directions)
    for q in pins:
        q["decl"] = False
        q["via"] = ""
    dp = _decl_pin(conn, v)
    if dp and dp["var_id"] != v["id"] and all(dp["id"] != q["id"] for q in pins):
        if dp["kind"] == "block":
            if not directions or dp["direction"] in directions:
                dp["decl"], dp["via"] = True, ""
                pins.append(dp)
        else:
            for q in _inner_pins(conn, dp["block_id"], dp["pin"], directions or ("I", "O", "S", "?")):
                if all(q["id"] != x["id"] for x in pins):
                    q["decl"], q["via"] = False, f"{dp['ctrl']}/{dp['path']}.{dp['pin']}"
                    pins.append(q)
    return pins


def _interface_info(conn, v):
    """If v is declared at a task/macro interface pin: {ref, usage, dir, at} else None."""
    dp = _decl_pin(conn, v)
    if dp and dp["kind"] != "block":
        return {"ref": f"{dp['ctrl']}/{dp['path']}.{dp['pin']}", "usage": dp["usage_declared"],
                "dir": _ds(dp["direction"], dp["dir_source"]), "at": _fl(dp["file_path"], dp["line_no"]),
                "block_kind": dp["kind"], "opaque": dp["is_opaque"]}
    return None


def _inner_pins(conn, block_id, pin_name, directions):
    """Pins INSIDE macro/task `block_id` wired to its interface pin `pin_name` through 'L:<pin>' (conn_kind P)."""
    return _rows(conn, _PIN_SQL + "WHERE p.tgt_block_id=? AND p.tgt_pin=? AND p.conn_kind='P' AND p.direction IN (%s) "
                 "ORDER BY b.path, p.name" % ",".join("?" * len(directions)), (block_id, pin_name, *directions))


def _block_by_id(conn, block_id):
    return _row(conn, """SELECT b.*, pr.name AS program, pr.file_path, pr.encrypted FROM block b
                         JOIN program pr ON pr.id=b.program_id WHERE b.id=?""", (block_id,))


def _var_by_id(conn, var_id):
    return _row(conn, "SELECT * FROM variable WHERE id=?", (var_id,))


def _full_names(conn, ids):
    ids = [i for i in set(ids) if i is not None]
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for vid, fn in conn.execute("SELECT id,full_name FROM variable WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            out[vid] = fn
    return out


def _conn_text(conn, p, names=None):
    """Describe what a pin is wired to: variable full_name, L:/P target, constant, address."""
    k = p["conn_kind"]
    if k == "V" and p["var_id"]:
        fn = (names or {}).get(p["var_id"]) or (_var_by_id(conn, p["var_id"]) or {}).get("full_name")
        return fn or p["connection"]
    if k in ("L", "P"):
        if p["tgt_block_id"]:
            tb = _block_by_id(conn, p["tgt_block_id"])
            if tb:
                return f"{p['connection']} = {tb['ctrl']}/{tb['path']}.{p['tgt_pin']} [{tb['block_type'] or tb['kind']}]"
        return p["connection"] or ""
    if k in ("N", "E"):
        return p["connection"] or ""
    if k == "D":
        return f"{p['connection']} (device pin, unresolved)"
    if k == "A":
        dv = _var_at_pin(conn, p)
        if dv:
            return f"{dv['full_name']} (declared at this pin)"
        return f"addr {p['address']}"
    return p["connection"] or ""


def _controllers(conn):
    return [r[0] for r in conn.execute("SELECT name FROM controller ORDER BY name")]


def _producer_status(conn, producer_ctrl):
    """'producer outside checkout' (no controller folder) or 'producer not indexed in this DB' (folder exists,
    but no egd_exchange rows for it - e.g. a --ctrl subset build)."""
    if conn.execute("SELECT 1 FROM egd_exchange WHERE producer_ctrl=? LIMIT 1", (producer_ctrl,)).fetchone():
        return "producer indexed"
    if conn.execute("SELECT 1 FROM controller WHERE name=?", (producer_ctrl,)).fetchone():
        return "producer not indexed in this DB"
    return "producer outside checkout"


# -------------------------------------------------------------------------------------- signal resolution
def resolve_signal(conn, key):
    """-> (variable row or None, result dict or None). The dict is returned when the key is ambiguous/unknown."""
    key = (key or "").strip()
    if not key:
        return None, {"kind": "notfound", "key": key, "hint": "empty signal name"}
    v = _row(conn, "SELECT * FROM variable WHERE full_name=?", (key,))
    if v:
        return v, None
    cands = _rows(conn, "SELECT * FROM variable WHERE name=? ORDER BY ctrl", (key,))
    if len(cands) == 1:
        return cands[0], None
    if len(cands) > 1:
        return None, _ambiguous(key, cands)
    # case-insensitive
    cands = _rows(conn, "SELECT * FROM variable WHERE full_name=? COLLATE NOCASE ORDER BY ctrl", (key,))
    if len(cands) == 1:
        return cands[0], None
    cands = _rows(conn, "SELECT * FROM variable WHERE name=? COLLATE NOCASE ORDER BY ctrl", (key,))
    if len(cands) == 1:
        return cands[0], None
    if len(cands) > 1:
        return None, _ambiguous(key, cands)
    # alias
    cands = _rows(conn, "SELECT * FROM variable WHERE alias=? COLLATE NOCASE ORDER BY ctrl", (key,))
    if len(cands) == 1:
        return cands[0], None
    if len(cands) > 1:
        return None, _ambiguous(key, cands, "alias")
    # CTRL.alias
    if "." in key:
        c, _, rest = key.partition(".")
        cands = _rows(conn, "SELECT * FROM variable WHERE ctrl=? AND alias=? COLLATE NOCASE", (c, rest))
        if len(cands) == 1:
            return cands[0], None
    # io device tag
    ids = [r[0] for r in conn.execute("SELECT DISTINCT var_id FROM io_point WHERE device_tag=? COLLATE NOCASE AND var_id IS NOT NULL", (key,))]
    if len(ids) == 1:
        return _var_by_id(conn, ids[0]), None
    if len(ids) > 1:
        cands = [_var_by_id(conn, i) for i in ids]
        return None, _ambiguous(key, cands, "io device_tag")
    # array element 'NAME[3]' -> base
    if key.endswith("]") and "[" in key:
        return resolve_signal(conn, key[:key.index("[")])
    return None, {"kind": "notfound", "key": key,
                  "hint": "not a variable full_name/name/alias/device_tag; try: find <pattern>"}


def _ambiguous(key, cands, how="name"):
    return {"kind": "ambiguous", "key": key, "matched_by": how,
            "candidates": [{"full_name": c["full_name"], "datatype": c["datatype"], "description": c["description"]}
                           for c in cands[:SECTION_CAP]],
            "hint": "use CTRL.NAME"}


# -------------------------------------------------------------------------------------------------- show
def _def_section(conn, v):
    fs = None
    if v["format_spec"]:
        fs = _row(conn, "SELECT name,units,low,high,decimals FROM format_spec WHERE name=?", (v["format_spec"],))
    units = v["units"] or (fs or {}).get("units")
    lo = v["disp_low"] if v["disp_low"] is not None else (fs or {}).get("low")
    hi = v["disp_high"] if v["disp_high"] is not None else (fs or {}).get("high")
    producer = None
    if v["producer_var_id"]:
        pv = _var_by_id(conn, v["producer_var_id"])
        producer = pv["full_name"] if pv else None
    d = OrderedDict()
    d["full_name"] = v["full_name"]
    d["ctrl"] = v["ctrl"]
    d["name"] = v["name"]
    d["description"] = v["description"]
    d["datatype"] = v["datatype"]
    d["address"] = v["address"]
    d["value"] = v["value"]
    d["scope"] = v["scope"]
    d["egd_page"] = v["egd_page"]
    d["alias"] = v["alias"]
    d["format_spec"] = v["format_spec"]
    d["units"] = units
    d["disp_low"] = lo
    d["disp_high"] = hi
    d["control_constant"] = v["control_constant"]
    d["is_program_local"] = v["is_program_local"]
    d["decl"] = {"program": v["decl_program"], "task": v["decl_task"], "connection": v["decl_connection"],
                 "at": _fl(v["decl_file"], v["decl_line"])}
    d["referenced_in"] = [x for x in (v["referenced_in"] or "").split(",") if x]
    d["device_name"] = v["device_name"]
    d["producer"] = producer
    d["display_screen"] = v["display_screen"]
    return d


def _writer_entry(conn, p, names):
    e = _pin_ref(p)
    ins = []
    if p["kind"] != "block":
        for q in _inner_pins(conn, p["block_id"], p["pin"], ("O",)):
            ins.append({"pin": f"inner {q['path'].split('/')[-1]}.{q['pin']}", "dir": _ds(q["direction"], q["dir_source"]),
                        "conn_kind": "P", "to": f"[{q['block_type'] or q['kind']}] writes this interface pin {_fl(q['file_path'], q['line_no'])}"})
    for q in _pins_of_block(conn, p["block_id"]):
        if q["id"] == p["id"] or q["direction"] == "O" or q["conn_kind"] in ("A", "-"):
            continue
        ins.append({"pin": q["pin"], "dir": _ds(q["direction"], q["dir_source"]), "conn_kind": q["conn_kind"],
                    "to": _conn_text(conn, q, names)})
    e["inputs"] = ins[:8]
    e["inputs_more"] = max(0, len(ins) - 8)
    return e


def _io_rows(conn, var_id=None, where="", args=()):
    sql = """SELECT p.id, p.ctrl, p.name AS point, p.direction, p.signal_type, p.connection, p.var_id, p.device_tag,
                    p.address, p.input_type, p.low_value, p.high_value, p.line_no,
                    m.name AS module, m.cabinet, m.module_id AS module_no, b.name AS board, b.hw_form, b.position_r
             FROM io_point p LEFT JOIN io_module m ON m.id=p.module_id LEFT JOIN io_board b ON b.id=p.board_id """
    if var_id is not None:
        sql += "WHERE p.var_id=? "
        args = (var_id,)
    else:
        sql += where + " "
    sql += "ORDER BY p.ctrl, m.cabinet, m.name, b.name, p.name LIMIT %d" % SECTION_CAP
    out = []
    for r in _rows(conn, sql, args):
        r["screws"] = [f"{s[0]}={s[1]}" + (f" cable {s[2]}" if s[2] else "") + (f" wire {s[3]}" if s[3] else "")
                       + (f" via {s[4]}" if s[4] else "") + (f" ({s[5]})" if s[5] else "")
                       for s in conn.execute("""SELECT name,number,cable_number,wire_number,interposing_tb,note
                                                FROM io_screw WHERE point_id=? ORDER BY number""", (r["id"],))]
        r["at"] = _fl(f"{r['ctrl']}/DistributedIO.Xml", r["line_no"])
        out.append(r)
    return out


def _egd_section(conn, v):
    vid = v["id"]
    produced = _rows(conn, """SELECT x.producer_ctrl, x.exchange_id, x.page, p.voffs, p.dtype, p.address, x.period_ns
                              FROM egd_produced p JOIN egd_exchange x ON x.id=p.exchange_pk
                              WHERE p.var_id=? ORDER BY x.exchange_id""", (vid,))
    consumers = _rows(conn, """SELECT c.consumer_ctrl, c.producer_ctrl||'.'||c.var_name AS local_name, c.exchange_id,
                                      c.page, c.voffs, c.match_method, c.local_address, c.local_var_id
                               FROM egd_consumed c WHERE c.producer_var_id=? ORDER BY c.consumer_ctrl, c.exchange_id""", (vid,))
    source = _rows(conn, """SELECT c.producer_ctrl, c.var_name, c.exchange_id, c.page, c.voffs, c.match_method,
                                   c.producer_var_id, c.local_address
                            FROM egd_consumed c WHERE c.local_var_id=? ORDER BY c.exchange_id""", (vid,))
    for s in source:
        s["producer_full_name"] = f"{s['producer_ctrl']}.{s['var_name']}"
        s["producer_in_checkout"] = s["producer_var_id"] is not None
        s["producer_status"] = "producer indexed" if s["producer_var_id"] else _producer_status(conn, s["producer_ctrl"])
    note = None
    if produced and not consumers:
        pages = sorted({p["page"] or "" for p in produced})
        if any((pg in DCS_PAGES or not pg) for pg in pages):
            note = f"consumer outside checkout (page {','.join(pages)}; TURB_DCS/MIS = DCS export)"
        else:
            note = f"no consumer rows in checkout (page {','.join(pages)})"
    return {"produced": produced, "consumers": consumers, "source": source, "note": note}


def _hmi_section(conn, v):
    menu = {}
    for r in conn.execute("SELECT screen, block, menu, submenu, item FROM hmi_menu"):
        menu.setdefault((r[0] or "").lower(), []).append(" / ".join(x for x in r[1:] if x))
    rows = _rows(conn, "SELECT full_point, screen, source, unit_prefix FROM hmi_point WHERE var_id=? ORDER BY screen, source", (v["id"],))
    seen = OrderedDict()
    for r in rows:
        s = r["screen"]
        e = seen.setdefault(s, {"screen": s, "sources": [], "full_point": r["full_point"],
                                "menu": menu.get((s or "").lower(), [None])[0]})
        if r["source"] not in e["sources"]:
            e["sources"].append(r["source"])
    return list(seen.values())


def _alarm_section(v):
    if not (v["alarm_id"] or v["alarm_class"] or v["alarm_definition"]):
        return None
    return {"alarm_id": v["alarm_id"], "class": v["alarm_class"], "definition": v["alarm_definition"],
            "plant_area": v["plant_area"], "potential_causes": v["potential_causes"],
            "operator_action": v["operator_action"], "consequence": v["consequence"], "urgency": v["urgency"],
            "normal_severity": v["normal_severity"], "active_severity": v["active_severity"]}


def _mirror_info(conn, vid):
    """If variable `vid` is the published value of an already-wired pin, describe that pin and its wiring source."""
    m = _row(conn, "SELECT pin_id, kind FROM pin_mirror WHERE var_id=?", (vid,))
    if not m:
        return None
    p = _row(conn, _PIN_SQL + "WHERE p.id=?", (m["pin_id"],))
    if not p:
        return None
    info = {"kind": m["kind"], "pin": _pin_ref(p), "conn_kind": p["conn_kind"], "src": None, "src_text": ""}
    if not info["pin"]["block_type"]:
        info["pin"]["block_type"] = p["kind"]      # task / userblock interface pins have no block type
    k = p["conn_kind"]
    if k == "V" and p["var_id"]:
        sv = _var_by_id(conn, p["var_id"])
        if sv:
            info["src"] = {"k": "V", "var_id": sv["id"], "full_name": sv["full_name"]}
            info["src_text"] = sv["full_name"]
            sw = _var_pins(conn, sv, ("O",))
            if sw:
                info["src_text"] += f"  (written by {sw[0]['ctrl']}/{sw[0]['path']}.{sw[0]['pin']} [{sw[0]['block_type'] or sw[0]['kind']}]" + (f" +{len(sw)-1} more" if len(sw) > 1 else "") + ")"
    elif k in ("L", "P") and p["tgt_block_id"]:
        tb = _block_by_id(conn, p["tgt_block_id"])
        if tb:
            info["src"] = {"k": k, "block_id": tb["id"], "ref": f"{tb['ctrl']}/{tb['path']}.{p['tgt_pin']}", "pin": p["tgt_pin"]}
            info["src_text"] = f"{p['connection']} = {tb['ctrl']}/{tb['path']}.{p['tgt_pin']} [{tb['block_type'] or tb['kind']}]"
    elif k in ("N", "E"):
        info["src"] = {"k": k, "text": p["connection"]}
        info["src_text"] = f"{p['connection']} [const]"
    return info


def show(conn, signal, all_rows=False):
    v, err = resolve_signal(conn, signal)
    if err:
        return err
    vid = v["id"]
    mirror = _mirror_info(conn, vid)
    pins = _var_pins(conn, v)
    iface = _interface_info(conn, v)
    names = _full_names(conn, [p["var_id"] for p in pins])
    writers = [p for p in pins if p["direction"] == "O"]
    readers = [p for p in pins if p["direction"] in ("I", "S")]
    unknown = [p for p in pins if p["direction"] not in ("I", "O", "S")]
    w_entries = [_writer_entry(conn, p, names) for p in writers[:SECTION_CAP]]
    r_entries = []
    for p in readers[:SECTION_CAP]:
        e = _pin_ref(p)
        parts = p["path"].split("/")
        e["group"] = f"{p['ctrl']}/{'/'.join(parts[:2])}"
        r_entries.append(e)
    u_entries = [_pin_ref(p) for p in unknown[:SECTION_CAP]]
    io_rows = _io_rows(conn, var_id=vid)
    egd = _egd_section(conn, v)
    hmi = _hmi_section(conn, v)
    alarm = _alarm_section(v)
    if alarm and alarm["class"]:
        ac = _row(conn, "SELECT description, priority FROM alarm_class WHERE name=?", (alarm["class"],))
        if ac:
            alarm["class_description"] = ac["description"]
            alarm["class_priority"] = ac["priority"]
    watch = _rows(conn, "SELECT ctrl, watch_file, datasource FROM watch WHERE var_id=? ORDER BY ctrl, watch_file", (vid,))
    drg = sorted({(p["logic_drg"] or "", p["p_id"] or "") for p in pins if (p["logic_drg"] or p["p_id"])})
    if not drg:   # fall back to the task's LogicDrg
        drg = sorted({(r[0] or "", "") for r in conn.execute(
            """SELECT DISTINCT t.logic_drg FROM pin p JOIN block b ON b.id=p.block_id JOIN task t ON t.id=b.task_id
               WHERE p.var_id=? AND t.logic_drg IS NOT NULL AND t.logic_drg<>''""", (vid,))})
    refd = [x for x in (v["referenced_in"] or "").split(",") if x]
    enc = []
    if refd:
        encset = {r[0] for r in conn.execute("SELECT name FROM program WHERE ctrl=? AND encrypted=1", (v["ctrl"],))}
        enc = [p for p in refd if p in encset]
    # a published pin value: input pin -> source is the pin's wiring; output pin -> the block writes it
    if not writers and mirror and mirror["kind"] == "I" and mirror["src"]:
        w_entries.insert(0, {"ref": mirror["pin"]["ref"], "block_type": mirror["pin"]["block_type"], "dir": mirror["pin"]["dir"],
                             "at": mirror["pin"]["at"], "inputs": [{"pin": mirror["pin"]["ref"].rsplit(".", 1)[-1], "dir": "I",
                             "conn_kind": mirror["conn_kind"], "to": mirror["src_text"]}], "inputs_more": 0,
                             "note": "(published value of this INPUT pin; its wiring is the source)"})
    elif not writers and mirror and mirror["kind"] == "O":
        w_entries.insert(0, {"ref": mirror["pin"]["ref"], "block_type": mirror["pin"]["block_type"], "dir": mirror["pin"]["dir"],
                             "at": mirror["pin"]["at"], "inputs": [], "inputs_more": 0, "note": "(published value of this OUTPUT pin)"})
    # source-of-truth summary
    if not writers and mirror and mirror["kind"] == "I" and mirror["src"]:
        source = f"value of pin {mirror['pin']['ref']} [{mirror['pin']['block_type']}] <- {mirror['src_text']}"
    elif not writers and mirror and mirror["kind"] == "O":
        source = f"logic (output pin value): {mirror['pin']['ref']} [{mirror['pin']['block_type']}]"
    elif writers:
        source = f"logic: {w_entries[0]['ref']}" + (f" (+{len(writers)-1} more writers)" if len(writers) > 1 else "")
    elif iface and (iface["usage"] or "").lower() == "output":
        source = f"interface Output pin {iface['ref']} (no inner writer found" + ("; opaque macro" if iface["opaque"] else "") + ")"
    elif egd["source"]:
        s = egd["source"][0]
        source = f"EGD from {s['producer_full_name']} (exchange {s['exchange_id']} voffs {s['voffs']} match {s['match_method']})"
    elif any(r["direction"] == "I" for r in io_rows):
        r = next(r for r in io_rows if r["direction"] == "I")
        source = f"field I/O ({r['ctrl']} {r['module']} {r['board'] or ''} {r['point']} tag {r['device_tag'] or '-'})"
    elif enc:
        source = f"not traceable: referenced only in encrypted program(s) {', '.join(enc)}"
    elif unknown:
        source = f"unknown: {len(unknown)} pin(s) with direction '?' (see UNKNOWN-DIR)"
    elif v["control_constant"]:
        source = f"control constant (value {v['value']})"
    else:
        source = "no writer found in checkout"
    d = _def_section(conn, v)
    d["interface"] = iface
    return {"kind": "show", "def": d, "source": source,
            "writers": w_entries, "writers_total": max(len(writers), len(w_entries)), "mirror": mirror,
            "readers": r_entries, "readers_total": len(readers),
            "unknown": u_entries, "unknown_total": len(unknown),
            "io": io_rows, "egd": egd, "hmi": hmi, "alarm": alarm, "watch": watch,
            "drg": [{"logic_drg": a, "p_id": b} for a, b in drg], "encrypted": enc}


# -------------------------------------------------------------------------------------------------- trace
class _Trace:
    def __init__(self, conn, max_lines):
        self.conn, self.budget, self.lines, self.exhausted = conn, max_lines, [], False
        self.seen_var, self.seen_block = set(), set()
        self.names = {}

    def emit(self, d, text):
        if self.budget <= 0:
            if not self.exhausted:
                self.lines.append({"d": d, "text": "... budget exhausted (--max-lines)"})
                self.exhausted = True
            return False
        self.lines.append({"d": d, "text": text})
        self.budget -= 1
        return True

    def vname(self, vid):
        if vid not in self.names:
            v = _var_by_id(self.conn, vid)
            self.names[vid] = v["full_name"] if v else f"var#{vid}"
        return self.names[vid]

    def io_line(self, vid, direction):
        rows = [r for r in _io_rows(self.conn, var_id=vid) if r["direction"] == direction]
        out = []
        for r in rows:
            out.append(f"field I/O {r['ctrl']} {r['module'] or ''}/{r['board'] or ''} {r['point']} tag {r['device_tag'] or '-'} "
                       f"{r['direction']} ({r['at']})")
        return out

    # ---- upstream
    def up_var(self, vid, d, level, up, head=None):
        conn = self.conn
        v = _var_by_id(conn, vid)
        if not v:
            return
        writers = _var_pins(conn, v, ("O",))
        tag = f"{v['full_name']} [{v['datatype'] or '?'}]"
        if head is not None:
            tag = f"{head} {tag}"
        if len(writers) > 1:
            tag += f" [multi-writer {len(writers)}]"
        if vid in self.seen_var:
            self.emit(d, tag + " (seen)")
            return
        self.seen_var.add(vid)
        if not self.emit(d, tag):
            return
        if level > up:
            return
        if not writers:
            mir = _mirror_info(conn, vid)
            if mir and mir["kind"] == "I" and mir["src"]:
                if not self.emit(d + 1, f"<= value of pin {mir['pin']['ref']} [{mir['pin']['block_type']}] wired to {mir['conn_kind']}: {mir['src_text'].split('  (')[0]} ({mir['pin']['at']})"):
                    return
                sk = mir["src"]["k"]
                if sk == "V":
                    self.up_var(mir["src"]["var_id"], d + 2, level + 1, up)
                elif sk in ("L", "P"):
                    self.up_block(mir["src"]["block_id"], d + 2, level + 1, up)
                return
            if mir and mir["kind"] == "O":
                self.emit(d + 1, f"<= output pin value {mir['pin']['ref']} [{mir['pin']['block_type']}] ({mir['pin']['at']})")
                self.up_block(mir["pin"]["block_id"], d + 2, level + 1, up)
                return
            for s in _rows(conn, "SELECT producer_ctrl,var_name,exchange_id,voffs,match_method,producer_var_id FROM egd_consumed WHERE local_var_id=?", (vid,)):
                pf = f"{s['producer_ctrl']}.{s['var_name']}"
                if s["producer_var_id"]:
                    self.up_var(s["producer_var_id"], d + 1, level + 1, up,
                                head=f"<= EGD exch {s['exchange_id']} voffs {s['voffs']} match {s['match_method']}")
                else:
                    self.emit(d + 1, f"<= EGD from {pf} [{_producer_status(conn, s['producer_ctrl'])}]")
            for t in self.io_line(vid, "I"):
                self.emit(d + 1, "<= " + t)
            encs = self.encrypted(v)
            if encs:
                self.emit(d + 1, f"[encrypted: not traceable] programs {', '.join(encs)}")
            if v["control_constant"]:
                self.emit(d + 1, f"= control constant {v['value']}")
            return
        for p in writers:
            if not self.emit(d + 1, f"<= {p['ctrl']}/{p['path']}.{p['pin']} [{p['block_type'] or p['kind']}] "
                                    f"{_ds(p['direction'], p['dir_source'])} ({_fl(p['file_path'], p['line_no'])})"
                                    + (" [decl]" if p.get("decl") else "") + (f" [via {p['via']}]" if p.get("via") else "")):
                return
            if p["kind"] != "block":
                # interface pin of a macro/task: the real writer is the inner pin wired to it with 'L:<pin>'
                inner = _inner_pins(conn, p["block_id"], p["pin"], ("O",))
                if inner:
                    for q in inner:
                        if not self.emit(d + 2, f"<= inner {q['ctrl']}/{q['path']}.{q['pin']} [{q['block_type'] or q['kind']}] "
                                                f"{_ds(q['direction'], q['dir_source'])} ({_fl(q['file_path'], q['line_no'])})"):
                            return
                        self.up_block(q["block_id"], d + 3, level, up, skip_pin=q["id"])
                    continue
                if p["is_opaque"]:
                    self.emit(d + 2, "[opaque userblock: not traceable]")
                    continue
            self.up_block(p["block_id"], d + 2, level, up, skip_pin=p["id"])

    def up_block(self, block_id, d, level, up, skip_pin=None):
        conn = self.conn
        if block_id in self.seen_block:
            self.emit(d, "(block seen)")
            return
        self.seen_block.add(block_id)
        b = _block_by_id(conn, block_id)
        if b and b["is_opaque"]:
            self.emit(d, "[opaque userblock: not traceable]")
            return
        pins = [q for q in _pins_of_block(conn, block_id) if q["id"] != skip_pin and q["direction"] != "O"]
        # connected pins first; address-only ("declared at pin") pins are only followed when that variable has a
        # writer / EGD source, otherwise they are summarised in one line (LOGIC_BUILDER-style blocks have dozens)
        pins.sort(key=lambda q: 0 if q["conn_kind"] in ("V", "L", "P", "D", "N", "E") else 1)
        skipped_decl = 0
        for q in pins:
            k = q["conn_kind"]
            ds = _ds(q["direction"], q["dir_source"])
            if k in ("A", "-"):
                dv = _var_at_pin(conn, q) if k == "A" else None
                if dv:
                    has_src = (_row(conn, "SELECT 1 FROM pin WHERE var_id=? AND direction='O' LIMIT 1", (dv["id"],))
                               or _row(conn, "SELECT 1 FROM egd_consumed WHERE local_var_id=? LIMIT 1", (dv["id"],))
                               or _row(conn, "SELECT 1 FROM io_point WHERE var_id=? AND direction='I' LIMIT 1", (dv["id"],)))
                    if has_src:
                        self.up_var(dv["id"], d, level + 1, up, head=f"{q['pin']} {ds} = [decl]")
                    else:
                        skipped_decl += 1
                continue
            if k == "V" and q["var_id"]:
                self.up_var(q["var_id"], d, level + 1, up, head=f"{q['pin']} {ds} <-")
            elif k in ("N", "E"):
                self.emit(d, f"{q['pin']} {ds} <- {q['connection']} [const]")
            elif k in ("L", "P"):
                tb = _block_by_id(conn, q["tgt_block_id"]) if q["tgt_block_id"] else None
                if not tb:
                    self.emit(d, f"{q['pin']} {ds} <- {q['connection']} [unresolved]")
                    continue
                if not self.emit(d, f"{q['pin']} {ds} <- {q['connection']} = {tb['ctrl']}/{tb['path']}.{q['tgt_pin']} "
                                    f"[{tb['block_type'] or tb['kind']}] ({_fl(tb['file_path'], tb['line_no'])})"):
                    return
                if level + 1 > up:
                    continue
                if k == "P":
                    # interface pin of the enclosing macro: follow its own wiring outward
                    ip = _row(conn, _PIN_SQL + "WHERE p.block_id=? AND p.name=?", (tb["id"], q["tgt_pin"]))
                    if ip and ip["conn_kind"] == "V" and ip["var_id"]:
                        self.up_var(ip["var_id"], d + 1, level + 1, up, head=f"{ip['pin']} {_ds(ip['direction'], ip['dir_source'])} <-")
                    elif ip and ip["conn_kind"] in ("N", "E"):
                        self.emit(d + 1, f"{ip['pin']} <- {ip['connection']} [const]")
                    elif ip and ip["conn_kind"] in ("L", "P") and ip["tgt_block_id"]:
                        self.up_block(ip["tgt_block_id"], d + 1, level + 1, up)
                else:
                    self.up_block(tb["id"], d + 1, level + 1, up)
            elif k == "D":
                self.emit(d, f"{q['pin']} {ds} <- {q['connection']} [device pin, unresolved]")
        if skipped_decl:
            self.emit(d, f"... {skipped_decl} declared-only pins without any writer skipped")

    # ---- downstream
    def down_var(self, vid, d, level, down, head=None):
        conn = self.conn
        v = _var_by_id(conn, vid)
        if not v:
            return
        readers = _var_pins(conn, v, ("I", "S"))
        writers_n = len(_var_pins(conn, v, ("O",)))
        tag = f"{v['full_name']} [{v['datatype'] or '?'}]"
        if head is not None:
            tag = f"{head} {tag}"
        if writers_n > 1:
            tag += f" [multi-writer {writers_n}]"
        if vid in self.seen_var:
            self.emit(d, tag + " (seen)")
            return
        self.seen_var.add(vid)
        if not self.emit(d, tag):
            return
        if level > down:
            return
        for p in readers:
            if not self.emit(d + 1, f"=> {p['ctrl']}/{p['path']}.{p['pin']} [{p['block_type'] or p['kind']}] "
                                    f"{_ds(p['direction'], p['dir_source'])} ({_fl(p['file_path'], p['line_no'])})"
                                    + (" [decl]" if p.get("decl") else "") + (f" [via {p['via']}]" if p.get("via") else "")):
                return
            if p["kind"] != "block":
                inner = _inner_pins(conn, p["block_id"], p["pin"], ("I", "S"))
                if inner:
                    for q in inner:
                        if not self.emit(d + 2, f"=> inner {q['ctrl']}/{q['path']}.{q['pin']} [{q['block_type'] or q['kind']}] "
                                                f"{_ds(q['direction'], q['dir_source'])} ({_fl(q['file_path'], q['line_no'])})"):
                            return
                        self.down_block(q["block_id"], d + 3, level, down, skip_pin=q["id"])
                    continue
                if p["is_opaque"]:
                    self.emit(d + 2, "[opaque userblock: not traceable]")
                    continue
            self.down_block(p["block_id"], d + 2, level, down, skip_pin=p["id"])
        for c in _rows(conn, "SELECT consumer_ctrl,producer_ctrl,var_name,exchange_id,voffs,match_method,local_var_id FROM egd_consumed WHERE producer_var_id=?", (vid,)):
            if c["local_var_id"]:
                self.down_var(c["local_var_id"], d + 1, level + 1, down,
                              head=f"=> EGD exch {c['exchange_id']} voffs {c['voffs']} match {c['match_method']}")
            else:
                self.emit(d + 1, f"=> EGD consumer {c['consumer_ctrl']} {c['producer_ctrl']}.{c['var_name']} [no local variable]")
        for t in self.io_line(vid, "O"):
            self.emit(d + 1, "=> " + t)
        unk = conn.execute("SELECT count(*) FROM pin WHERE var_id=? AND direction='?'", (vid,)).fetchone()[0]
        if unk:
            self.emit(d + 1, f"?  {unk} pin(s) with unknown direction not followed (show {v['full_name']})")

    def down_block(self, block_id, d, level, down, skip_pin=None):
        conn = self.conn
        if block_id in self.seen_block:
            self.emit(d, "(block seen)")
            return
        self.seen_block.add(block_id)
        b = _block_by_id(conn, block_id)
        if b and b["is_opaque"]:
            self.emit(d, "[opaque userblock: not traceable]")
            return
        for q in _pins_of_block(conn, block_id):
            if q["id"] == skip_pin or q["direction"] != "O":
                continue
            ds = _ds(q["direction"], q["dir_source"])
            if q["conn_kind"] == "A":
                dv = _var_at_pin(conn, q)
                if dv:
                    self.down_var(dv["id"], d, level + 1, down, head=f"{q['pin']} {ds} = [decl]")
            if q["conn_kind"] == "V" and q["var_id"]:
                self.down_var(q["var_id"], d, level + 1, down, head=f"{q['pin']} {ds} ->")
            elif q["conn_kind"] in ("L", "P") and q["tgt_block_id"]:
                tb = _block_by_id(conn, q["tgt_block_id"])
                if tb and self.emit(d, f"{q['pin']} {ds} -> {q['connection']} = {tb['ctrl']}/{tb['path']}.{q['tgt_pin']} [{tb['block_type'] or tb['kind']}]"):
                    if level + 1 <= down:
                        self.down_block(tb["id"], d + 1, level + 1, down)
            # pins elsewhere that read this output through 'L:Block.Pin'
            for h in _rows(conn, _PIN_SQL + "WHERE p.tgt_block_id=? AND p.tgt_pin=? AND p.direction IN ('I','S') ORDER BY b.path", (block_id, q["pin"])):
                if not self.emit(d, f"{q['pin']} {ds} -> {h['ctrl']}/{h['path']}.{h['pin']} [{h['block_type'] or h['kind']}] "
                                    f"{_ds(h['direction'], h['dir_source'])} ({_fl(h['file_path'], h['line_no'])})"):
                    return
                if level + 1 <= down:
                    self.down_block(h["block_id"], d + 1, level + 1, down, skip_pin=h["id"])

    def encrypted(self, v):
        refd = [x for x in (v["referenced_in"] or "").split(",") if x]
        if not refd:
            return []
        encset = {r[0] for r in self.conn.execute("SELECT name FROM program WHERE ctrl=? AND encrypted=1", (v["ctrl"],))}
        return [p for p in refd if p in encset]


def trace(conn, signal, up=0, down=0, max_lines=60):
    v, err = resolve_signal(conn, signal)
    if err:
        return err
    if not up and not down:
        up = down = 1
    out = {"kind": "trace", "signal": v["full_name"], "datatype": v["datatype"], "up": up, "down": down,
           "max_lines": max_lines, "up_lines": [], "down_lines": []}
    if up:
        t = _Trace(conn, max_lines)
        t.up_var(v["id"], 0, 1, up)
        out["up_lines"] = t.lines
        out["up_exhausted"] = t.exhausted
    if down:
        t = _Trace(conn, max_lines)
        t.down_var(v["id"], 0, 1, down)
        out["down_lines"] = t.lines
        out["down_exhausted"] = t.exhausted
    return out


# ---------------------------------------------------------------------------------------------- block/task
def _find_block(conn, ctrl, path):
    b = _row(conn, """SELECT b.*, pr.name AS program, pr.file_path, pr.encrypted FROM block b
                      JOIN program pr ON pr.id=b.program_id WHERE b.ctrl=? AND b.path=?""", (ctrl, path))
    if b:
        return b, None
    # tolerate 'Task/Block' without the program, or a bare block name
    cands = _rows(conn, """SELECT b.*, pr.name AS program, pr.file_path, pr.encrypted FROM block b
                           JOIN program pr ON pr.id=b.program_id
                           WHERE b.ctrl=? AND (b.path LIKE ? OR b.name=?) ORDER BY b.path LIMIT %d""" % SECTION_CAP,
                  (ctrl, "%/" + path, path))
    if len(cands) == 1:
        return cands[0], None
    if cands:
        return None, {"kind": "ambiguous", "key": f"{ctrl} {path}", "matched_by": "block path suffix/name",
                      "candidates": [{"full_name": f"{c['ctrl']}/{c['path']}", "datatype": c["block_type"],
                                      "description": c["description"]} for c in cands],
                      "hint": "use the full path Program/Task/.../Block"}
    return None, {"kind": "notfound", "key": f"{ctrl} {path}", "hint": "block path is Program/Task/.../Block"}


def block(conn, ctrl, path):
    b, err = _find_block(conn, ctrl, path)
    if err:
        return err
    pins = _pins_of_block(conn, b["id"])
    names = _full_names(conn, [p["var_id"] for p in pins])
    prow = []
    for p in pins:
        prow.append({"pin": p["pin"], "dir": _ds(p["direction"], p["dir_source"]), "conn_kind": p["conn_kind"] or "-",
                     "to": _conn_text(conn, p, names), "usage": p["usage_declared"], "value": p["value"],
                     "alias": p["alias"], "at": _fl(p["file_path"], p["line_no"])})
    attrs = _rows(conn, "SELECT name, value FROM block_attr WHERE block_id=? ORDER BY name", (b["id"],))
    children = _rows(conn, """SELECT path, name, coalesce(block_type,'') AS block_type, kind, is_opaque, description, line_no
                              FROM block WHERE parent_id=? ORDER BY line_no""", (b["id"],))
    parent = _block_by_id(conn, b["parent_id"]) if b["parent_id"] else None
    task = _row(conn, "SELECT name, logic_drg, line_no FROM task WHERE id=?", (b["task_id"],)) if b["task_id"] else None
    help_row = _row(conn, "SELECT library, mht_path, def_file FROM library_help WHERE block_type=?", (b["block_type"],))
    return {"kind": "block", "ctrl": b["ctrl"], "path": b["path"], "name": b["name"], "block_type": b["block_type"],
            "block_kind": b["kind"], "version": b["version"], "is_opaque": b["is_opaque"], "description": b["description"],
            "program": b["program"], "task": task["name"] if task else None,
            "logic_drg": b["logic_drg"] or (task["logic_drg"] if task else None), "p_id": b["p_id"], "device": b["device"],
            "hmi_linked_object": b["hmi_linked_object"], "at": _fl(b["file_path"], b["line_no"]),
            "parent": f"{parent['ctrl']}/{parent['path']}" if parent else None,
            "pins": prow, "attrs": attrs, "children": children, "library_help": help_row}


def task(conn, ctrl, program, task_name):
    pr = _row(conn, "SELECT * FROM program WHERE ctrl=? AND name=?", (ctrl, program))
    if not pr:
        cands = [r[0] for r in conn.execute("SELECT name FROM program WHERE ctrl=? AND name LIKE ? ORDER BY name LIMIT 40",
                                            (ctrl, f"%{program}%"))]
        return {"kind": "notfound", "key": f"{ctrl} {program}", "hint": "program not found",
                "candidates": cands}
    t = _row(conn, "SELECT * FROM task WHERE program_id=? AND name=?", (pr["id"], task_name))
    if not t:
        cands = [r[0] for r in conn.execute("SELECT name FROM task WHERE program_id=? ORDER BY name", (pr["id"],))]
        return {"kind": "notfound", "key": f"{ctrl} {program} {task_name}", "hint": "task not found in program",
                "candidates": cands, "encrypted": pr["encrypted"]}
    blocks = _rows(conn, """SELECT path, name, coalesce(block_type,'') AS block_type, kind, is_opaque, description,
                                   logic_drg, p_id, line_no,
                                   (SELECT count(*) FROM pin WHERE pin.block_id=block.id) AS n_pins
                            FROM block WHERE task_id=? AND kind<>'task' ORDER BY line_no""", (t["id"],))
    troot = _row(conn, "SELECT id FROM block WHERE task_id=? AND kind='task'", (t["id"],))
    iface = []
    if troot:
        for p in _pins_of_block(conn, troot["id"]):
            iface.append({"pin": p["pin"], "dir": _ds(p["direction"], p["dir_source"]), "usage": p["usage_declared"],
                          "conn_kind": p["conn_kind"] or "-", "to": _conn_text(conn, p)})
    return {"kind": "task", "ctrl": ctrl, "program": program, "task": t["name"], "block_type": t["block_type"],
            "logic_drg": t["logic_drg"], "is_task": t["is_task"], "at": _fl(pr["file_path"], t["line_no"]),
            "program_encrypted": pr["encrypted"], "n_blocks": len(blocks), "interface_pins": iface,
            "blocks": blocks[:SECTION_CAP], "blocks_more": max(0, len(blocks) - SECTION_CAP)}


# --------------------------------------------------------------------------------------------------- find
def _like(pattern):
    p = pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%").replace("?", "_")
    return p if _WILD.search(pattern) else f"%{p}%"


def _fts_query(pattern):
    return '"' + pattern.replace('"', '""') + '"*'


def _var_flags(conn, v):
    vid = v["id"]
    f = []
    r = conn.execute("SELECT sum(direction='O'), sum(direction IN ('I','S')), sum(direction='?') FROM pin WHERE var_id=?", (vid,)).fetchone()
    w, rd, u = r[0] or 0, r[1] or 0, r[2] or 0
    for dp in _var_pins(conn, v):
        if dp["var_id"] == vid:
            continue
        if dp["direction"] == "O":
            w += 1
        elif dp["direction"] in ("I", "S"):
            rd += 1
        else:
            u += 1
    if w:
        f.append(f"W{w}")
    if rd:
        f.append(f"R{rd}")
    if u:
        f.append(f"?{u}")
    if conn.execute("SELECT 1 FROM io_point WHERE var_id=? LIMIT 1", (vid,)).fetchone():
        f.append("IO")
    if conn.execute("SELECT 1 FROM egd_produced WHERE var_id=? LIMIT 1", (vid,)).fetchone() or \
       conn.execute("SELECT 1 FROM egd_consumed WHERE local_var_id=? LIMIT 1", (vid,)).fetchone():
        f.append("EGD")
    if conn.execute("SELECT 1 FROM hmi_point WHERE var_id=? LIMIT 1", (vid,)).fetchone():
        f.append("HMI")
    return f


def find(conn, pattern, ctrl=None, kind="var", limit=40):
    pattern = (pattern or "").strip()
    kind = (kind or "var").lower()
    limit = int(limit or 40)
    out = {"kind": "find", "pattern": pattern, "ctrl": ctrl, "what": kind, "rows": [], "more": 0}
    if kind == "var":
        found = OrderedDict()
        wild = bool(_WILD.search(pattern))
        if not wild:
            try:
                sql = """SELECT v.id, v.ctrl, v.full_name, v.datatype, v.description, v.alias, v.decl_connection FROM variable_fts f
                         JOIN variable v ON v.id=f.rowid WHERE variable_fts MATCH ?"""
                args = [_fts_query(pattern)]
                if ctrl:
                    sql += " AND v.ctrl=?"
                    args.append(ctrl)
                sql += " ORDER BY rank LIMIT %d" % (limit * 3)
                for r in _rows(conn, sql, args):
                    found.setdefault(r["id"], r)
            except sqlite3.OperationalError as e:      # no FTS5 / empty index
                out["fts_error"] = str(e)
        sql = """SELECT id, ctrl, full_name, datatype, description, alias, decl_connection FROM variable
                 WHERE (name LIKE ? ESCAPE '\\' OR alias LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"""
        lk = _like(pattern)
        args = [lk, lk, lk]
        if ctrl:
            sql += " AND ctrl=?"
            args.append(ctrl)
        sql += " ORDER BY length(name), full_name LIMIT %d" % (limit * 3 + 1)
        for r in _rows(conn, sql, args):
            found.setdefault(r["id"], r)
        rows = list(found.values())
        # exact / prefix name hits first
        pl = pattern.lower()
        rows.sort(key=lambda r: (0 if r["full_name"].lower().split(".", 1)[-1] == pl else
                                 1 if pl in r["full_name"].lower() else 2, len(r["full_name"]), r["full_name"]))
        out["more"] = max(0, len(rows) - limit)
        for r in rows[:limit]:
            r["flags"] = _var_flags(conn, r)
            r.pop("decl_connection", None)
            out["rows"].append(r)
        return out
    lk = _like(pattern)
    if kind == "block":
        sql = """SELECT b.ctrl, b.path, coalesce(b.block_type,'') AS block_type, b.kind AS bkind, b.description,
                        pr.file_path, b.line_no FROM block b JOIN program pr ON pr.id=b.program_id
                 WHERE (b.name LIKE ? ESCAPE '\\' OR b.path LIKE ? ESCAPE '\\' OR b.block_type LIKE ? ESCAPE '\\')"""
        args = [lk, lk, lk]
        if ctrl:
            sql += " AND b.ctrl=?"
            args.append(ctrl)
        sql += " ORDER BY b.ctrl, b.path LIMIT %d" % (limit + 1)
        rows = _rows(conn, sql, args)
        for r in rows:
            r["at"] = _fl(r.pop("file_path"), r.pop("line_no"))
    elif kind == "io":
        where = "WHERE (p.device_tag LIKE ? ESCAPE '\\' OR p.name LIKE ? ESCAPE '\\' OR p.connection LIKE ? ESCAPE '\\')"
        args = [lk, lk, lk]
        if ctrl:
            where += " AND p.ctrl=?"
            args.append(ctrl)
        rows = _io_rows(conn, where=where, args=tuple(args))
        rows = rows[:limit + 1]
    elif kind == "screen":
        sql = """SELECT screen, count(*) AS n_points FROM hmi_point WHERE screen LIKE ? ESCAPE '\\'
                 GROUP BY screen ORDER BY screen LIMIT %d""" % (limit + 1)
        rows = _rows(conn, sql, (lk,))
        menu = {(r[0] or "").lower(): " / ".join(x for x in r[1:] if x) for r in
                conn.execute("SELECT screen, block, menu, submenu, item FROM hmi_menu")}
        for r in rows:
            r["menu"] = menu.get((r["screen"] or "").lower())
    elif kind == "alarm":
        res = alarm(conn, pattern, ctrl=ctrl, limit=limit)
        res["kind"] = "find"
        res["what"] = "alarm"
        res["pattern"] = pattern
        return res
    else:
        return {"kind": "error", "message": f"unknown --kind {kind}; use var|block|io|screen|alarm"}
    out["more"] = max(0, len(rows) - limit)
    out["rows"] = rows[:limit]
    return out


# ----------------------------------------------------------------------------------------------------- io
def io(conn, key, ctrl=None):
    key = (key or "").strip()
    cwhere, cargs = ("AND p.ctrl=? ", [ctrl]) if ctrl else ("", [])
    tries = [
        ("device_tag", "WHERE p.device_tag=? COLLATE NOCASE " + cwhere, [key] + cargs),
        ("connection", "WHERE p.connection=? COLLATE NOCASE " + cwhere, [key] + cargs),
        ("module", "WHERE m.name=? COLLATE NOCASE " + cwhere, [key] + cargs),
        ("cabinet", "WHERE m.cabinet=? COLLATE NOCASE " + cwhere, [key] + cargs),
        ("board", "WHERE b.name=? COLLATE NOCASE " + cwhere, [key] + cargs),
    ]
    rows, how = [], None
    for how, where, args in tries:
        rows = _io_rows(conn, where=where, args=tuple(args))
        if rows:
            break
    if not rows:
        v, _err = resolve_signal(conn, key)
        if v:
            rows = _io_rows(conn, var_id=v["id"])
            how = "variable"
    if not rows:
        lk = _like(key)
        rows = _io_rows(conn, where="WHERE (p.device_tag LIKE ? ESCAPE '\\' OR p.name LIKE ? ESCAPE '\\' "
                                    "OR m.name LIKE ? ESCAPE '\\' OR m.cabinet LIKE ? ESCAPE '\\') " + cwhere,
                        args=tuple([lk, lk, lk, lk] + cargs))
        how = "like"
    if not rows:
        v2, _e = resolve_signal(conn, key)
        hint = (f"variable {v2['full_name']} exists but has no io_point (not wired to field I/O)" if v2 else
                "no io_point by device_tag / connection / module / cabinet / board / variable")
        return {"kind": "notfound", "key": key, "hint": hint}
    names = _full_names(conn, [r["var_id"] for r in rows])
    for r in rows:
        r["var"] = names.get(r["var_id"])
    return {"kind": "io", "key": key, "matched_by": how, "rows": rows, "n": len(rows)}


# ---------------------------------------------------------------------------------------------------- egd
def egd(conn, key, page=None):
    key = (key or "").strip()
    ctrls = _controllers(conn)
    if key in ctrls or key.upper() in ctrls:
        c = key if key in ctrls else key.upper()
        pid = (_row(conn, "SELECT egd_producer_id FROM controller WHERE name=?", (c,)) or {}).get("egd_producer_id")
        sql = """SELECT x.id, x.exchange_id, x.page, x.period_ns, x.data_length, x.sig_major,
                        (SELECT count(*) FROM egd_produced p WHERE p.exchange_pk=x.id) AS n_vars,
                        (SELECT count(*) FROM egd_produced p WHERE p.exchange_pk=x.id AND p.var_id IS NULL) AS n_novar,
                        (SELECT group_concat(DISTINCT c.consumer_ctrl) FROM egd_consumed c
                          WHERE c.producer_ctrl=x.producer_ctrl AND c.exchange_id=x.exchange_id) AS consumers,
                        (SELECT count(*) FROM egd_consumed c
                          WHERE c.producer_ctrl=x.producer_ctrl AND c.exchange_id=x.exchange_id) AS n_consumed
                 FROM egd_exchange x WHERE x.producer_ctrl=? """
        args = [c]
        if page:
            sql += "AND x.page=? COLLATE NOCASE "
            args.append(page)
        sql += "ORDER BY x.exchange_id"
        exchanges = _rows(conn, sql, args)
        for x in exchanges:
            x["period_ms"] = (x["period_ns"] or 0) / 1e6
            x["dcs_export"] = (x["page"] in DCS_PAGES)
        variables = []
        if page:
            variables = _rows(conn, """SELECT x.exchange_id, p.voffs, p.var_name, p.dtype, p.address, p.var_id,
                                              v.description, v.datatype
                                       FROM egd_produced p JOIN egd_exchange x ON x.id=p.exchange_pk
                                       LEFT JOIN variable v ON v.id=p.var_id
                                       WHERE x.producer_ctrl=? AND x.page=? COLLATE NOCASE
                                       ORDER BY x.exchange_id, p.voffs LIMIT %d""" % (SECTION_CAP + 1), (c, page))
        consumed = _rows(conn, """SELECT producer_ctrl, count(*) AS n, count(DISTINCT exchange_id) AS n_exchanges,
                                         sum(match_method='both') AS n_both, sum(match_method='name') AS n_name,
                                         sum(match_method='voffs') AS n_voffs, sum(match_method='none') AS n_none,
                                         sum(producer_var_id IS NULL) AS n_ext
                                  FROM egd_consumed WHERE consumer_ctrl=? GROUP BY producer_ctrl ORDER BY producer_ctrl""", (c,))
        for row in consumed:
            row["producer_status"] = _producer_status(conn, row["producer_ctrl"])
        pages = _rows(conn, """SELECT page, count(*) AS n_exchanges,
                                      (SELECT count(*) FROM egd_produced p JOIN egd_exchange y ON y.id=p.exchange_pk
                                        WHERE y.producer_ctrl=x.producer_ctrl AND coalesce(y.page,'')=coalesce(x.page,'')) AS n_vars
                               FROM egd_exchange x WHERE producer_ctrl=? GROUP BY page ORDER BY page""", (c,))
        return {"kind": "egd_ctrl", "ctrl": c, "producer_id": pid, "page": page, "pages": pages,
                "exchanges": exchanges, "variables": variables[:SECTION_CAP],
                "variables_more": max(0, len(variables) - SECTION_CAP), "consumed": consumed}
    v, err = resolve_signal(conn, key)
    if err:
        return err
    e = _egd_section(conn, v)
    e.update({"kind": "egd_var", "signal": v["full_name"], "egd_page": v["egd_page"], "device_name": v["device_name"]})
    return e


# ------------------------------------------------------------------------------------------------- screen
def screen(conn, key):
    key = (key or "").strip()
    menu = {}
    for r in conn.execute("SELECT screen, block, menu, submenu, item, unit FROM hmi_menu"):
        menu.setdefault((r[0] or "").lower(), []).append({"path": " / ".join(x for x in r[1:5] if x), "unit": r[5]})
    scr = None
    if key.lower().endswith(".cim"):
        scr = key
    else:
        hit = _row(conn, "SELECT screen FROM hmi_point WHERE screen=? COLLATE NOCASE LIMIT 1", (key,))
        if hit:
            scr = hit["screen"]
    if scr:
        rows = _rows(conn, """SELECT h.full_point, h.unit_prefix, h.source, h.var_id, v.full_name, v.datatype, v.description
                              FROM hmi_point h LEFT JOIN variable v ON v.id=h.var_id
                              WHERE h.screen=? COLLATE NOCASE ORDER BY h.full_point, h.source""", (scr,))
        pts = OrderedDict()
        for r in rows:
            e = pts.setdefault(r["full_point"], {"full_point": r["full_point"], "var": r["full_name"],
                                                  "datatype": r["datatype"], "description": r["description"],
                                                  "sources": []})
            if r["source"] not in e["sources"]:
                e["sources"].append(r["source"])
        if not pts and scr.lower() not in menu:
            cands = [r[0] for r in conn.execute("SELECT DISTINCT screen FROM hmi_point WHERE screen LIKE ? ORDER BY screen LIMIT 40",
                                                (f"%{scr[:-4] if scr.lower().endswith('.cim') else scr}%",))]
            return {"kind": "notfound", "key": key, "hint": "screen not in hmi_point/hmi_menu", "candidates": cands}
        plist = list(pts.values())
        return {"kind": "screen", "screen": scr, "menu": menu.get(scr.lower(), []), "n_points": len(plist),
                "n_resolved": sum(1 for p in plist if p["var"]), "points": plist[:SECTION_CAP],
                "more": max(0, len(plist) - SECTION_CAP)}
    v, err = resolve_signal(conn, key)
    if err:
        return err
    return {"kind": "screen_var", "signal": v["full_name"], "display_screen": v["display_screen"],
            "screens": _hmi_section(conn, v)}


# -------------------------------------------------------------------------------------------------- alarm
def alarm(conn, pattern, ctrl=None, limit=40):
    lk = _like(pattern or "")
    sql = """SELECT full_name, datatype, description, alarm_id, alarm_class, alarm_definition, plant_area,
                    potential_causes, operator_action, consequence, urgency
             FROM variable WHERE (alarm_id IS NOT NULL OR alarm_class IS NOT NULL OR alarm_definition IS NOT NULL)
               AND (name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR potential_causes LIKE ? ESCAPE '\\'
                    OR operator_action LIKE ? ESCAPE '\\' OR consequence LIKE ? ESCAPE '\\' OR alias LIKE ? ESCAPE '\\')"""
    args = [lk] * 6
    if ctrl:
        sql += " AND ctrl=?"
        args.append(ctrl)
    sql += " ORDER BY full_name LIMIT %d" % (int(limit) + 1)
    rows = _rows(conn, sql, args)
    return {"kind": "alarm", "pattern": pattern, "ctrl": ctrl, "rows": rows[:limit], "more": max(0, len(rows) - limit)}


# -------------------------------------------------------------------------------------------------- where
def where(conn, key):
    key = (key or "").strip()
    ctrls = _controllers(conn)
    ctrl = path = None
    if " " in key:
        ctrl, path = key.split(None, 1)
    elif "/" in key:
        ctrl, path = key.split("/", 1)
    if ctrl and ctrl in ctrls and path:
        b, err = _find_block(conn, ctrl, path)
        if err:
            return err
        items = [{"what": f"block {b['ctrl']}/{b['path']} [{b['block_type'] or b['kind']}]", "at": _fl(b["file_path"], b["line_no"])}]
        for p in _pins_of_block(conn, b["id"]):
            if p["conn_kind"] in ("A", "-"):
                continue
            items.append({"what": f"pin .{p['pin']} {_ds(p['direction'], p['dir_source'])} {p['conn_kind']} {p['connection'] or ''}".rstrip(),
                          "at": _fl(p["file_path"], p["line_no"])})
        return {"kind": "where", "key": key, "target": f"{b['ctrl']}/{b['path']}", "items": items}
    v, err = resolve_signal(conn, key)
    if err:
        return err
    items = [{"what": f"decl {v['full_name']} ({v['decl_connection'] or 'no Connection'})", "at": _fl(v["decl_file"], v["decl_line"])}]
    for p in _var_pins(conn, v):
        d = p["direction"] or "?"
        label = {"O": "writer", "I": "reader", "S": "state"}.get(d, "unknown-dir")
        items.append({"what": f"{label} {p['ctrl']}/{p['path']}.{p['pin']} [{p['block_type'] or p['kind']}] {_ds(d, p['dir_source'])}"
                      + (" (declared at this pin)" if p.get("decl") else "") + (f" (via interface pin {p['via']})" if p.get("via") else ""),
                      "at": _fl(p["file_path"], p["line_no"])})
    mir = _mirror_info(conn, v["id"])
    if mir:
        items.append({"what": f"mirror of pin {mir['pin']['ref']} [{mir['pin']['block_type']}] kind {mir['kind']}" + (f" <- {mir['src_text']}" if mir['src_text'] else ""),
                      "at": mir["pin"]["at"]})
    for r in _io_rows(conn, var_id=v["id"]):
        items.append({"what": f"io {r['ctrl']} {r['module'] or ''}/{r['board'] or ''} {r['point']} {r['direction']}", "at": r["at"]})
    for r in _rows(conn, """SELECT x.producer_ctrl, x.exchange_id, p.voffs FROM egd_produced p JOIN egd_exchange x ON x.id=p.exchange_pk
                            WHERE p.var_id=?""", (v["id"],)):
        items.append({"what": f"egd produced exch {r['exchange_id']} voffs {r['voffs']}", "at": f"{r['producer_ctrl']}/ProducedData.xml"})
    for r in _rows(conn, "SELECT consumer_ctrl, producer_ctrl, exchange_id, voffs FROM egd_consumed WHERE local_var_id=?", (v["id"],)):
        items.append({"what": f"egd consumed from {r['producer_ctrl']} exch {r['exchange_id']} voffs {r['voffs']}", "at": f"{r['consumer_ctrl']}/ConsumedData.xml"})
    return {"kind": "where", "key": key, "target": v["full_name"], "items": items[:SECTION_CAP],
            "more": max(0, len(items) - SECTION_CAP)}


# ------------------------------------------------------------------------------------------- lint/coverage
def lint(conn, limit=40):
    limit = int(limit or 40)
    multi = []
    # a task/userblock interface pin declared as the variable + the inner block writing it is ONE signal path,
    # so only count ordinary-block writers (or interface writers when there is no block writer)
    for vid, full, n, nb in conn.execute("""SELECT v.id, v.full_name, count(*) AS n, sum(b.kind='block') AS nb
                                            FROM pin p JOIN variable v ON v.id=p.var_id JOIN block b ON b.id=p.block_id
                                            WHERE p.direction='O' GROUP BY v.id HAVING (nb>1 OR (nb=0 AND n>1))
                                            ORDER BY nb DESC, n DESC, v.full_name LIMIT ?""", (limit,)):
        ws = _pins_of_var(conn, vid, ("O",))
        multi.append({"full_name": full, "n_writers": n, "n_block_writers": nb, "n_interface_writers": n - nb,
                      "writers": [f"{p['ctrl']}/{p['path']}.{p['pin']} [{p['block_type'] or p['kind']}] {_ds(p['direction'], p['dir_source'])} "
                                  f"{_fl(p['file_path'], p['line_no'])}" for p in ws[:3]],
                      "writers_more": max(0, len(ws) - 3)})
    n_multi = conn.execute("""SELECT count(*) FROM (SELECT p.var_id, count(*) AS n, sum(b.kind='block') AS nb FROM pin p JOIN block b ON b.id=p.block_id
                              WHERE p.direction='O' AND p.var_id IS NOT NULL GROUP BY p.var_id HAVING (nb>1 OR (nb=0 AND n>1)))""").fetchone()[0]
    # logic writer AND field input on the same variable
    wio = _rows(conn, """SELECT v.full_name, i.ctrl, i.name AS point, i.device_tag,
                                (SELECT count(*) FROM pin p WHERE p.var_id=v.id AND p.direction='O') AS n_writers
                         FROM io_point i JOIN variable v ON v.id=i.var_id
                         WHERE i.direction='I' AND EXISTS(SELECT 1 FROM pin p WHERE p.var_id=v.id AND p.direction='O')
                         ORDER BY v.full_name LIMIT ?""", (limit,))
    n_wio = conn.execute("""SELECT count(*) FROM io_point i WHERE i.direction='I' AND i.var_id IS NOT NULL
                            AND EXISTS(SELECT 1 FROM pin p WHERE p.var_id=i.var_id AND p.direction='O')""").fetchone()[0]
    # more than one field input point on one variable
    mio = _rows(conn, """SELECT v.full_name, count(*) AS n FROM io_point i JOIN variable v ON v.id=i.var_id
                         WHERE i.direction='I' GROUP BY v.id HAVING n>1 ORDER BY n DESC, v.full_name LIMIT ?""", (limit,))
    n_mio = conn.execute("""SELECT count(*) FROM (SELECT var_id FROM io_point WHERE direction='I' AND var_id IS NOT NULL
                            GROUP BY var_id HAVING count(*)>1)""").fetchone()[0]
    # produced in more than one exchange
    megd = _rows(conn, """SELECT v.full_name, count(*) AS n, group_concat(x.exchange_id) AS exchanges
                          FROM egd_produced p JOIN egd_exchange x ON x.id=p.exchange_pk JOIN variable v ON v.id=p.var_id
                          GROUP BY v.id HAVING n>1 ORDER BY n DESC, v.full_name LIMIT ?""", (limit,))
    n_megd = conn.execute("""SELECT count(*) FROM (SELECT var_id FROM egd_produced WHERE var_id IS NOT NULL
                             GROUP BY var_id HAVING count(*)>1)""").fetchone()[0]
    # consumed rows whose producer/consumer offsets disagree
    mism = _rows(conn, """SELECT consumer_ctrl, producer_ctrl||'.'||var_name AS local_name, exchange_id, voffs, match_method
                          FROM egd_consumed WHERE match_method IN ('name','voffs') ORDER BY consumer_ctrl, exchange_id LIMIT ?""", (limit,))
    n_mism = conn.execute("SELECT count(*) FROM egd_consumed WHERE match_method IN ('name','voffs')").fetchone()[0]
    return {"kind": "lint", "limit": limit,
            "multi_writer": multi, "n_multi_writer": n_multi,
            "writer_and_io_input": wio, "n_writer_and_io_input": n_wio,
            "multi_io_input": mio, "n_multi_io_input": n_mio,
            "multi_egd_produced": megd, "n_multi_egd_produced": n_megd,
            "egd_mismatch": mism, "n_egd_mismatch": n_mism}


def coverage(conn, limit=40):
    limit = int(limit or 40)
    inlist = "(" + ",".join("'%s'" % c for c in CONNECTED) + ")"
    ctrls = []
    for ctrl, n_conn, n_unk, n_pins in conn.execute(f"""
        SELECT b.ctrl, sum(p.conn_kind IN {inlist}), sum(p.conn_kind IN {inlist} AND p.direction='?'), count(*)
        FROM pin p JOIN block b ON b.id=p.block_id GROUP BY b.ctrl ORDER BY b.ctrl"""):
        n_conn = n_conn or 0
        ctrls.append({"ctrl": ctrl, "n_pins": n_pins, "n_connected": n_conn, "n_unknown": n_unk or 0,
                      "frac_unknown": (n_unk or 0) / n_conn if n_conn else 0.0})
    top = [{"block_type": bt, "pin_name": pn, "n": n} for bt, pn, n in conn.execute(f"""
        SELECT coalesce(b.block_type,''), p.name, count(*) AS n FROM pin p JOIN block b ON b.id=p.block_id
        WHERE p.direction='?' AND p.conn_kind IN {inlist} GROUP BY 1,2 ORDER BY n DESC, 1, 2 LIMIT {limit}""")]
    src = {r[0] or "-": r[1] for r in conn.execute("SELECT dir_source, count(*) FROM pin GROUP BY dir_source")}
    dirs = {r[0] or "?": r[1] for r in conn.execute("SELECT direction, count(*) FROM pin GROUP BY direction")}
    enc = _rows(conn, "SELECT ctrl, count(*) AS n, sum(encrypted) AS n_encrypted FROM program GROUP BY ctrl ORDER BY ctrl")
    opaque = {r[0]: r[1] for r in conn.execute("SELECT ctrl, count(*) FROM block WHERE is_opaque=1 GROUP BY ctrl")}
    for e in enc:
        e["n_opaque_userblocks"] = opaque.get(e["ctrl"], 0)
    unres = _rows(conn, """SELECT ctrl, sum(conn_kind='V' AND var_id IS NULL) AS v_unresolved,
                                  sum(conn_kind='D') AS d_device_pins,
                                  sum(conn_kind IN ('L','P') AND tgt_block_id IS NULL) AS lp_unresolved
                           FROM pin p JOIN block b ON b.id=p.block_id GROUP BY ctrl ORDER BY ctrl""")
    manual = conn.execute("SELECT count(*) FROM pin_dir_table").fetchone()[0]
    types_total = conn.execute("SELECT count(DISTINCT block_type) FROM block WHERE kind='block'").fetchone()[0]
    types_manual = conn.execute("""SELECT count(DISTINCT b.block_type) FROM block b
                                   WHERE b.kind='block' AND b.block_type IN (SELECT block_type FROM pin_dir_table)""").fetchone()[0]
    return {"kind": "coverage", "controllers": ctrls, "top_unknown": top, "by_source": src, "by_direction": dirs,
            "programs": enc, "unresolved": unres,
            "manual": {"pin_dir_table_rows": manual, "block_types_in_use": types_total, "block_types_in_manual": types_manual}}


# -------------------------------------------------------------------------------------------------- audit-type
def audit_type(conn, block_type, ctrl=None, limit=200):
    """Per-pin audit of one block type: direction/source/conn_kind distribution, declared-at-pin linking, usage."""
    where = "b.block_type=?" + (" AND b.ctrl=?" if ctrl else "")
    args = [block_type] + ([ctrl] if ctrl else [])
    n_blocks = conn.execute(f"SELECT count(*) FROM block b WHERE {where}", args).fetchone()[0]
    pins = []
    for r in conn.execute(f"""
        SELECT p.name, count(*) AS n,
               sum(p.direction='I') AS n_i, sum(p.direction='O') AS n_o, sum(p.direction='S') AS n_s, sum(p.direction='?') AS n_q,
               group_concat(DISTINCT p.dir_source) AS srcs,
               sum(p.conn_kind='V') AS ck_v, sum(p.conn_kind='L') AS ck_l, sum(p.conn_kind='P') AS ck_p,
               sum(p.conn_kind IN ('N','E')) AS ck_c, sum(p.conn_kind='A') AS ck_a, sum(p.conn_kind='-') AS ck_none,
               sum(p.var_id IS NOT NULL) AS linked,
               sum(p.conn_kind='A' AND p.var_id IS NOT NULL) AS a_linked,
               sum(p.var_id IS NOT NULL AND EXISTS(SELECT 1 FROM pin q WHERE q.var_id=p.var_id AND q.id<>p.id)) AS used_by_other_pin,
               sum(p.var_id IS NOT NULL AND (EXISTS(SELECT 1 FROM egd_produced e WHERE e.var_id=p.var_id)
                                            OR EXISTS(SELECT 1 FROM hmi_point h WHERE h.var_id=p.var_id)
                                            OR EXISTS(SELECT 1 FROM io_point i WHERE i.var_id=p.var_id))) AS egd_hmi_io
        FROM pin p JOIN block b ON b.id=p.block_id WHERE {where} GROUP BY p.name ORDER BY n DESC, p.name""", args):
        pins.append({"pin": r[0], "n": r[1], "I": r[2], "O": r[3], "S": r[4], "unknown": r[5], "sources": r[6] or "",
                     "V": r[7], "L": r[8], "P": r[9], "const": r[10], "A": r[11], "none": r[12],
                     "linked": r[13], "a_linked": r[14], "used_by_other_pin": r[15], "egd_hmi_io": r[16]})
    manual = {r[0]: r[1] for r in conn.execute("SELECT pin_name, direction FROM pin_dir_table WHERE block_type=?", (block_type,))}
    unknown = [p for p in pins if p["unknown"] and p["unknown"] == p["n"]]
    return {"kind": "audit", "block_type": block_type, "ctrl": ctrl, "n_blocks": n_blocks, "n_pin_names": len(pins),
            "pins": pins[:limit], "pins_more": max(0, len(pins) - limit), "manual_pins": len(manual),
            "unknown_pin_names": [p["pin"] for p in unknown], "n_unknown_pin_names": len(unknown)}
