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


def refresh_mirror_kinds(conn, log=print):
    """pin_mirror.kind = the pin's inferred direction; must run after direction.run()."""
    conn.execute("""UPDATE pin_mirror SET kind = coalesce((SELECT CASE WHEN p.direction IN ('I','O') THEN p.direction ELSE '?' END
                                                            FROM pin p WHERE p.id=pin_mirror.pin_id), '?')""")
    conn.commit()
    k = {r[0]: r[1] for r in conn.execute("SELECT kind, count(*) FROM pin_mirror GROUP BY kind")}
    log(f"  pin mirror kinds: I={k.get('I',0)} O={k.get('O',0)} ?={k.get('?',0)}")


def run(conn, root, ctrls, log=print):
    ctrl_names = [c.name for c in ctrls]
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
