# -*- coding: utf-8 -*-
"""Post-parse cross-table fix-ups + FTS rebuild.

Stage modules resolve their own var_id where they can; this is the safety net that runs after all stages:
  * variable.producer_var_id  (EGD consumed copies 'G11.L27QE1_A' with device_name='G11' -> G11's own row)
  * io_point / egd_produced / egd_consumed / hmi_point / watch .var_id where still NULL
  * variable_fts rebuild (full_name, description, alias, device_tag, alarm_text)
"""
import re
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
              For AI_<k> pairs (IN variable '<stem>_AI') the outputs are recovered too: OUT -> variable 'ai_<stem>',
              DEVICE_STATUS -> '<stem>_DS' (direction O), but only when that variable's ReferencedIn lists the block's
              program while no visible pin of that program references it (so the reference must be inside the
              encrypted block). FF_AI pairs have no such naming and get IN only.
    Runs AFTER direction.run (needs the XML pins' directions) and BEFORE refresh_mirror_kinds. Directions carry
    dir_source 'R' (recovered) for 'decl' rows and 'L' for 'link' rows. The list is only as complete as the evidence."""
    t0 = time.time()
    tot = {"decl": 0, "link": 0, "pair": 0, "pair_out": 0, "pair_skip": 0, "blocks": 0, "conflict": 0, "I": 0, "O": 0, "?": 0, "mirror": 0}
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
        prog_name = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM program WHERE ctrl=?", (ctrl,))}
        for bid, name, parent, line_no, prog_id in conn.execute(
                "SELECT id, name, parent_id, line_no, program_id FROM block WHERE ctrl=? AND is_opaque=1 AND block_type='AI_INT' AND parent_id IS NOT NULL", (ctrl,)):
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
            # outputs (AI pairs only): OUT -> ai_<stem>, DEVICE_STATUS -> <stem>_DS, evidenced by ReferencedIn
            if not src[1].endswith("_AI"):
                continue
            stem = src[1][:-3]
            for pname, vname in (("OUT", "ai_" + stem), ("DEVICE_STATUS", stem + "_DS")):
                v = conn.execute("SELECT id, referenced_in FROM variable WHERE ctrl=? AND name=?", (ctrl, vname)).fetchone()
                if not v or prog_name.get(prog_id) not in (v[1] or "").split(","):
                    tot["pair_skip"] += 1
                    continue
                vis = conn.execute("""SELECT 1 FROM pin p JOIN block b ON b.id=p.block_id
                                      WHERE p.var_id=? AND b.program_id=? AND p.origin IS NULL LIMIT 1""", (v[0], prog_id)).fetchone()
                if vis:
                    tot["pair_skip"] += 1
                    continue
                if (bid, pname) in rows:
                    tot["conflict"] += 1
                    continue
                rows[(bid, pname)] = {"bid": bid, "name": pname, "addr": None, "desc": None, "line": line_no, "origin": "pair", "src": "R",
                                      "ck": "V", "var": v[0], "conn": vname, "mirror": None, "dir": "O"}
                tot["pair_out"] += 1
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
    log(f"  opaque interface recovered: decl={tot['decl']} link={tot['link']} pair={tot['pair']}(+{tot['pair_out']} outputs, {tot['pair_skip']} skipped) blocks={tot['blocks']}/{n_opaque} "
        f"I={tot['I']} O={tot['O']} ?={tot['?']} mirrors={tot['mirror']} conflicts={tot['conflict']}  {time.time()-t0:.1f}s")
    return tot


_VOTE_TYPES = ("2oo3_Basic",)


def _vnorm(s):
    return s.lower().replace("level", "lvl")


def recover_vote_pins(conn, ctrl_names, log=print):
    """Opaque 2oo3 analog voters ('2oo3_Basic', task 'FNCTN_<stem>'): their interface is fixed (INA/BQA, INB/BQB, INC/BQC,
    HI_LIMIT, HYST -> OUT; inside INx -> HI_LO_MON_n.IN, BQx -> OR_n.IN3, OR_n -> VOTE_1.INn, M=2) and one instance was
    confirmed in the configuration tool, so the wiring of every instance is inferred from the task name and the variables
    the program references only inside encrypted blocks ('hidden': ReferencedIn lists the program, no plaintext pin there):
      INA/INB/INC  the one hidden variable '(ai_)<stem>(_Alt)<L>(Crctd)(_suffix)' per letter (shared by every voter of the
                   task, as the tool's Where-Used showed); skipped when a letter has 0 or >1 candidates (e.g. four
                   transmitter sets in one task).
      BQA/BQB/BQC  the hidden alarm sub-variable '<input>.BQ' or the hidden variable '<input>_BQ' (whichever the program
                   references only inside encrypted blocks), else the '<input>.BQ' sub-variable when it exists, else the
                   field '<input>.BQ' as conn_kind 'D' without a variable (the macro always wires the bad-quality flag).
      HYST         the one hidden 'k_…<stem>…_HYST*' constant (shared by the task's voters).
      HI_LIMIT     only when the task has ONE voter and ONE hidden HIGH-side 'k_…<stem>…_(H|HH|HHH|3H|4H)_SP' constant
                   (several voters = several set-points whose assignment the tool alone can show). A task whose only
                   set-points are low-side ('_L_SP', '_LL_SP'…) gets neither HI_LIMIT nor OUT: the 2oo3_Basic macro
                   has no LO_LIMIT pin, and a low set-point on HI_LIMIT was the one wrong wire this rule produced.
      OUT          only when the task has ONE voter and ONE variable 'PRO_<stem>[n]Hi' with no writer anywhere; when
                   HI_LIMIT is inferred too, the set-point level (H→Hi, HH→2Hi, HHH/3H→3Hi, 4H→4Hi) must equal the
                   output's level, otherwise neither pin is inferred.
    Rows get origin='vote', dir_source='R', direction I (OUT: O). Runs after recover_opaque_pins, before load_xref, whose
    verified rows replace these."""
    t0 = time.time()
    tot = {"blocks": 0, "rows": 0, "in": 0, "bq": 0, "bqf": 0, "hyst": 0, "hi": 0, "out": 0, "skip_in": 0, "skip_hi": 0, "skip_out": 0,
           "notask": 0, "low_side": 0, "level_mismatch": 0}
    next_id = (conn.execute("SELECT coalesce(max(id),0) FROM pin").fetchone()[0] or 0) + 1
    for ctrl in ctrl_names:
        blocks = conn.execute(f"""SELECT id, path, line_no, program_id FROM block WHERE ctrl=? AND is_opaque=1
                                  AND block_type IN ({','.join('?' * len(_VOTE_TYPES))}) ORDER BY path""", (ctrl, *_VOTE_TYPES)).fetchall()
        if not blocks:
            continue
        prog_id = {r[1]: r[0] for r in conn.execute("SELECT id, name FROM program WHERE ctrl=?", (ctrl,))}
        # hidden variables per program: ReferencedIn lists it, no plaintext pin of the variable in it
        seen = set(conn.execute("""SELECT DISTINCT p.var_id, b.program_id FROM pin p JOIN block b ON b.id=p.block_id
                                   WHERE b.ctrl=? AND p.var_id IS NOT NULL AND p.origin IS NULL""", (ctrl,)))
        writers = {r[0] for r in conn.execute("SELECT DISTINCT var_id FROM pin WHERE direction='O' AND var_id IS NOT NULL AND block_id IN (SELECT id FROM block WHERE ctrl=?)", (ctrl,))}
        allv = conn.execute("SELECT id, name, referenced_in FROM variable WHERE ctrl=?", (ctrl,)).fetchall()
        byname = {v[1]: v[0] for v in allv}
        hidden = {}   # program_id -> [(vid, name, norm)]
        refs = {}     # program_id -> [(vid, name, norm)] (all variables referenced in the program)
        for vid, name, refd in allv:
            for p in (refd or "").split(","):
                pid = prog_id.get(p)
                if pid is None:
                    continue
                refs.setdefault(pid, []).append((vid, name, _vnorm(name)))
                if (vid, pid) not in seen:
                    hidden.setdefault(pid, []).append((vid, name, _vnorm(name)))
        tasks = {}
        for bid, path, line_no, pid in blocks:
            parts = path.split("/")
            if len(parts) < 3 or not parts[-2].startswith("FNCTN_"):
                tot["notask"] += 1
                continue
            tasks.setdefault((pid, parts[-2]), []).append((bid, path, line_no))
        ins = []
        for (pid, task), insts in tasks.items():
            stem = task[len("FNCTN_"):]
            nb = _vnorm(stem)
            hid = [h for h in hidden.get(pid, []) if nb in h[2]]
            inputs = {}
            for L in "ABC":
                cand = [h for h in hid if re.fullmatch(rf"(ai_)?{re.escape(nb)}(_alt)?{L.lower()}(crctd)?(_[a-z0-9]+)?", h[2]) and not h[2].endswith("_bq")]
                if len(cand) == 1:
                    inputs[L] = cand[0]
                else:
                    tot["skip_in"] += 1
            hyst = [h for h in hid if re.search(r"_hyst\d*$", h[2]) and "." not in h[1]]   # not the '<var>.HYST' sub-variables
            all_sps = [h for h in hid if h[2].endswith("_sp") and "." not in h[1]]
            sps = [h for h in all_sps if re.search(r"_(h|hh|hhh|3h|4h)_sp$", h[2])]          # high-side only
            outs = [r for r in refs.get(pid, []) if re.fullmatch(rf"pro_{re.escape(nb)}\d*hi", r[2]) and r[0] not in writers]
            single = len(insts) == 1
            low_side = bool(all_sps) and not sps
            if low_side:
                tot["low_side"] += 1
                outs = []
            if not single or len(sps) != 1:
                tot["skip_hi"] += 1
            if not single or len(outs) != 1:
                tot["skip_out"] += 1
            if single and len(sps) == 1 and len(outs) == 1:
                lvl = {"h": "", "hh": "2", "hhh": "3", "3h": "3", "4h": "4"}[re.search(r"_(h|hh|hhh|3h|4h)_sp$", sps[0][2]).group(1)]
                if not outs[0][2].endswith(f"{lvl}hi") or (lvl == "" and re.search(r"\dhi$", outs[0][2])):
                    tot["level_mismatch"] += 1
                    sps, outs = [], []
            for bid, path, line_no in insts:
                rows = []
                for L, (vid, name, _) in inputs.items():
                    rows.append(("IN" + L, "I", "V", name, vid)); tot["in"] += 1
                    hidset = {h[0] for h in hid}
                    bqn = next((c for c in (name + ".BQ", name + "_BQ") if byname.get(c) in hidset), None)
                    if bqn is None and byname.get(name + ".BQ") is not None:
                        bqn = name + ".BQ"
                    if bqn is not None:
                        rows.append(("BQ" + L, "I", "V", bqn, byname[bqn])); tot["bq"] += 1
                    else:
                        rows.append(("BQ" + L, "I", "D", name + ".BQ", None)); tot["bqf"] += 1
                if len(hyst) == 1:
                    rows.append(("HYST", "I", "V", hyst[0][1], hyst[0][0])); tot["hyst"] += 1
                if single and len(sps) == 1:
                    rows.append(("HI_LIMIT", "I", "V", sps[0][1], sps[0][0])); tot["hi"] += 1
                if single and len(outs) == 1:
                    rows.append(("OUT", "O", "V", outs[0][1], outs[0][0])); tot["out"] += 1
                if not rows:
                    continue
                have = {r[0] for r in conn.execute("SELECT name FROM pin WHERE block_id=?", (bid,))}
                for pname, d, ck, connection, vid in rows:
                    if pname in have:
                        continue
                    ins.append((next_id, bid, pname, d, "R", ck, connection, vid, line_no, "vote"))
                    next_id += 1
                tot["blocks"] += 1
        if ins:
            conn.executemany("""INSERT OR IGNORE INTO pin(id, block_id, name, direction, dir_source, conn_kind, connection, var_id, line_no, origin)
                                VALUES(?,?,?,?,?,?,?,?,?,?)""", ins)
            conn.commit()
            tot["rows"] += len(ins)
    log(f"  2oo3 voter interface inferred: rows={tot['rows']} blocks={tot['blocks']} (in={tot['in']} bq={tot['bq']}+{tot['bqf']} field hyst={tot['hyst']} "
        f"hi_limit={tot['hi']} out={tot['out']}; skipped letters={tot['skip_in']} hi_limit={tot['skip_hi']} out={tot['skip_out']} tasks, "
        f"low-side tasks={tot['low_side']}, level mismatch={tot['level_mismatch']}, not in a FNCTN_ task={tot['notask']})  {time.time()-t0:.1f}s")
    return tot


XREF_CSV = "xref_manual.csv"
XREF_HEADER = ["ctrl", "variable", "block_path", "pin", "direction", "note", "grade"]
XREF_GRADES = ("tool", "mirror", "infer")


def xref_grade(note, grade=""):
    """Evidence grade of a row: the explicit `grade` column, else derived from the note prefix (tool cross-reference /
    tool logic sheet -> 'tool'; 'mirrored from' -> 'mirror'; 'inferred' -> 'infer'; anything else -> 'infer')."""
    g = (grade or "").strip().lower()
    if g in XREF_GRADES:
        return g
    n = (note or "").strip().lower()
    if n.startswith("tool "):
        return "tool"
    if n.startswith("mirrored"):
        return "mirror"
    return "infer"


def _pm():
    try:
        from . import parse_manual as pm
    except ImportError:
        from dcdas import parse_manual as pm
    return pm


def load_xref(conn, repo_dir, ctrl_names, log=print):
    """tools/xref_manual.csv: connections the user verified in the configuration tool's cross-reference (Where Used) that the
    XML cannot show (pins of a fully encrypted UserBlock instance, e.g. a 4oo20 voter reading a temperature). Each valid row
    becomes a pin row with origin='xref', conn_kind 'V' to the named variable, description = note, and dir_source by
    evidence grade (xref_grade): 'T' when the tool itself showed the connection ('tool'), 'M' when the row is mirrored from
    another controller or inferred from a sibling/pattern ('mirror'/'infer'). Rules: the block must exist and be opaque (plaintext blocks take their pins from the XML); a plaintext pin of the
    same name wins; a recovered decl/link/pair/vote row of the same name is replaced (verified beats inferred). A variable
    written '<var>.<FIELD>' (e.g. 'ai_X.BQ', the bad-quality field the plaintext XML also wires as a field) needs only the
    base variable to exist and becomes a conn_kind 'D' row without var_id. Reloaded on every
    build (purge_recovered drops every origin row first); rows of controllers outside this build are left alone."""
    from pathlib import Path
    pm = _pm()
    path = pm._tools_dir(Path(repo_dir)) / XREF_CSV
    rows = pm.read_csv(path, XREF_HEADER)
    tot = {"rows": 0, "loaded": 0, "skipped": 0, "replaced": 0}
    next_id = (conn.execute("SELECT coalesce(max(id),0) FROM pin").fetchone()[0] or 0) + 1
    for r in rows:
        ctrl, var, bpath, pin, d, note, grade = (r[h] for h in XREF_HEADER)
        if ctrl not in ctrl_names:
            continue
        tot["rows"] += 1
        src = "T" if xref_grade(note, grade) == "tool" else "M"
        why = None
        b = conn.execute("SELECT id, is_opaque, line_no FROM block WHERE ctrl=? AND path=?", (ctrl, bpath)).fetchone()
        v = conn.execute("SELECT id FROM variable WHERE ctrl=? AND name=?", (ctrl, var)).fetchone()
        field = False
        if not v and "." in var:   # '<var>.<FIELD>' with no such sub-variable -> field reference (conn_kind 'D'), base must exist
            field = conn.execute("SELECT 1 FROM variable WHERE ctrl=? AND name=?", (ctrl, var.rsplit(".", 1)[0])).fetchone() is not None
        if d not in ("I", "O"):
            why = f"direction must be I or O, got {d!r}"
        elif not b:
            why = "block not found"
        elif not b[1]:
            why = "block is plaintext; its pins come from the XML"
        elif not v and not field:
            why = "variable not found"
        elif not pin:
            why = "empty pin name"
        if why:
            tot["skipped"] += 1
            log(f"  xref skipped {ctrl} {bpath}.{pin}: {why}")
            continue
        ex = conn.execute("SELECT id, origin FROM pin WHERE block_id=? AND name=?", (b[0], pin)).fetchone()
        if ex:
            if ex[1] is None:
                tot["skipped"] += 1
                log(f"  xref skipped {ctrl} {bpath}.{pin}: plaintext pin already indexed")
                continue
            conn.execute("DELETE FROM pin WHERE id=?", (ex[0],))
            conn.execute("DELETE FROM pin_mirror WHERE pin_id=?", (ex[0],))
            tot["replaced"] += 1
        conn.execute("""INSERT INTO pin(id, block_id, name, direction, dir_source, conn_kind, connection, var_id, description, line_no, origin)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (next_id, b[0], pin, d, src, "D" if field else "V", var, v[0] if v else None, note or None, b[2], "xref"))
        tot[src] = tot.get(src, 0) + 1
        next_id += 1
        tot["loaded"] += 1
    conn.commit()
    log(f"  xref loaded: {tot['loaded']} of {tot['rows']} rows (skipped {tot['skipped']}, replaced {tot['replaced']}; "
        f"tool-verified T={tot.get('T', 0)}, mirrored/inferred M={tot.get('M', 0)}) from {path.name}")
    return tot


def csv_fingerprints(repo_dir):
    """sha1 of the hand-maintained CSVs the index depends on (xref rows, direction overrides / table): stored in meta at
    build / xref-reload time so `status` can tell when the index no longer reflects them."""
    import hashlib
    from pathlib import Path
    pm = _pm()
    tools = pm._tools_dir(Path(repo_dir))
    out = {}
    for name in (XREF_CSV, pm.OVERRIDE_CSV, pm.TABLE_CSV):
        p = tools / name
        out[name] = hashlib.sha1(p.read_bytes()).hexdigest()[:12] if p.exists() else None
    return out


def xref_row_count(repo_dir, ctrl_names):
    """Data rows of xref_manual.csv for these controllers (the number load_xref is expected to load when every row is valid)."""
    from pathlib import Path
    pm = _pm()
    rows = pm.read_csv(pm._tools_dir(Path(repo_dir)) / XREF_CSV, XREF_HEADER)
    return sum(1 for r in rows if r["ctrl"] in ctrl_names)


def xref_validate(conn, repo_dir, ctrl_names, log=print):
    """Dry run of load_xref: report every row that would be skipped (block missing / plaintext, variable missing, bad
    direction, plaintext pin) without touching the index. Returns the list of (ctrl, block_path, pin, why)."""
    from pathlib import Path
    pm = _pm()
    rows = pm.read_csv(pm._tools_dir(Path(repo_dir)) / XREF_CSV, XREF_HEADER)
    bad, seen, n = [], set(), 0
    for r in rows:
        ctrl, var, bpath, pin, d, note, grade = (r[h] for h in XREF_HEADER)
        if ctrl not in ctrl_names:
            continue
        n += 1
        b = conn.execute("SELECT id, is_opaque FROM block WHERE ctrl=? AND path=?", (ctrl, bpath)).fetchone()
        v = conn.execute("SELECT 1 FROM variable WHERE ctrl=? AND name=?", (ctrl, var)).fetchone()
        field = (not v and "." in var and conn.execute("SELECT 1 FROM variable WHERE ctrl=? AND name=?", (ctrl, var.rsplit(".", 1)[0])).fetchone() is not None)
        why = None
        if d not in ("I", "O"):
            why = f"direction {d!r}"
        elif not b:
            why = "block not found"
        elif not b[1]:
            why = "block is plaintext"
        elif not v and not field:
            why = "variable not found"
        elif not pin:
            why = "empty pin"
        elif (ctrl, bpath, pin) in seen:
            why = "duplicate row"
        elif conn.execute("SELECT 1 FROM pin WHERE block_id=? AND name=? AND origin IS NULL", (b[0], pin)).fetchone():
            why = "plaintext pin already indexed"
        elif grade and grade.strip().lower() not in XREF_GRADES:
            why = f"unknown grade {grade!r}"
        seen.add((ctrl, bpath, pin))
        if why:
            bad.append((ctrl, bpath, pin, why))
            log(f"  xref INVALID {ctrl} {bpath}.{pin}: {why}")
    log(f"  xref validate: {n} rows, {len(bad)} invalid")
    return bad


_WU_RE = re.compile(r"^\s*(?P<path>[A-Za-z0-9_.]+)\s*(?:\(.*\))?\s*$")


def xref_paste(conn, repo_dir, text_path, ctrl, date=None, dry_run=False, log=print):
    """Turn a pasted Where-Used tree from the configuration tool into xref_manual.csv rows. Input: first non-empty line =
    the variable ('Program.Task.Var (…)' or 'Var (…)'), every later line 'Program.Task[.Block…].PIN (…)'. A line whose block
    is not in the index is matched on shorter prefixes (the tail is then the path inside the encrypted block, kept in the
    note). Appended: lines on an opaque block whose pin is not indexed yet (direction assumed I, said so in the note) and
    lines on a recovered pin (origin decl/link/pair/vote): the tool confirms the inference, so a verified row replaces it on
    the next build (direction kept from the recovered pin). Plaintext and xref pins are reported only. Returns the
    appended rows."""
    from pathlib import Path
    pm = _pm()
    path = pm._tools_dir(Path(repo_dir)) / XREF_CSV
    lines = [l.rstrip("\n") for l in open(text_path, encoding="utf-8-sig") if l.strip()]
    if not lines:
        raise SystemExit("empty file")
    m = _WU_RE.match(lines[0])
    var = (m.group("path") if m else lines[0].strip()).rsplit(".", 1)[-1]
    if not conn.execute("SELECT 1 FROM variable WHERE ctrl=? AND name=?", (ctrl, var)).fetchone():
        raise SystemExit(f"variable {ctrl}.{var} not in the index (first line must name the variable)")
    have = {(r["ctrl"], r["block_path"], r["pin"]) for r in pm.read_csv(path, XREF_HEADER)}
    added = []
    for raw in lines[1:]:
        m = _WU_RE.match(raw)
        if not m:
            log(f"  ? unparsed: {raw.strip()}")
            continue
        toks = m.group("path").split(".")
        if toks[0] == "EGD" or len(toks) < 3:
            log(f"  - {m.group('path')}: not a block pin")
            continue
        b, cut = None, 0
        for cut in range(len(toks) - 1, 1, -1):
            b = conn.execute("SELECT id, is_opaque, path FROM block WHERE ctrl=? AND path=?", (ctrl, "/".join(toks[:cut]))).fetchone()
            if b:
                break
        if not b:
            log(f"  ? {m.group('path')}: block not found")
            continue
        pin, inner = toks[-1], ".".join(toks[cut:-1])
        if inner:   # a child line of the tree: the path inside the encrypted block under the previous interface pin
            if added and added[-1][2] == b[2]:
                added[-1][5] += f"; inside -> {inner}.{pin}"
                log(f"    inside {b[2]}: {inner}.{pin} (noted on {added[-1][3]})")
            else:
                log(f"  - {m.group('path')}: path inside the encrypted block, nothing to add")
            continue
        ex = conn.execute("""SELECT p.origin, p.direction, v.name FROM pin p LEFT JOIN variable v ON v.id=p.var_id
                             WHERE p.block_id=? AND p.name=?""", (b[0], pin)).fetchone()
        if ex and ex[0] in (None, "xref"):
            log(f"  = {b[2]}.{pin}: already indexed ({ex[0] or 'plaintext'})")
            continue
        if not b[1]:
            log(f"  ! {b[2]}.{pin}: plaintext block but pin not indexed (XML gap, not an xref case)")
            continue
        key = (ctrl, b[2], pin)
        if key in have:
            log(f"  = {b[2]}.{pin}: already in {path.name}")
            continue
        stamp = f"tool cross-reference {date or ''}".strip()
        if ex:   # recovered (decl/link/pair) pin: the tool confirms it -> verified row, replaces the recovered one on build
            d = ex[1] if ex[1] in ("I", "O") else "I"
            note = f"{stamp}; confirms the recovered ({ex[0]}) pin, direction kept from it"
            if ex[2] and ex[2] != var:
                note += f"; NOTE recovered row had {ex[2]}"
                log(f"  ! {b[2]}.{pin}: recovered row reads {ex[2]}, tool says {var} (verified wins)")
            added.append([ctrl, var, b[2], pin, d, note, "tool"])
            log(f"  ^ {b[2]}.{pin} <- {var} ({d}, upgrades {ex[0]} -> xref)")
        else:
            added.append([ctrl, var, b[2], pin, "I", stamp + "; direction assumed I", "tool"])
            log(f"  + {b[2]}.{pin} <- {var} (I assumed)")
        have.add(key)
    if added and not dry_run:
        import csv
        new = not path.exists()
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            if new:
                w.writerow(XREF_HEADER)
            w.writerows(added)
        log(f"  appended {len(added)} row(s) to {path} - rebuild the index to load them")
    elif added:
        log(f"  dry run: {len(added)} row(s) not written")
    return added


def refresh_mirror_kinds(conn, log=print):
    """pin_mirror.kind = the pin's inferred direction; must run after direction.run()."""
    conn.execute("""UPDATE pin_mirror SET kind = coalesce((SELECT CASE WHEN p.direction IN ('I','O') THEN p.direction ELSE '?' END
                                                            FROM pin p WHERE p.id=pin_mirror.pin_id), '?')""")
    conn.commit()
    k = {r[0]: r[1] for r in conn.execute("SELECT kind, count(*) FROM pin_mirror GROUP BY kind")}
    log(f"  pin mirror kinds: I={k.get('I',0)} O={k.get('O',0)} ?={k.get('?',0)}")


def vote_io_directions(conn, ctrl_names, log=print):
    """I/O points still '?' after the name/parameter rules: decide from the logic that uses the linked variable.
    Only readers (direction I/S pins) and no ordinary-block writer -> the point feeds the logic -> 'I'; only ordinary-block
    writers and no reader -> the logic drives the point -> 'O'. Both or neither -> stays '?'. Source 'V' (vote) so the
    `io` listing and the signal card can tell an inferred direction from a named one. Runs after direction.run."""
    t0 = time.time()
    tot = {"I": 0, "O": 0}
    for ctrl in ctrl_names:
        upd = []
        for pid, nw, nr in conn.execute("""
                SELECT i.id,
                       (SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE p.var_id=i.var_id AND p.direction='O'
                          AND (b.kind='block' OR p.origin IS NOT NULL)),
                       (SELECT count(*) FROM pin p WHERE p.var_id=i.var_id AND p.direction IN ('I','S'))
                FROM io_point i WHERE i.ctrl=? AND i.direction='?' AND i.var_id IS NOT NULL""", (ctrl,)):
            if nr and not nw:
                upd.append(("I", pid)); tot["I"] += 1
            elif nw and not nr:
                upd.append(("O", pid)); tot["O"] += 1
        conn.executemany("UPDATE io_point SET direction=?, dir_source='V' WHERE id=?", upd)
        conn.commit()
    log(f"  io direction by logic vote: I={tot['I']} O={tot['O']}  {time.time()-t0:.1f}s")
    return tot


def build_external_nodes(conn, log=print):
    """Nodes named by the checkout but absent from it: EGD producers the consumers bind to, and the first segment of HMI
    navigation points that is no indexed controller (a data concentrator, a gateway, a controller not checked out).
    hmi_point.resolved_via='external' marks their points so 'unresolved' counts only real gaps."""
    conn.execute("DELETE FROM external_node")
    conn.execute("""INSERT INTO external_node(name, kind, n)
                    SELECT producer_ctrl, 'egd_producer', count(*) FROM egd_consumed
                    WHERE producer_ctrl NOT IN (SELECT name FROM controller) GROUP BY producer_ctrl""")
    conn.execute("""INSERT OR REPLACE INTO external_node(name, kind, n)
                    SELECT substr(full_point, 1, instr(full_point, '.')-1), 'hmi_prefix', count(*) FROM hmi_point
                    WHERE var_id IS NULL AND source='navcsv' AND instr(full_point, '.')>1
                      AND substr(full_point, 1, instr(full_point, '.')-1) NOT IN (SELECT name FROM controller)
                    GROUP BY 1 HAVING count(*) >= 5""")
    cur = conn.execute("""UPDATE hmi_point SET resolved_via='external' WHERE var_id IS NULL AND source='navcsv' AND instr(full_point, '.')>1
                          AND substr(full_point, 1, instr(full_point, '.')-1) IN (SELECT name FROM external_node WHERE kind='hmi_prefix')""")
    conn.commit()
    n = conn.execute("SELECT count(*) FROM external_node").fetchone()[0]
    log(f"  external nodes: {n} (hmi points marked external: {cur.rowcount})")
    return n


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
    _upd(conn, """UPDATE hmi_point SET var_id=(SELECT v.id FROM variable v WHERE v.full_name=hmi_point.full_point), resolved_via='full'
          WHERE var_id IS NULL AND EXISTS(SELECT 1 FROM variable v WHERE v.full_name=hmi_point.full_point)""", log, "hmi_point.var_id")
    build_external_nodes(conn, log)
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
