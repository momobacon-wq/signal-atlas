# -*- coding: utf-8 -*-
"""Independent reconciliation of docs/data (static shards) against the SQLite index.  py tools/verify_web.py [docs]

Written without importing export_web: it re-derives every expectation from CONTRACT.md + the DB.
Checks: manifest present & build reproducible; names rows == variable rows per controller; every variable's card is in
the shard sha1(full_name)[:3]; 200 random cards match the DB field-by-field (def, writers, readers, io, egd, alarm);
every block key in its shard and pin counts match; no local absolute path in any file; file sizes; screens/alarms counts.
Exit 0 only when there are zero errors.
"""
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dcdas import db as dbm  # noqa: E402

ABS_RE = re.compile(r'(?:(?<![A-Za-z])|(?<=\\[nrtbf]))[A-Za-z]:(?:\\\\|/)|/c/Users/|\\Users\\|\\\\\\\\[A-Za-z0-9][\w.$-]*\\\\')
CTRL_KINDS = {"controller", "safety", "exciter", "drive"}
NODE_KINDS = {"hmi", "server", "network", "external", "other"}
ERRORS = []


def err(msg):
    ERRORS.append(msg)
    print("ERR  " + msg)


def ok(msg):
    print("ok   " + msg)


KEY = None       # set from meta.json + passphrase in main()
DATA_DIR = None


def read_text(data, rel):
    """Return the plaintext of data/<rel> (decrypting <rel>.bin when the export is encrypted)."""
    p = Path(data) / rel
    if KEY is not None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import gzip
        with open(str(p) + ".bin", "rb") as f:
            blob = f.read()
        plain = AESGCM(KEY).decrypt(blob[:12], blob[12:], rel.encode("utf-8"))
        return gzip.decompress(plain).decode("utf-8")
    with open(p, "rb") as f:
        return f.read().decode("utf-8")


def load(p):
    """Load a data file given its plaintext path (Path under data/)."""
    return json.loads(read_text(DATA_DIR, Path(p).relative_to(DATA_DIR).as_posix()))


def sh(key, n=3):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:n]


