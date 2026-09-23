# -*- coding: utf-8 -*-
"""Post-parse cross-table fix-ups + FTS rebuild.

Stage modules resolve their own var_id where they can; this is the safety net that runs after all stages:
  * variable.producer_var_id  (EGD consumed copies 'G11.L27QE1_A' with device_name='G11' -> G11's own row)
  * io_point / egd_produced / egd_consumed / hmi_point / watch .var_id where still NULL
  * variable_fts rebuild (full_name, description, alias, device_tag, alarm_text)
"""
import time


def _upd(conn, sql, log, label):
    t0 = time.time()
    cur = conn.execute(sql)
    conn.commit()
    log(f"  {label:34s} {cur.rowcount:8d}  {time.time()-t0:4.1f}s")


def link_declared_pins(conn, ctrl_names, log=print):
    """Address-only pins (conn_kind 'A', no Connection attr) whose value the configuration tool publishes as a global variable
    declared AT that pin (GlobalNamePrefix Block/Task/Full). Link pin.var_id so writers/readers/traces/diagrams see it.
    Rules per controller, first hit wins: (1) variable.name == Block.Pin  (2) variable.decl_connection ==
    Program.<path dots>.Pin  (3) unique variable at the same address (prefer names not starting with 'DistributedIO.')."""
    t0 = time.time()
    tot = {"name": 0, "decl": 0, "addr": 0, "ambiguous": 0}
    for ctrl in ctrl_names:
        by_name, by_decl, by_addr = {}, {}, {}
        for vid, name, decl, addr in conn.execute("SELECT id,name,decl_connection,address FROM variable WHERE ctrl=?", (ctrl,)):
            by_name[name] = vid
            if decl:
                by_decl[decl] = vid
            if addr:
                by_addr.setdefault(addr, []).append((vid, name))
        updates = []
        for pid, pname, paddr, bname, bpath, prog in conn.execute("""
                SELECT p.id, p.name, p.address, b.name, b.path, pr.name FROM pin p
                JOIN block b ON b.id=p.block_id JOIN program pr ON pr.id=b.program_id
                WHERE b.ctrl=? AND p.conn_kind='A' AND p.var_id IS NULL""", (ctrl,)):
            vid = by_name.get(f"{bname}.{pname}")
            how = "name"
            if vid is None:
                vid = by_decl.get(f"{bpath.replace('/', '.')}.{pname}") if bpath else None   # path already starts with Program
                how = "decl"
            if vid is None and paddr:
                cands = by_addr.get(paddr, [])
                if len(cands) > 1:
                    cands = [c for c in cands if not c[1].startswith("DistributedIO.")] or cands
                if len(cands) == 1:
                    vid, how = cands[0][0], "addr"
                elif len(cands) > 1:
                    tot["ambiguous"] += 1
            if vid is not None:
                updates.append((vid, pid))
                tot[how] += 1
        conn.executemany("UPDATE pin SET var_id=? WHERE id=?", updates)
        conn.commit()
    log(f"  declared-at-pin links: name={tot['name']} decl={tot['decl']} addr={tot['addr']} ambiguous-skipped={tot['ambiguous']}  {time.time()-t0:.1f}s")
    return tot


