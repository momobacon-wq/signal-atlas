# -*- coding: utf-8 -*-
"""SQLite index -> static JSON shards for the GitHub Pages site (docs/data/).  See CONTRACT.md.

Layout (all JSON compact, UTF-8, no local absolute paths — the exporter aborts if it finds one):
  manifest.json                 build hash, source info, controllers, shard config, encrypted programs, block types
  names/<CTRL>.json             {ctrl, rows:[[name, desc, flags, alias, datatype]]}   (search index, lazy per controller)
  var/<hhh>.json                {v:{full_name: card}}     shard = sha1(full_name)[:3]  (4096 shards)
  task/<hhh>.json               {t:{tkey:{n, vd:{full_name: desc}, b:{key: block}}}}  tkey = ctrl|Program/Task, shard = sha1(tkey)[:3];
                                b in document order, root first; pins tuple = [name, dir, src, conn_kind, connection, var_full, tgt_key, tgt_pin, address, alias, desc]
  program/<CTRL>.json           {ctrl, programs:[{name, lib, file, enc, help, tasks:[{name, type, drg, blocks:[[key,name,type,kind,opaque]]}]}]}
  io/<CTRL>.json                {ctrl, modules:[{name, id, cabinet, red, boards:[{name, hw, pos, points:[[name, dir, conn, tag, addr, type, lo, hi, screws]]}]}]}
  screen/<hh>.json              {s:{screen: {menu:[...], points:[[full_name, source]]}}}  shard = sha1(screen)[:2]
  alarm/<CTRL>.json             {ctrl, rows:[[name, desc, cls, def, area, urgency]]}
flags bits in names rows: 1=has_writer 2=has_io 4=has_egd 8=has_hmi 16=has_alarm 32=const 64=egd_copy 128=in_encrypted
"""
import base64
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from collections import defaultdict
from pathlib import Path

from .db import get_meta

KDF_ITER = 200_000
CHECK_TEXT = b"signal-atlas-ok"


def derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITER).derive(passphrase.encode("utf-8"))


def seal(key: bytes, plain: bytes, aad: str) -> bytes:
    """12-byte random IV || AES-256-GCM ciphertext+tag (WebCrypto layout). AAD = relative path (or 'check')."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    return iv + AESGCM(key).encrypt(iv, plain, aad.encode("utf-8"))

ABS_RE = re.compile(r'(?<![A-Za-z])[A-Za-z]:(?:\\|/)|/c/Users/|\\Users\\')
MAX_REFS = 400          # writers/readers per card (rest counted in r_more / w_more)
MAX_FILE = 1_000_000    # bytes per shard file target (soft; reported)
VAR_SHARDS = 3          # hex digits
SCREEN_SHARDS = 2


def _dump(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _shard(key, n=VAR_SHARDS):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:n]


class Writer:
    """Writes each data file; with a key: gzip -> AES-GCM -> '<rel>.bin'. Keeps per-file plaintext digests so the
    build hash stays deterministic even though IVs are random."""

    def __init__(self, out: Path, log, key: bytes = None):
        self.out, self.log, self.files, self.total, self.wire, self.key = out, log, [], 0, 0, key
        self.digests = []

    def write(self, rel: str, text: str):
        if ABS_RE.search(text):
            m = ABS_RE.search(text)
            raise SystemExit(f"ABORT: local absolute path in {rel}: ...{text[max(0, m.start()-60):m.end()+60]}...")
        b = text.encode("utf-8")
        self.digests.append((rel, hashlib.sha256(b).hexdigest()))
        self.files.append((rel, len(b)))
        self.total += len(b)
        if self.key:
            blob = seal(self.key, gzip.compress(b, 6, mtime=0), rel)
            p = self.out / (rel + ".bin")
        else:
            blob = b
            p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.write(blob)
        self.wire += len(blob)
        if len(b) > MAX_FILE and not rel.startswith("names/"):
            self.log(f"  WARN {rel} is {len(b)/1e6:.1f} MB (> {MAX_FILE/1e6:.0f} MB)")

    def build_hash(self, manifest_wo_build: dict) -> str:
        h = hashlib.sha256()
        for rel, dg in sorted(self.digests):
            h.update(rel.encode("utf-8") + b"\0" + dg.encode("ascii"))
        h.update(_dump(manifest_wo_build).encode("utf-8"))
        return h.hexdigest()[:10]


def _compact(o):
    """Drop None / empty containers recursively (cards are mostly empty fields; saves ~60% bytes)."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            v = _compact(v)
            if v is None or v == [] or v == {} or v == "" or (v == 0 and k in ("const", "local", "opaque")):
                continue
            out[k] = v
        return out
    if isinstance(o, list):
        return [_compact(x) if isinstance(x, (dict, list)) else x for x in o]
    return o