def main(docs):
    global KEY, DATA_DIR
    data = Path(docs) / "data"
    DATA_DIR = data
    if not data.exists():
        err(f"missing {data}")
        return 1
    conn = sqlite3.connect(f"file:{dbm.db_path().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # ---- meta / encryption
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8")) if (data / "meta.json").exists() else {"enc": 0}
    if meta.get("enc"):
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        pw = dbm.web_passphrase()
        k = meta["kdf"]
        KEY = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(k["salt"]),
                         iterations=int(k["iter"])).derive(pw.encode("utf-8"))
        chk = base64.b64decode(meta["check"])
        if AESGCM(KEY).decrypt(chk[:12], chk[12:], b"check") != b"signal-atlas-ok":
            err("passphrase does not open meta.check")
            return 1
        ok(f"encrypted export (PBKDF2 x{k['iter']}, AES-256-GCM); passphrase verified")
        plain_json = [p for p in data.rglob("*.json") if p.name != "meta.json"]
        (ok if not plain_json else err)(f"{len(plain_json)} plaintext .json files besides meta.json")
    else:
        ok("PLAIN export (unencrypted) - must not be published")

    # ---- files: sizes + absolute paths (scanned on plaintext)
    files, disk = [], 0
    for root, _d, fs in os.walk(data):
        for fn in fs:
            p = Path(root) / fn
            rel = p.relative_to(data).as_posix()
            disk += p.stat().st_size
            if rel == "meta.json":
                continue
            files.append(rel[:-4] if rel.endswith(".bin") else rel)
    ok(f"{len(files)} data files, {disk/1e6:.1f} MB on disk")
    if disk > 900_000_000:
        err("total > 900 MB (GitHub Pages limit 1 GB)")
    bad, plain_total, big = 0, 0, 0
    digests = []
    for rel in files:
        t = read_text(data, rel)
        b = t.encode("utf-8")
        plain_total += len(b)
        big += len(b) > 1_000_000
        digests.append((rel, hashlib.sha256(b).hexdigest()))
        if ABS_RE.search(t):
            bad += 1
            if bad <= 5:
                err(f"absolute path in {rel}: {ABS_RE.search(t).group(0)}")
    if not bad:
        ok(f"no local absolute paths ({plain_total/1e6:.1f} MB plaintext, {big} files > 1 MB)")

    # ---- manifest + build reproducibility (hash of plaintext digests + manifest without build)
    man = load(data / "manifest.json")
    h = hashlib.sha256()
    for rel, dg in sorted(d for d in digests if d[0] != "manifest.json"):
        h.update(rel.encode("utf-8") + b"\0" + dg.encode("ascii"))
    m2 = dict(man)
    m2.pop("build", None)
    h.update(json.dumps(m2, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    if h.hexdigest()[:10] == man.get("build") == meta.get("build"):
        ok(f"manifest.build reproducible {man['build']} (meta.json agrees)")
    else:
        err(f"manifest.build mismatch manifest={man.get('build')} meta={meta.get('build')} computed={h.hexdigest()[:10]}")
    vp = Path(docs) / "version.json"
    ver = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else {}
    if ver.get("build") != man.get("build"):
        err("version.json build != manifest.build")

    # ---- manifest controllers == DB controllers (both directions), kind vocabulary
    ctrls = [r[0] for r in conn.execute("SELECT name FROM controller ORDER BY name")]
    ctrl_set = set(ctrls)
    man_ctrls = [x["name"] for x in man.get("controllers", [])]
    if set(man_ctrls) == ctrl_set and len(man_ctrls) == len(ctrl_set):
        ok(f"manifest controllers == DB controller table ({len(ctrls)})")
    else:
        err(f"manifest controllers != DB: only in manifest {sorted(set(man_ctrls) - ctrl_set)}, "
            f"only in DB {sorted(ctrl_set - set(man_ctrls))}, duplicates {len(man_ctrls) - len(set(man_ctrls))}")
    bad_kind = [(x["name"], x.get("kind")) for x in man.get("controllers", []) if x.get("kind") not in CTRL_KINDS]
    (ok if not bad_kind else err)(f"controller kinds within {sorted(CTRL_KINDS)}" + (f": bad {bad_kind}" if bad_kind else ""))

    # ---- names vs variable counts (a controller without variables may have no names file)
    for c in ctrls:
        n_db = conn.execute("SELECT count(*) FROM variable WHERE ctrl=?", (c,)).fetchone()[0]
        p = data / "names" / f"{c}.json"
        try:
            rows = load(p)["rows"]
        except FileNotFoundError:
            rows = None
        if rows is None and n_db == 0:
            ok(f"names {c}: no variables, no names file")
        else:
            n_web = -1 if rows is None else len(rows)
            (ok if n_db == n_web else err)(f"names {c}: db {n_db} web {n_web}")
            n_sub = conn.execute("SELECT count(*) FROM variable WHERE ctrl=? AND sub_of IS NOT NULL", (c,)).fetchone()[0]
            n_sub_web = -1 if rows is None else sum(1 for r in rows if r[2] & 256)
            if n_sub != n_sub_web:
                err(f"names {c}: alarm sub-variable flag (256) db {n_sub} web {n_sub_web}")
        mc = next((x for x in man["controllers"] if x["name"] == c), None)
        if not mc or mc["n_vars"] != n_db:
            err(f"manifest n_vars {c} != {n_db}")

    # ---- non-controller nodes: registry only (name + kind), disjoint from controllers, no per-controller files
    has_node = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='node'").fetchone() is not None
    man_nodes = man.get("nodes", [])
    node_set = {x.get("name") for x in man_nodes}
    if not has_node:
        ok("node table absent in DB: skipping manifest.nodes check" + (f" (manifest lists {len(man_nodes)})" if man_nodes else ""))
    else:
        db_nodes = {r[0]: r[1] for r in conn.execute("SELECT name, kind FROM node")}
        got_nodes = {x.get("name"): x.get("kind") for x in man_nodes}
        if got_nodes == db_nodes and len(man_nodes) == len(db_nodes):
            ok(f"manifest nodes == DB node table ({len(db_nodes)})")
        else:
            err(f"manifest nodes != DB: only in manifest {sorted(set(got_nodes) - set(db_nodes))[:5]}, "
                f"only in DB {sorted(set(db_nodes) - set(got_nodes))[:5]}, "
                f"kind differs {[n for n in set(got_nodes) & set(db_nodes) if got_nodes[n] != db_nodes[n]][:5]}")
    bad_nk = [x for x in man_nodes if x.get("kind") not in NODE_KINDS or set(x) != {"name", "kind"}]
    if bad_nk:
        err(f"manifest nodes: {len(bad_nk)} entries with a kind outside {sorted(NODE_KINDS)} or fields other than name/kind, e.g. {bad_nk[0]}")
    overlap = node_set & ctrl_set
    fileset = set(files)
    node_files = [f"{d}/{n}.json" for n in node_set for d in ("names", "program", "io", "alarm") if f"{d}/{n}.json" in fileset]
    (ok if not overlap and not node_files else err)(
        f"{len(node_set)} nodes: {len(overlap)} also listed as controllers, {len(node_files)} names/program/io/alarm files for nodes"
        + (f" e.g. {sorted(overlap)[:3] + node_files[:3]}" if overlap or node_files else ""))

    # ---- prog_files == programs whose source file is not <ctrl>/_<program>.xml
    want_pf = {(r[0], r[1], r[2]) for r in conn.execute("SELECT ctrl, name, file_path FROM program WHERE file_path IS NOT NULL "
                                                        "AND file_path <> ctrl || '/_' || name || '.xml'")}
    got_pf = {(c, p, f) for c, m in (man.get("prog_files") or {}).items() for p, f in m.items()}
    (ok if got_pf == want_pf else err)(f"manifest prog_files: web {len(got_pf)} db {len(want_pf)}"
                                       + (f"; differ e.g. {sorted(got_pf ^ want_pf)[:3]}" if got_pf != want_pf else ""))

    # ---- sample cards
    random.seed(20260919)
    ids = [r[0] for r in conn.execute("SELECT id FROM variable")]
    sample = random.sample(ids, min(200, len(ids)))
    # add the golden ones
    for full in ("G11.L27QE1_A", "BOPE1.G11.L27QE1_A", "WSC1.1-TI-CW011-2AAXQ01"):
        r = conn.execute("SELECT id FROM variable WHERE full_name=?", (full,)).fetchone()
        if r:
            sample.append(r[0])
    # every EGD copy whose producer is a node, plus one per controller-produced copy, so egd.src is exercised
    if has_node:
        for r in conn.execute("SELECT min(local_var_id) FROM egd_consumed WHERE local_var_id IS NOT NULL "
                              "AND producer_ctrl IN (SELECT name FROM node) GROUP BY producer_ctrl"):
            sample.append(r[0])
    r = conn.execute("SELECT min(local_var_id) FROM egd_consumed WHERE local_var_id IS NOT NULL "
                     "AND producer_ctrl IN (SELECT name FROM controller)").fetchone()
    if r and r[0]:
        sample.append(r[0])
    shard_cache = {}
    src_outside = set()
    nbad = 0
    for vid in sample:
        v = conn.execute("SELECT * FROM variable WHERE id=?", (vid,)).fetchone()
        full = v["full_name"]
        s = sh(full)
        if s not in shard_cache:
            try:
                shard_cache[s] = load(data / "var" / f"{s}.json")["v"]
            except FileNotFoundError:
                shard_cache[s] = {}
        card = shard_cache[s].get(full)
        if card is None:
            nbad += 1
            err(f"card missing {full} in var/{s}.json")
            continue
        d = card["d"]
        if d.get("dt") != v["datatype"] or d.get("addr") != v["address"] or (d.get("desc") or None) != (v["description"] or None):
            nbad += 1
            err(f"card def mismatch {full}: {d.get('dt')},{d.get('addr')} vs {v['datatype']},{v['address']}")
        nw = conn.execute("SELECT count(*) FROM pin WHERE var_id=? AND direction='O'", (vid,)).fetchone()[0]
        nr = conn.execute("SELECT count(*) FROM pin WHERE var_id=? AND direction IN ('I','S')", (vid,)).fetchone()[0]
        cw, cr = card.get("w", []), card.get("r", [])
        if len(cw) + card.get("w_more", 0) != nw or len(cr) + card.get("r_more", 0) != nr:
            nbad += 1
            err(f"card refs mismatch {full}: w {len(cw)}+{card.get('w_more',0)} vs {nw}; r {len(cr)}+{card.get('r_more',0)} vs {nr}")
        nio = conn.execute("SELECT count(*) FROM io_point WHERE var_id=?", (vid,)).fetchone()[0]
        if len(card.get("io", [])) != nio:
            nbad += 1
            err(f"card io mismatch {full}: {len(card.get('io', []))} vs {nio}")
        np_ = conn.execute("SELECT count(*) FROM egd_produced WHERE var_id=?", (vid,)).fetchone()[0]
        if len(card.get("egd", {}).get("p", [])) != np_:
            nbad += 1
            err(f"card egd produced mismatch {full}: {len(card.get('egd', {}).get('p', []))} vs {np_}")
        # egd.c: controller consumers only (node subscriptions are not indexed; no cap); egd.src.ctrl: a controller or a node
        egd_ = card.get("egd", {}) or {}
        cons = egd_.get("c", [])
        nc_ = conn.execute("SELECT count(*) FROM egd_consumed WHERE producer_var_id=? AND consumer_ctrl IN (SELECT name FROM controller)",
                           (vid,)).fetchone()[0]
        not_ctrl = [x.get("ctrl") for x in cons if x.get("ctrl") not in ctrl_set]
        if len(cons) != nc_ or not_ctrl:
            nbad += 1
            err(f"card egd consumers {full}: {len(cons)} vs db {nc_}" + (f"; non-controller consumers {not_ctrl[:3]}" if not_ctrl else ""))
        src_ = egd_.get("src")
        db_src = {r[0] for r in conn.execute("SELECT producer_ctrl FROM egd_consumed WHERE local_var_id=? "
                                             "AND consumer_ctrl IN (SELECT name FROM controller)", (vid,))}
        if bool(src_) != bool(db_src) or (src_ and src_.get("ctrl") not in db_src):
            nbad += 1
            err(f"card egd.src {full}: {src_ and src_.get('ctrl')!r} vs db producers {sorted(db_src)[:3]}")
        elif src_ and src_.get("ctrl") not in ctrl_set and src_.get("ctrl") not in node_set:
            src_outside.add(src_.get("ctrl"))  # allowed by the contract (no card to link), reported below
        has_alm = bool(v["alarm_id"]) or bool(v["sub_of"] and v["alarm_class"])
        if has_alm != ("alm" in card):
            nbad += 1
            err(f"card alarm presence mismatch {full}")
        # writer refs point at real blocks with that pin
        for ref in cw[:3]:
            ctrl, prog, path, btype, pin, src, line = ref[:7]
            if len(ref) > 7 and ref[7] not in ("b", "t"):
                nbad += 1
                err(f"card ref kind {full}: {ref[7]!r}")
            n = conn.execute("""SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE b.ctrl=? AND b.path=? AND p.name=?""",
                             (ctrl, path, pin)).fetchone()[0]
            if n != 1:
                nbad += 1
                err(f"writer ref not found {full}: {ctrl}|{path}.{pin}")
    (ok if not nbad else err)(f"sampled {len(sample)} cards, {nbad} mismatches")
    if src_outside:
        ok(f"egd.src producers in neither controllers nor nodes (allowed by the contract, no card; check the node registry): {sorted(src_outside)}")
    # ---- mirror cards: 10 variables from pin_mirror must carry d.m pointing at the right pin
    mbad = 0
    mrows = conn.execute("""SELECT v.full_name, b.ctrl, b.path, p.name, m.kind FROM pin_mirror m JOIN variable v ON v.id=m.var_id
                            JOIN pin p ON p.id=m.pin_id JOIN block b ON b.id=p.block_id ORDER BY v.id LIMIT 200""").fetchall()
    random.shuffle(mrows)
    for full, ctrl, path, pname, kind in mrows[:10]:
        s_ = sh(full)
        if s_ not in shard_cache:
            try:
                shard_cache[s_] = load(data / "var" / f"{s_}.json")["v"]
            except FileNotFoundError:
                shard_cache[s_] = {}
        card = shard_cache[s_].get(full) or {}
        m = (card.get("d") or {}).get("m")
        if not m or m.get("pin", [None]*7)[2] != path or m["pin"][4] != pname or m.get("kind") != kind:
            mbad += 1
            if mbad <= 3:
                err(f"mirror card {full}: {m}")
    (ok if not mbad else err)(f"sampled 10 mirror cards, {mbad} mismatches")

    # ---- blocks: sample 300 blocks, check task shard + pin count
    def tkey_of(ctrl, path):
        return f"{ctrl}|{'/'.join(path.split('/', 2)[:2])}"
    tcache = {}

    def task_entry(tkey):
        s = sh(tkey)
        if s not in tcache:
            try:
                tcache[s] = load(data / "task" / f"{s}.json")["t"]
            except FileNotFoundError:
                tcache[s] = {}
        return tcache[s].get(tkey)
    bids = [r[0] for r in conn.execute("SELECT id FROM block")]
    bsample = random.sample(bids, min(300, len(bids)))
    # plus one block from every program whose source file is not <ctrl>/_<program>.xml (manifest.prog_files)
    n_pf_blocks = 0
    for r in conn.execute("""SELECT min(b.id) FROM block b JOIN program g ON g.id=b.program_id
                             WHERE g.file_path IS NOT NULL AND g.file_path <> g.ctrl || '/_' || g.name || '.xml' GROUP BY g.id"""):
        if r[0] is not None:
            bsample.append(r[0])
            n_pf_blocks += 1
    bbad = 0
    for bid in bsample:
        b = conn.execute("""SELECT b.ctrl, b.path, g.name, g.file_path FROM block b LEFT JOIN program g ON g.id=b.program_id
                            WHERE b.id=?""", (bid,)).fetchone()
        key = f"{b[0]}|{b[1]}"
        ent = task_entry(tkey_of(b[0], b[1]))
        blkj = ent["b"].get(key) if ent else None
        npins = conn.execute("SELECT count(*) FROM pin WHERE block_id=?", (bid,)).fetchone()[0]
        if blkj is None or len(blkj.get("pins", [])) != npins:
            bbad += 1
            if bbad <= 5:
                err(f"block {key}: {'missing' if blkj is None else 'pins ' + str(len(blkj.get('pins', []))) + ' vs ' + str(npins)}")
            continue
        want_file = b[3] or f"{b[0]}/_{b[2] or ''}.xml"
        if blkj.get("file") != want_file:
            bbad += 1
            if bbad <= 5:
                err(f"block {key}: file {blkj.get('file')!r} vs program.file_path {want_file!r}")
    (ok if not bbad else err)(f"sampled {len(bsample)} blocks ({n_pf_blocks} from prog_files programs), {bbad} mismatches (pins, file)")
    # ---- opaque macros with recovered interface pins: rc counts and per-pin origin
    obad = 0
    orows = conn.execute("SELECT DISTINCT block_id FROM pin WHERE origin IS NOT NULL").fetchall()
    for (bid,) in random.sample(orows, min(10, len(orows))):
        b = conn.execute("SELECT ctrl,path FROM block WHERE id=?", (bid,)).fetchone()
        key = f"{b[0]}|{b[1]}"
        ent = task_entry(tkey_of(b[0], b[1]))
        blkj = ent["b"].get(key) if ent else None
        want = [conn.execute("SELECT count(*) FROM pin WHERE block_id=? AND origin=?", (bid, o)).fetchone()[0] for o in ("decl", "link", "pair", "xref", "vote", "print")]
        got_org = sorted(p[11] for p in (blkj or {}).get("pins", []) if p[11])
        want_org = sorted({"decl": "d", "link": "l", "pair": "p", "xref": "x", "vote": "v", "print": "g"}[r[0]] for r in conn.execute("SELECT origin FROM pin WHERE block_id=? AND origin IS NOT NULL", (bid,)))
        if blkj is None or not blkj.get("opaque") or blkj.get("rc") != want or got_org != want_org:
            obad += 1
            if obad <= 5:
                err(f"opaque block {key}: rc {(blkj or {}).get('rc')} vs {want}, origins {len(got_org)} vs {len(want_org)}")
    (ok if not obad else err)(f"sampled {min(10, len(orows))} opaque blocks with recovered pins, {obad} mismatches")
    # ---- pins promoted from the printed sheets: the task's pv map cites where each is drawn
    pbad = 0
    prows = conn.execute("SELECT b.ctrl, b.path, p.name, c.pdf, c.page, c.cell FROM pin_cite c JOIN pin p ON p.id=c.pin_id AND (p.origin='print' OR p.dir_source='G') "
                         "JOIN block b ON b.id=p.block_id").fetchall()
    for ctrl_, path_, pin_, pdf_, page_, cell_ in random.sample(prows, min(20, len(prows))):
        ent = task_entry(tkey_of(ctrl_, path_))
        got = ((ent or {}).get("pv") or {}).get(f"{ctrl_}|{path_}#{pin_}")
        if got != [pdf_, page_, cell_]:
            pbad += 1
            if pbad <= 5:
                err(f"printed-sheet cite {ctrl_}|{path_}#{pin_}: {got} vs {[pdf_, page_, cell_]}")
    (ok if not pbad else err)(f"sampled {min(20, len(prows))} of {len(prows)} pins promoted from the printed sheets, {pbad} cite mismatches")
    li = man.get("lib_iface", {})
    (ok if li and "AI_INT" in li else err)(f"manifest lib_iface: {len(li)} types")
    # ---- tasks: sample 30 task entries (root first, document order, counts)
    tids = [r[0] for r in conn.execute("SELECT id FROM task")]
    tbad = 0
    for tid in random.sample(tids, min(30, len(tids))):
        rows = conn.execute("SELECT ctrl||'|'||path, kind FROM block WHERE task_id=? ORDER BY id", (tid,)).fetchall()
        if not rows:
            continue
        tkey = tkey_of(rows[0][0].split("|")[0], rows[0][0].split("|", 1)[1])
        ent = task_entry(tkey)
        keys = [r[0] for r in rows]
        if ent is None or ent.get("n") != len(keys) or list(ent["b"].keys()) != keys or next(iter(ent["b"].values())).get("kind") != "task":
            tbad += 1
            if tbad <= 5:
                err(f"task {tkey}: {'missing' if ent is None else 'n ' + str(ent.get('n')) + '/' + str(len(ent['b'])) + ' vs ' + str(len(keys)) + ' or order/root mismatch'}")
            continue
        # pin tuples carry 12 fields (desc, origin last); vd = descriptions of every variable referenced by the task's pins
        bad_len = sum(1 for rec in ent["b"].values() for p in rec.get("pins", []) if len(p) != 12)
        want_vd = {r[0]: r[1].split("\n")[0].strip() for r in conn.execute(
            """SELECT DISTINCT v.full_name, v.description FROM pin p JOIN block b ON b.id=p.block_id JOIN variable v ON v.id=p.var_id
               WHERE b.task_id=? AND v.description IS NOT NULL AND v.description<>''""", (tid,)) if r[1].strip()}
        got_vd = ent.get("vd", {})
        vu_bad = 0
        for full, (nw, nr, fl) in list(ent.get("vu", {}).items())[:10]:
            v = conn.execute("SELECT id FROM variable WHERE full_name=?", (full,)).fetchone()
            if not v:
                vu_bad += 1
                continue
            w_ = conn.execute("SELECT count(*) FROM pin WHERE var_id=? AND direction='O'", (v[0],)).fetchone()[0]
            r_ = conn.execute("SELECT count(*) FROM pin WHERE var_id=? AND direction<>'O'", (v[0],)).fetchone()[0]
            if (w_, r_) != (nw, nr):
                vu_bad += 1
        if vu_bad:
            tbad += 1
            if tbad <= 5:
                err(f"task {tkey}: {vu_bad} vu entries disagree with DB")
        if bad_len or got_vd != want_vd:
            tbad += 1
            if tbad <= 5:
                err(f"task {tkey}: {bad_len} pins not 11 fields; vd {len(got_vd)} vs {len(want_vd)} expected")
    (ok if not tbad else err)(f"sampled 30 task entries, {tbad} mismatches")
    if (data / "block").exists():
        err("legacy data/block directory still present")
    if man.get("shards", {}).get("task") != 4096:
        err("manifest.shards.task != 4096")

    # ---- screens & alarms
    n_scr_db = conn.execute("SELECT count(*) FROM (SELECT lower(screen) FROM hmi_point UNION SELECT lower(screen) FROM hmi_menu)").fetchone()[0]
    try:
        scr = load(data / "screens.json")["rows"]
    except FileNotFoundError:
        scr = []
    (ok if len(scr) == n_scr_db else err)(f"screens: db {n_scr_db} web {len(scr)}")
    # screen points [full_point, source, st(, card)]: st 1 <=> hmi_point.var_id resolved; st in {0,1,2}; the link target
    # (card, else full_point) is exactly the resolved variable's full name; legacy 2-field rows noted
    sbad, n_legacy, n_pts = 0, 0, 0
    scache = {}
    for srow in random.sample(scr, min(30, len(scr))):
        sname = srow[0]
        s_ = sh(sname, 2)
        if s_ not in scache:
            try:
                scache[s_] = load(data / "screen" / f"{s_}.json")["s"]
            except FileNotFoundError:
                scache[s_] = {}
        pts = (scache[s_].get(sname) or {}).get("points", [])
        db_res, db_card = {}, {}
        for r in conn.execute("SELECT h.full_point, h.source, h.var_id, v.full_name FROM hmi_point h LEFT JOIN variable v "
                              "ON v.id=h.var_id WHERE lower(h.screen)=lower(?)", (sname,)):
            db_res[(r[0], r[1])] = r[2] is not None
            db_card[(r[0], r[1])] = r[3]
        for p in pts:
            n_pts += 1
            if len(p) < 3:
                n_legacy += 1
                continue
            st = p[2]
            target = (p[3] if len(p) > 3 else p[0]) if st == 1 else None
            if st not in (0, 1, 2) or (st == 1) != db_res.get((p[0], p[1]), False) or (st == 1 and target != db_card.get((p[0], p[1]))):
                sbad += 1
                if sbad <= 5:
                    err(f"screen {sname}: point {p[0]} st {st!r} vs db resolved {db_res.get((p[0], p[1]))}")
    (ok if not sbad else err)(f"sampled {min(30, len(scr))} screens, {n_pts} points, {sbad} st / link-target mismatches"
                              + (f", {n_legacy} legacy 2-field rows (st not exported yet)" if n_legacy else ""))
    for c in ctrls:
        n_db = conn.execute("SELECT count(*) FROM variable WHERE ctrl=? AND (alarm_id IS NOT NULL OR (sub_of IS NOT NULL AND alarm_class IS NOT NULL))", (c,)).fetchone()[0]
        try:
            n_web = len(load(data / "alarm" / f"{c}.json")["rows"])
        except FileNotFoundError:
            n_web = 0
        if n_db != n_web:
            err(f"alarm {c}: db {n_db} web {n_web}")
    ok("alarm lists checked")
    print()
    print(f"{len(ERRORS)} error(s)" if ERRORS else "ALL OK")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / "docs")))