def build_pin_mirror(conn, ctrl_names, log=print):
    """A variable declared at a pin that ALSO carries its own connection (V/L/P/N/E...) is the published value of that
    pin ('mirror'). Input pin -> the mirror's source is the pin's wiring; output pin -> the block writes it.
    Keys: decl_connection == Program.Task….Block.Pin, else name == Block.Pin."""
    t0 = time.time()
    conn.execute("CREATE TABLE IF NOT EXISTS pin_mirror(var_id INTEGER PRIMARY KEY, pin_id INTEGER, kind TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_mirror_pin ON pin_mirror(pin_id)")
    inlist = ",".join("'" + n.replace("'", "''") + "'" for n in ctrl_names)
    conn.execute(f"DELETE FROM pin_mirror WHERE var_id IN (SELECT id FROM variable WHERE ctrl IN ({inlist}))")
    tot = {"decl": 0, "name": 0, "I": 0, "O": 0, "?": 0}
    for ctrl in ctrl_names:
        by_decl, by_name = {}, {}
        for pid, pname, bname, bpath, ck, direction, vid in conn.execute("""
                SELECT p.id, p.name, b.name, b.path, p.conn_kind, p.direction, p.var_id FROM pin p JOIN block b ON b.id=p.block_id
                WHERE b.ctrl=? AND p.conn_kind<>'A'""", (ctrl,)):
            by_decl[f"{bpath.replace('/', '.')}.{pname}"] = (pid, direction, vid)
            by_name.setdefault(f"{bname}.{pname}", (pid, direction, vid))
        rows = []
        for vid, name, decl in conn.execute("SELECT id,name,decl_connection FROM variable WHERE ctrl=?", (ctrl,)):
            hit = by_decl.get(decl) if decl else None
            how = "decl"
            if hit is None:
                hit = by_name.get(name)
                how = "name"
            if hit is None or hit[2] == vid:      # pin's own wiring IS this variable -> not a mirror
                continue
            kind = hit[1] if hit[1] in ("I", "O") else "?"
            rows.append((vid, hit[0], kind))
            tot[how] += 1
            tot[kind] += 1
        conn.executemany("INSERT OR REPLACE INTO pin_mirror(var_id,pin_id,kind) VALUES(?,?,?)", rows)
        conn.commit()
    log(f"  pin mirrors: decl={tot['decl']} name={tot['name']}  I={tot['I']} O={tot['O']} ?={tot['?']}  {time.time()-t0:.1f}s")
    return tot


def _inlist(names):
    return ",".join("'" + n.replace("'", "''") + "'" for n in names)


def purge_recovered(conn, ctrl_names, log=print):
    """Drop pins recovered by recover_opaque_pins for these controllers (post-only rebuilds would otherwise keep stale
    rows and feed them into link_declared_pins / build_pin_mirror / direction)."""
    cur = conn.execute(f"DELETE FROM pin WHERE origin IS NOT NULL AND block_id IN (SELECT id FROM block WHERE ctrl IN ({_inlist(ctrl_names)}))")
    conn.execute(f"DELETE FROM pin_mirror WHERE pin_id NOT IN (SELECT id FROM pin)")
    conn.commit()
    if cur.rowcount:
        log(f"  purged recovered pins: {cur.rowcount}")