def task_key(ctrl: str, path: str) -> str:
    """'CTRL|Program/Task' for any block path ('Program/Task/UB/Block' -> first two segments)."""
    return f"{ctrl}|{'/'.join(path.split('/', 2)[:2])}"


def _ref(r):
    # [ctrl, program, block_path, block_type, pin, dir_src, line]
    return [r["ctrl"], r["program"], r["path"], r["block_type"], r["pin"], r["dir_source"] or "-", r["line_no"]]


def run(conn, docs: Path, repo: Path, log=print, passphrase: str = None):
    t0 = time.time()
    data = docs / "data"
    if data.exists():
        log(f"  clearing {data}")
        for attempt in range(5):          # a local test server may hold a file open for a moment
            try:
                shutil.rmtree(data)
                break
            except PermissionError as e:
                if attempt == 4:
                    raise SystemExit(f"cannot clear {data}: {e} (stop the local server and retry)")
                time.sleep(2)
    data.mkdir(parents=True)
    enc_key = salt = None
    if passphrase:
        salt = secrets.token_bytes(16)
        enc_key = derive_key(passphrase, salt)
        log(f"  encryption: AES-256-GCM, PBKDF2-SHA256 x{KDF_ITER} (gzip before encrypt)")
    else:
        log("  WARNING: plain (unencrypted) export - for local testing only, never publish")
    w = Writer(data, log, enc_key)

    ctrls = [dict(r) for r in conn.execute("SELECT * FROM controller ORDER BY name")]
    cnames = [c["name"] for c in ctrls]
    enc_programs = [(r[0], r[1]) for r in conn.execute("SELECT ctrl,name FROM program WHERE encrypted=1 ORDER BY 1,2")]
    enc_set = set(enc_programs)

    # ---------------------------------------------------------------- lookups
    prog_by_id = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id,ctrl,name FROM program")}
    log("  loading block index")
    blk = {}   # block_id -> (ctrl, program, path, block_type, kind, opaque, line)
    for r in conn.execute("SELECT id,ctrl,program_id,path,block_type,kind,is_opaque,line_no FROM block"):
        blk[r[0]] = (r[1], prog_by_id.get(r[2], ("", ""))[1], r[3], r[4] or "", r[5], r[6], r[7])
    var_name = {}   # id -> full_name
    for r in conn.execute("SELECT id,full_name FROM variable"):
        var_name[r[0]] = r[1]

    # ---------------------------------------------------------------- per-variable aggregates
    log("  aggregating pins per variable")
    writers, readers, unknown = defaultdict(list), defaultdict(list), defaultdict(list)
    wmore, rmore = defaultdict(int), defaultdict(int)
    for r in conn.execute("SELECT var_id,block_id,name,direction,dir_source,line_no FROM pin WHERE var_id IS NOT NULL"):
        vid, bid, pname, d, ds, ln = r
        b = blk.get(bid)
        if not b:
            continue
        ref = [b[0], b[1], b[2], b[3], pname, ds or "-", ln]
        if d == "O":
            if len(writers[vid]) < MAX_REFS:
                writers[vid].append(ref)
            else:
                wmore[vid] += 1
        elif d == "I" or d == "S":
            if len(readers[vid]) < MAX_REFS:
                readers[vid].append(ref)
            else:
                rmore[vid] += 1
        else:
            if len(unknown[vid]) < MAX_REFS:
                unknown[vid].append(ref)
    io = defaultdict(list)
    screws = defaultdict(list)
    for r in conn.execute("SELECT point_id,name,number,cable_number,wire_number FROM io_screw ORDER BY point_id,number"):
        screws[r[0]].append([r[1], r[2], r[3] or "", r[4] or ""])
    for r in conn.execute("""SELECT p.id,p.var_id,p.ctrl,m.name,m.cabinet,b.name,b.hw_form,b.position_r,p.name,p.device_tag,
                                    p.direction,p.input_type,p.low_value,p.high_value,p.signal_type
                             FROM io_point p LEFT JOIN io_module m ON m.id=p.module_id LEFT JOIN io_board b ON b.id=p.board_id
                             WHERE p.var_id IS NOT NULL"""):
        io[r[1]].append({"ctrl": r[2], "module": r[3], "cabinet": r[4], "board": r[5], "hw": r[6], "pos": r[7],
                         "point": r[8], "tag": r[9], "dir": r[10], "type": r[11], "lo": r[12], "hi": r[13],
                         "kind": r[14], "screws": screws.get(r[0], [])})
    egd_p = defaultdict(list)
    for r in conn.execute("SELECT p.var_id,x.page,x.exchange_id,p.voffs FROM egd_produced p JOIN egd_exchange x ON x.id=p.exchange_pk WHERE p.var_id IS NOT NULL"):
        egd_p[r[0]].append({"page": r[1], "ex": r[2], "voffs": r[3]})
    egd_c = defaultdict(list)       # producer_var_id -> consumers
    egd_src = {}                    # local_var_id -> producer info
    for r in conn.execute("SELECT producer_var_id,local_var_id,consumer_ctrl,producer_ctrl,var_name,exchange_id,voffs,match_method,page FROM egd_consumed"):
        if r[0] is not None:
            egd_c[r[0]].append({"ctrl": r[2], "local": f"{r[3]}.{r[4]}", "ex": r[5], "voffs": r[6], "match": r[7], "page": r[8]})
        if r[1] is not None:
            egd_src[r[1]] = {"ctrl": r[3], "var": r[4], "ex": r[5], "voffs": r[6], "match": r[7]}
    # CIMPLICITY screen names are case-insensitive: canonicalise on lower-case (menu spelling wins)
    canon = {}
    menu = defaultdict(list)
    for r in conn.execute("SELECT screen,menu,submenu,item,block FROM hmi_menu"):
        k = (r[0] or "").lower()
        canon.setdefault(k, r[0])
        menu[k].append(" / ".join(x for x in (r[4], r[1], r[2], r[3]) if x))
    hmi = defaultdict(list)
    for r in conn.execute("SELECT var_id,screen,source FROM hmi_point WHERE var_id IS NOT NULL"):
        k = (r[1] or "").lower()
        hmi[r[0]].append([canon.setdefault(k, r[1]), menu.get(k, [""])[0], r[2]])
    watch = defaultdict(list)
    for r in conn.execute("SELECT var_id,ctrl,watch_file FROM watch WHERE var_id IS NOT NULL"):
        watch[r[0]].append([r[1], Path(r[2]).name])
    drg = defaultdict(set)
    # logic drawing numbers live on the enclosing task (TopUserBlock attributes LogicDrg / P_ID)
    task_drg = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT task_id, logic_drg, p_id FROM block WHERE kind='task' AND (logic_drg IS NOT NULL OR p_id IS NOT NULL)")}
    for r in conn.execute("""SELECT DISTINCT p.var_id, b.task_id, b.logic_drg, b.p_id FROM pin p JOIN block b ON b.id=p.block_id
                             WHERE p.var_id IS NOT NULL"""):
        td = task_drg.get(r[1], (None, None))
        ld, pid = r[2] or td[0], r[3] or td[1]
        if ld or pid:
            drg[r[0]].add((ld or "", pid or ""))
    fs = {r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT name,units,low,high FROM format_spec")}

    # ---------------------------------------------------------------- variables -> names + var shards + alarm lists
    log("  writing var shards")
    shards = defaultdict(list)
    names = defaultdict(list)
    alarms = defaultdict(list)
    n_vars = 0
    cols = ("id ctrl name full_name description datatype address value egd_page alias format_spec units disp_low disp_high "
            "display_screen control_constant device_name producer_var_id referenced_in alarm_id alarm_class alarm_definition "
            "plant_area potential_causes operator_action consequence urgency decl_program decl_task decl_file decl_line "
            "is_program_local").split()
    for r in conn.execute("SELECT " + ",".join(cols) + " FROM variable ORDER BY ctrl,name"):
        v = dict(zip(cols, r))
        vid = v["id"]
        units, lo, hi = v["units"], v["disp_low"], v["disp_high"]
        if v["format_spec"] and v["format_spec"] in fs:
            fu, flo, fhi = fs[v["format_spec"]]
            units = units or fu
            lo = lo if lo is not None else flo
            hi = hi if hi is not None else fhi
        refd = [x for x in (v["referenced_in"] or "").split(",") if x]
        enc = [p for p in refd if (v["ctrl"], p) in enc_set]
        flags = 0
        if writers.get(vid): flags |= 1
        if io.get(vid): flags |= 2
        if egd_p.get(vid) or vid in egd_src or egd_c.get(vid): flags |= 4
        if hmi.get(vid): flags |= 8
        if v["alarm_id"]: flags |= 16
        if v["control_constant"]: flags |= 32
        if v["device_name"]: flags |= 64
        if enc: flags |= 128
        names[v["ctrl"]].append([v["name"], v["description"] or "", flags, v["alias"] or "", v["datatype"] or ""])
        card = {
            "d": {"desc": v["description"], "dt": v["datatype"], "addr": v["address"], "val": v["value"],
                  "egd_page": v["egd_page"], "alias": v["alias"], "fs": v["format_spec"], "units": units,
                  "lo": lo, "hi": hi, "const": v["control_constant"], "local": v["is_program_local"],
                  "decl": [v["decl_program"], v["decl_task"], v["decl_file"], v["decl_line"]],
                  "device_name": v["device_name"], "producer": var_name.get(v["producer_var_id"]),
                  "ref": refd, "screen": v["display_screen"]},
            "w": writers.get(vid, []), "r": readers.get(vid, []), "u": unknown.get(vid, []),
            "io": io.get(vid, []),
            "egd": {"p": egd_p.get(vid, []), "c": egd_c.get(vid, []), "src": egd_src.get(vid)},
            "hmi": hmi.get(vid, []), "watch": watch.get(vid, []),
            "drg": sorted(drg.get(vid, ())), "enc": enc,
        }
        if wmore.get(vid): card["w_more"] = wmore[vid]
        if rmore.get(vid): card["r_more"] = rmore[vid]
        if v["alarm_id"]:
            card["alm"] = {"id": v["alarm_id"], "cls": v["alarm_class"], "def": v["alarm_definition"], "area": v["plant_area"],
                           "causes": v["potential_causes"], "action": v["operator_action"], "conseq": v["consequence"],
                           "urg": v["urgency"]}
            alarms[v["ctrl"]].append([v["name"], v["description"] or "", v["alarm_class"] or "", v["alarm_definition"] or "",
                                      v["plant_area"] or "", v["urgency"] or ""])
        shards[_shard(v["full_name"])].append(_dump(v["full_name"]) + ":" + _dump(_compact(card)))
        n_vars += 1
    for sh, items in shards.items():
        w.write(f"var/{sh}.json", '{"v":{' + ",".join(items) + "}}")
    for c, rows in names.items():
        w.write(f"names/{c}.json", _dump({"ctrl": c, "rows": rows}))
    for c, rows in alarms.items():
        w.write(f"alarm/{c}.json", _dump({"ctrl": c, "rows": rows}))
    del shards, writers, readers, unknown
    log(f"    {n_vars} variables, {time.time()-t0:.0f}s")

    # ---------------------------------------------------------------- blocks + pins -> task shards, program trees
    log("  writing task shards")
    task_items = defaultdict(list)     # tkey -> ['"key":{record}', ...] in document order (root first)
    task_n = defaultdict(int)
    task_vars = defaultdict(set)       # tkey -> var ids referenced anywhere in the task
    has_layout = bool(conn.execute("SELECT 1 FROM pragma_table_info('block') WHERE name='layout'").fetchone())
    pins_by_block = defaultdict(list)
    block_vars = defaultdict(set)      # block_id -> var ids referenced by its pins (for the per-task description map)
    for r in conn.execute("""SELECT block_id,name,direction,dir_source,conn_kind,connection,var_id,tgt_block_id,tgt_pin,address,alias,description
                             FROM pin ORDER BY block_id,id"""):
        tb = blk.get(r[7]) if r[7] else None
        pins_by_block[r[0]].append([r[1], r[2] or "?", r[3] or "-", r[4] or "-", r[5], var_name.get(r[6]),
                                    (f"{tb[0]}|{tb[2]}" if tb else None), r[8], r[9], r[10],
                                    (r[11].split("\n")[0].strip() or None) if r[11] else None])
        if r[6] is not None:
            block_vars[r[0]].add(r[6])
    var_desc = {r[0]: r[1].split("\n")[0].strip() for r in conn.execute(
        "SELECT id, description FROM variable WHERE description IS NOT NULL AND description<>''") if r[1].strip()}
    attrs = defaultdict(dict)
    for r in conn.execute("SELECT block_id,name,value FROM block_attr"):
        attrs[r[0]][r[1]] = r[2]
    prog_tree = defaultdict(lambda: defaultdict(list))   # ctrl -> program -> tasks
    task_blocks = defaultdict(list)                        # task_id -> [key,name,type,kind,opaque]
    n_blocks = 0
    lay_col = ",layout" if has_layout else ",NULL"
    for r in conn.execute(f"""SELECT id,ctrl,program_id,task_id,path,name,block_type,kind,version,is_opaque,description,logic_drg,p_id,device,
                                    hmi_linked_object,line_no{lay_col} FROM block ORDER BY ctrl,program_id,id"""):
        bid, ctrl, pid, tid, path, name, btype, kind, ver, opq, desc, ldrg, p_id, dev, hlo, ln, lay = r
        prog = prog_by_id.get(pid, ("", ""))[1]
        key = f"{ctrl}|{path}"
        b = {"ctrl": ctrl, "program": prog, "path": path, "name": name, "type": btype, "kind": kind, "ver": ver,
             "opaque": opq, "desc": desc, "drg": ldrg, "pid": p_id, "device": dev, "hmi": hlo, "lay": lay,
             "attrs": attrs.get(bid, {}), "pins": pins_by_block.get(bid, []), "line": ln,
             "file": f"{ctrl}/_{prog}.xml"}
        tkey = task_key(ctrl, path)
        task_items[tkey].append(_dump(key) + ":" + _dump(_compact(b)))
        task_n[tkey] += 1
        if bid in block_vars:
            task_vars[tkey].update(block_vars[bid])
        if tid is not None:
            task_blocks[tid].append([key, name, btype, kind, opq])
        n_blocks += 1
    tshards = defaultdict(list)
    sizes = []
    for tkey, items in task_items.items():
        vd = {var_name[v]: var_desc[v] for v in task_vars.get(tkey, ()) if v in var_desc and v in var_name}
        body = '{"n":%d,"vd":%s,"b":{%s}}' % (task_n[tkey], _dump(vd), ",".join(items))
        sizes.append(len(body))
        tshards[_shard(tkey)].append(_dump(tkey) + ":" + body)
    for sh, items in tshards.items():
        w.write(f"task/{sh}.json", '{"t":{' + ",".join(items) + "}}")
    sizes.sort()
    if sizes:
        log(f"    {len(sizes)} task entries  p50 {sizes[len(sizes)//2]/1e3:.0f} KB  p95 {sizes[int(len(sizes)*.95)]/1e3:.0f} KB  "
            f"max {sizes[-1]/1e6:.2f} MB  layout column: {has_layout}")
    del task_items, tshards, pins_by_block, block_vars, task_vars, var_desc
    tasks_by_prog = defaultdict(list)
    for r in conn.execute("SELECT id,program_id,name,block_type,logic_drg,is_task,line_no FROM task ORDER BY program_id,id"):
        tasks_by_prog[r[1]].append({"name": r[2], "type": r[3], "drg": r[4], "is_task": r[5], "line": r[6],
                                    "blocks": task_blocks.get(r[0], [])})
    progs = defaultdict(list)
    for r in conn.execute("SELECT id,ctrl,name,library_type,file_path,encrypted,help_file,block_count,task_count FROM program ORDER BY ctrl,name"):
        progs[r[1]].append({"name": r[2], "lib": r[3], "file": r[4], "enc": r[5], "help": r[6], "n_blocks": r[7],
                            "n_tasks": r[8], "tasks": tasks_by_prog.get(r[0], [])})
    for c in cnames:
        w.write(f"program/{c}.json", _dump({"ctrl": c, "programs": progs.get(c, [])}))
    log(f"    {n_blocks} blocks, {time.time()-t0:.0f}s")

    # ---------------------------------------------------------------- io per controller
    log("  writing io")
    for c in cnames:
        mods = []
        for m in conn.execute("SELECT id,name,module_id,cabinet,io_redundancy,ip_r FROM io_module WHERE ctrl=? ORDER BY cabinet,name", (c,)):
            boards = []
            for b in conn.execute("SELECT id,name,hw_form,position_r FROM io_board WHERE module_id=? ORDER BY id", (m[0],)):
                pts = []
                for p in conn.execute("""SELECT id,name,direction,connection,device_tag,address,input_type,low_value,high_value,var_id
                                         FROM io_point WHERE board_id=? ORDER BY id""", (b[0],)):
                    pts.append([p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], screws.get(p[0], []), var_name.get(p[9])])
                boards.append({"name": b[1], "hw": b[2], "pos": b[3], "points": pts})
            internal = [[p[1], p[2], p[3], var_name.get(p[4])] for p in conn.execute(
                "SELECT id,name,connection,address,var_id FROM io_point WHERE module_id=? AND board_id IS NULL ORDER BY id", (m[0],))]
            mods.append({"name": m[1], "id": m[2], "cabinet": m[3], "red": m[4], "boards": boards, "internal": internal})
        w.write(f"io/{c}.json", _dump({"ctrl": c, "modules": mods}))

    # ---------------------------------------------------------------- screens
    log("  writing screens")
    scr = defaultdict(lambda: {"menu": [], "points": []})
    for k, paths in menu.items():
        scr[canon[k]]["menu"] = paths
    for r in conn.execute("SELECT screen,full_point,source FROM hmi_point ORDER BY screen,full_point"):
        k = (r[0] or "").lower()
        scr[canon.setdefault(k, r[0])]["points"].append([r[1], r[2]])
    sshards = defaultdict(list)
    for s, d in scr.items():
        sshards[_shard(s, SCREEN_SHARDS)].append(_dump(s) + ":" + _dump(d))
    for sh, items in sshards.items():
        w.write(f"screen/{sh}.json", '{"s":{' + ",".join(items) + "}}")
    screen_index = sorted((s, len(d["points"]), d["menu"][0] if d["menu"] else "") for s, d in scr.items())
    w.write("screens.json", _dump({"rows": screen_index}))

    # ---------------------------------------------------------------- manifest
    log("  writing manifest")
    counts = {}
    for c in cnames:
        counts[c] = {
            "n_vars": conn.execute("SELECT count(*) FROM variable WHERE ctrl=?", (c,)).fetchone()[0],
            "n_blocks": conn.execute("SELECT count(*) FROM block WHERE ctrl=? AND kind='block'", (c,)).fetchone()[0],
            "n_pins": conn.execute("SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE b.ctrl=?", (c,)).fetchone()[0],
            "n_programs": conn.execute("SELECT count(*) FROM program WHERE ctrl=?", (c,)).fetchone()[0],
            "n_encrypted": conn.execute("SELECT count(*) FROM program WHERE ctrl=? AND encrypted=1", (c,)).fetchone()[0],
            "n_tasks": conn.execute("SELECT count(*) FROM task t JOIN program p ON p.id=t.program_id WHERE p.ctrl=?", (c,)).fetchone()[0],
            "n_io": conn.execute("SELECT count(*) FROM io_point WHERE ctrl=? AND board_id IS NOT NULL", (c,)).fetchone()[0],
        }
    btypes = [[r[0], r[1]] for r in conn.execute(
        "SELECT block_type,count(*) FROM block WHERE kind='block' GROUP BY block_type ORDER BY 2 DESC")]
    man = {
        "site": "Signal Atlas",
        "source": {"toolbox_version": get_meta(conn, "toolbox_version"), "indexed_at": get_meta(conn, "built_at"),
                   "controllers_minor_rev": {c["name"]: c["minor_rev"] for c in ctrls}},
        "controllers": [{"name": c["name"], "kind": c["kind"], "redundancy": c["redundancy"],
                         "product_version": c["product_version"], **counts[c["name"]]} for c in ctrls],
        "shards": {"var": 16 ** VAR_SHARDS, "task": 16 ** VAR_SHARDS, "screen": 16 ** SCREEN_SHARDS},
        "dir_legend": {"U": "介面腳 Usage", "T": "手冊表/人工覆寫", "C": "常數規則", "L": "連線投票", "H": "命名慣例", "?": "未知"},
        "flags": {"1": "has_writer", "2": "has_io", "4": "has_egd", "8": "has_hmi", "16": "has_alarm", "32": "const",
                  "64": "egd_copy", "128": "in_encrypted"},
        "encrypted_programs": enc_programs,
        "block_types": btypes,
        "related": [],
        "files": {"names": [f"names/{c}.json" for c in cnames if c in names]},
    }
    # deterministic build hash over every data file (plaintext digests) + manifest (without build)
    man["build"] = w.build_hash(man)
    w.write("manifest.json", _dump(man))
    meta = {"enc": 1 if enc_key else 0, "gzip": 1 if enc_key else 0, "build": man["build"]}
    if enc_key:
        meta["kdf"] = {"name": "PBKDF2", "hash": "SHA-256", "iter": KDF_ITER, "salt": base64.b64encode(salt).decode()}
        meta["check"] = base64.b64encode(seal(enc_key, CHECK_TEXT, "check")).decode()
    with open(data / "meta.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(_dump(meta))
    with open(docs / "version.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(_dump({"build": man["build"]}))
    big = sorted(w.files, key=lambda x: -x[1])[:5]
    log(f"  files {len(w.files)}  plaintext {w.total/1e6:.1f} MB  on disk {w.wire/1e6:.1f} MB  largest: " + ", ".join(f"{r} {n/1e6:.2f}MB" for r, n in big))
    log(f"  build {man['build']}  {time.time()-t0:.0f}s")
    if w.wire > 700_000_000:
        raise SystemExit("ABORT: data exceeds 700 MB on disk — GitHub Pages limit is 1 GB")
    # stamp index.html asset refs
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("stamp_assets", repo / "stamp_assets.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        log("  stamp " + _dump(mod.stamp(str(docs))))
    except Exception as e:   # index.html may not exist yet
        log(f"  stamp skipped: {e}")
    return man