def recover_opaque_pins(conn, ctrl_names, log=print):
    """Opaque macros (UserBlock whose whole body, interface pins included, is encrypted) have no pin rows. Recover the
    visible part of their interface from two kinds of evidence and mark the rows with pin.origin:
      'decl'  a variable declared AT the block ('Program.Task….Block.PIN'): one pin per variable. Direction from evidence:
              control constant -> I ('A', var=itself); address shared with an OUTPUT pin of this controller -> I ('V',
              var=itself; that pin already writes it); address shared with another variable -> I ('V', var=that source,
              and the declared variable becomes a pin_mirror of this pin); otherwise 'A' var=itself with O only when the
              variable has readers/alarm/HMI/IO-output AND no other writer/field input/EGD source, else '?'.
      'link'  a sibling pin wired 'L:Block.PIN' to the block: direction opposite to the referrer ('-', no variable).
      'pair'  AI_INT_<k> only: an opaque analog-input interface macro sitting next to AI_<k> / FF_AI_<k> (same parent,
              same number) is assumed to read that block's device-named output on its catalogue pin IN (direction I,
              conn 'V' to the AI's '{Device}' variable / the FF_AI's OUT variable). Naming-pair INFERENCE, not XML
              evidence: the sibling's output has no visible reader in the same task (517/517 measured) and the user's
              configuration tool confirms one instance; never overrides a 'decl'/'link' row for IN.
    Runs AFTER direction.run (needs the XML pins' directions) and BEFORE refresh_mirror_kinds. Directions carry
    dir_source 'R' (recovered) for 'decl' rows and 'L' for 'link' rows. The list is only as complete as the evidence."""
    t0 = time.time()
    tot = {"decl": 0, "link": 0, "pair": 0, "blocks": 0, "conflict": 0, "I": 0, "O": 0, "?": 0, "mirror": 0}
    n_opaque = conn.execute("SELECT count(*) FROM block WHERE is_opaque=1").fetchone()[0]
    next_id = (conn.execute("SELECT coalesce(max(id),0) FROM pin").fetchone()[0] or 0) + 1
    for ctrl in ctrl_names:
        opaque = {}
        for bid, path, line_no in conn.execute("SELECT id, path, line_no FROM block WHERE ctrl=? AND is_opaque=1", (ctrl,)):
            opaque[path.replace("/", ".")] = (bid, line_no)
        if not opaque:
            continue
        opaque_ids = {v[0] for v in opaque.values()}
        # --- evidence indexes for this controller
        o_pin_addr = {r[0] for r in conn.execute("""SELECT p.address FROM pin p JOIN block b ON b.id=p.block_id
                                                     WHERE b.ctrl=? AND p.direction='O' AND p.address IS NOT NULL AND p.address<>''""", (ctrl,))}
        var_writer = {r[0] for r in conn.execute("SELECT DISTINCT p.var_id FROM pin p JOIN block b ON b.id=p.block_id WHERE b.ctrl=? AND p.direction='O' AND p.var_id IS NOT NULL", (ctrl,))}
        var_reader = {r[0] for r in conn.execute("SELECT DISTINCT p.var_id FROM pin p JOIN block b ON b.id=p.block_id WHERE b.ctrl=? AND p.direction<>'O' AND p.var_id IS NOT NULL", (ctrl,))}
        io_in = {r[0] for r in conn.execute("SELECT DISTINCT var_id FROM io_point WHERE ctrl=? AND direction='I' AND var_id IS NOT NULL", (ctrl,))}
        io_out = {r[0] for r in conn.execute("SELECT DISTINCT var_id FROM io_point WHERE ctrl=? AND direction='O' AND var_id IS NOT NULL", (ctrl,))}
        egd_cons = {r[0] for r in conn.execute("SELECT DISTINCT local_var_id FROM egd_consumed WHERE consumer_ctrl=? AND local_var_id IS NOT NULL", (ctrl,))}
        hmi = {r[0] for r in conn.execute("SELECT DISTINCT h.var_id FROM hmi_point h JOIN variable v ON v.id=h.var_id WHERE v.ctrl=? AND h.var_id IS NOT NULL", (ctrl,))}
        by_addr = {}
        for vid, name, addr in conn.execute("SELECT id, name, address FROM variable WHERE ctrl=? AND address IS NOT NULL AND address<>''", (ctrl,)):
            by_addr.setdefault(addr, []).append((vid, name))
        lrefs = {}   # (bid, pin) -> set of referrer directions
        for tb, tp, d in conn.execute("""SELECT p.tgt_block_id, p.tgt_pin, p.direction FROM pin p
                                          WHERE p.tgt_block_id IN (SELECT id FROM block WHERE ctrl=? AND is_opaque=1) AND p.tgt_pin IS NOT NULL""", (ctrl,)):
            lrefs.setdefault((tb, tp), set()).add(d or "?")
        # --- decl rows
        rows = {}     # (bid, pname) -> dict
        mirrors = []  # (decl var id, (bid, pname))
        for vid, name, decl, addr, cc, desc, alarm_id in conn.execute(
                "SELECT id, name, decl_connection, address, control_constant, description, alarm_id FROM variable WHERE ctrl=? AND decl_connection IS NOT NULL AND decl_connection<>''", (ctrl,)):
            key, _, pname = decl.rpartition(".")
            if key not in opaque or not pname:
                continue
            bid, line_no = opaque[key]
            row = {"bid": bid, "name": pname, "addr": addr, "desc": desc, "line": line_no, "origin": "decl", "src": "R", "ck": "A", "var": vid, "mirror": None}
            others = [c for c in by_addr.get(addr, []) if c[0] != vid] if addr else []
            if cc:
                row["dir"] = "I"
            elif addr and addr in o_pin_addr:
                row["dir"], row["ck"] = "I", "V"
            elif others:
                pref = [c for c in others if c[0] in var_writer or c[0] in io_in] or [c for c in others if not c[1].startswith("DistributedIO.")] or others
                row["dir"], row["ck"], row["var"], row["mirror"] = "I", "V", pref[0][0], vid
            else:
                out_ev = vid in var_reader or alarm_id is not None or vid in hmi or vid in io_out
                clean = vid not in var_writer and vid not in io_in and vid not in egd_cons
                row["dir"] = "O" if (out_ev and clean) else "?"
            if row["dir"] == "I" and "I" in lrefs.get((bid, pname), ()):   # declared as input but a sibling reads it as an output
                row.update(dir="?", ck="A", var=vid, mirror=None)
                tot["conflict"] += 1
            rows[(bid, pname)] = row
            tot["decl"] += 1
        # --- link rows
        for (bid, tp), dirs in lrefs.items():
            if (bid, tp) in rows:
                continue
            d = "O" if dirs <= {"I", "?"} and "I" in dirs else ("I" if dirs <= {"O", "?"} and "O" in dirs else "?")
            line_no = conn.execute("SELECT line_no FROM block WHERE id=?", (bid,)).fetchone()[0]
            rows[(bid, tp)] = {"bid": bid, "name": tp, "addr": None, "desc": None, "line": line_no, "origin": "link", "src": "L", "ck": "-", "var": None, "mirror": None, "dir": d}
            tot["link"] += 1
        # --- pair rows: opaque AI_INT_<k> beside AI_<k> ('{Device}' output) or FF_AI_<k> (OUT) in the same parent
        for bid, name, parent, line_no in conn.execute(
                "SELECT id, name, parent_id, line_no FROM block WHERE ctrl=? AND is_opaque=1 AND block_type='AI_INT' AND parent_id IS NOT NULL", (ctrl,)):
            k = name[len("AI_INT_"):]
            if not k:
                continue
            src = conn.execute("""SELECT p.var_id, v.name FROM block s JOIN pin p ON p.block_id=s.id JOIN variable v ON v.id=p.var_id
                                  WHERE s.parent_id=? AND s.is_opaque=0 AND p.var_id IS NOT NULL
                                    AND ((s.block_type='AI' AND s.name=? AND p.lib_name='{Device}')
                                      OR (s.block_type='FF_AI' AND s.name=? AND p.name='OUT'))
                                  ORDER BY s.block_type LIMIT 1""", (parent, "AI_" + k, "FF_AI_" + k)).fetchone()
            if not src:
                continue
            if (bid, "IN") in rows:
                tot["conflict"] += 1
                continue
            rows[(bid, "IN")] = {"bid": bid, "name": "IN", "addr": None, "desc": None, "line": line_no, "origin": "pair", "src": "R",
                                 "ck": "V", "var": src[0], "conn": src[1], "mirror": None, "dir": "I"}
            tot["pair"] += 1
        if not rows:
            continue
        order = {"I": 0, "O": 1, "S": 2, "?": 3}
        ins, mir = [], []
        for key in sorted(rows, key=lambda k: (k[0], order.get(rows[k]["dir"], 9), k[1])):
            r = rows[key]
            ins.append((next_id, r["bid"], r["name"], r["dir"], r["src"], r["ck"], r["var"], r["addr"], r["desc"], r["line"], r["origin"], r.get("conn")))
            if r["mirror"] is not None:
                mir.append((r["mirror"], next_id, "I"))
            tot[r["dir"]] = tot.get(r["dir"], 0) + 1
            next_id += 1
        conn.executemany("""INSERT OR IGNORE INTO pin(id, block_id, name, direction, dir_source, conn_kind, var_id, address, description, line_no, origin, connection)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", ins)
        conn.executemany("INSERT OR REPLACE INTO pin_mirror(var_id, pin_id, kind) VALUES(?,?,?)", mir)
        conn.commit()
        tot["blocks"] += len({k[0] for k in rows})
        tot["mirror"] += len(mir)
    log(f"  opaque interface recovered: decl={tot['decl']} link={tot['link']} pair={tot['pair']} blocks={tot['blocks']}/{n_opaque} "
        f"I={tot['I']} O={tot['O']} ?={tot['?']} mirrors={tot['mirror']} conflicts={tot['conflict']}  {time.time()-t0:.1f}s")
    return tot


def refresh_mirror_kinds(conn, log=print):
    """pin_mirror.kind = the pin's inferred direction; must run after direction.run()."""
    conn.execute("""UPDATE pin_mirror SET kind = coalesce((SELECT CASE WHEN p.direction IN ('I','O') THEN p.direction ELSE '?' END
                                                            FROM pin p WHERE p.id=pin_mirror.pin_id), '?')""")
    conn.commit()
    k = {r[0]: r[1] for r in conn.execute("SELECT kind, count(*) FROM pin_mirror GROUP BY kind")}
    log(f"  pin mirror kinds: I={k.get('I',0)} O={k.get('O',0)} ?={k.get('?',0)}")


def run(conn, root, ctrls, log=print):
    ctrl_names = [c.name for c in ctrls]
    purge_recovered(conn, ctrl_names, log)
    link_declared_pins(conn, ctrl_names, log)
    build_pin_mirror(conn, ctrl_names, log)
    inlist = ",".join("'" + n.replace("'", "''") + "'" for n in ctrl_names)
    _upd(conn, f"""UPDATE variable SET producer_var_id=(
            SELECT p.id FROM variable p WHERE p.ctrl=variable.device_name
              AND p.name=substr(variable.name, length(variable.device_name)+2))
          WHERE device_name IS NOT NULL AND device_name<>'' AND ctrl IN ({inlist})
            AND substr(name,1,length(device_name)+1)=device_name||'.'""", log, "variable.producer_var_id")
    _upd(conn, f"""UPDATE io_point SET var_id=(SELECT v.id FROM variable v WHERE v.ctrl=io_point.ctrl AND v.name=io_point.connection)
          WHERE var_id IS NULL AND connection IS NOT NULL AND connection<>'' AND ctrl IN ({inlist})""",
         log, "io_point.var_id")
    _upd(conn, f"""UPDATE egd_produced SET var_id=(
            SELECT v.id FROM variable v JOIN egd_exchange x ON x.id=egd_produced.exchange_pk
             WHERE v.ctrl=x.producer_ctrl AND v.name=egd_produced.var_name)
          WHERE var_id IS NULL AND exchange_pk IN (SELECT id FROM egd_exchange WHERE producer_ctrl IN ({inlist}))""",
         log, "egd_produced.var_id")
    _upd(conn, f"""UPDATE egd_consumed SET producer_var_id=(
            SELECT v.id FROM variable v WHERE v.ctrl=egd_consumed.producer_ctrl AND v.name=egd_consumed.var_name)
          WHERE producer_var_id IS NULL AND consumer_ctrl IN ({inlist})""", log, "egd_consumed.producer_var_id")
    _upd(conn, f"""UPDATE egd_consumed SET local_var_id=(
            SELECT v.id FROM variable v WHERE v.ctrl=egd_consumed.consumer_ctrl
               AND (v.name=egd_consumed.producer_ctrl||'.'||egd_consumed.var_name
                    OR (v.address=egd_consumed.local_address AND v.device_name=egd_consumed.producer_ctrl)))
          WHERE local_var_id IS NULL AND consumer_ctrl IN ({inlist})""", log, "egd_consumed.local_var_id")
    _upd(conn, """UPDATE hmi_point SET var_id=(SELECT v.id FROM variable v WHERE v.full_name=hmi_point.full_point)
          WHERE var_id IS NULL""", log, "hmi_point.var_id")
    _upd(conn, """UPDATE watch SET var_id=(SELECT v.id FROM variable v WHERE v.ctrl=watch.ctrl AND v.name=watch.var_name)
          WHERE var_id IS NULL""", log, "watch.var_id")


def rebuild_fts(conn, log=print):
    t0 = time.time()
    try:
        conn.execute("DELETE FROM variable_fts")
    except Exception as e:
        log(f"  fts skipped: {e}")
        return
    conn.execute("""INSERT INTO variable_fts(rowid, full_name, description, alias, device_tag, alarm_text)
        SELECT v.id, v.full_name, coalesce(v.description,''), coalesce(v.alias,''),
               coalesce((SELECT group_concat(DISTINCT p.device_tag) FROM io_point p WHERE p.var_id=v.id), ''),
               trim(coalesce(v.potential_causes,'')||' '||coalesce(v.operator_action,'')||' '||coalesce(v.consequence,''))
        FROM variable v""")
    conn.commit()
    n = conn.execute("SELECT count(*) FROM variable_fts").fetchone()[0]
    log(f"  variable_fts rows {n}  {time.time()-t0:.1f}s")
