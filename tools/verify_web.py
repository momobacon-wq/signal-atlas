# -*- coding: utf-8 -*-
"""Independent reconciliation of docs/data (static shards) against the SQLite index.  py tools/verify_web.py [docs]

The default folder is the config's web_dir (docs/ for the default site, docs/<site>/ for a site set by DCDAS_CONFIG).

Written without importing export_web: it re-derives every expectation from CONTRACT.md + the DB.
Checks: manifest present & build reproducible; names rows == variable rows per controller; every variable's card is in
the shard sha1(full_name)[:3]; 200 random cards match the DB field-by-field (def, writers, readers, io, egd, alarm);
every block key in its shard and pin counts match; no local absolute path in any file; file sizes; screens/alarms counts;
sfc.json (sequence keys, steps / transitions, Enable indexes, re-decoded Configuration, start-chain hops against the
index and the task shards, d / inf / op / w flags); the page's atlas-site equals the configured site; a sub-site page
equals the one derived from the root page (modulo stamps); the repo's whole docs/ stays under the GitHub Pages 1 GB limit.
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
import stamp_assets  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PAGES_LIMIT = 1_000_000_000

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


def dir_size(d):
    n = 0
    for root, _d, fs in os.walk(d):
        for fn in fs:
            try:
                n += (Path(root) / fn).stat().st_size
            except OSError:
                pass
    return n


def check_pages(docs):
    """atlas-site of <docs>/index.html == the configured site; every sub-site page == the page derived from the root
    page (stamp_assets.subsite_html) modulo stamps; the repo's docs/ (what Pages serves) under 1 GB."""
    docs = Path(docs)
    site = dbm.site()
    ix = docs / "index.html"
    if ix.exists():
        page = stamp_assets.page_site(ix.read_text(encoding="utf-8"))
        (ok if page == site else err)(f"page atlas-site {page or '(default)'!r}, configured site {site or '(default)'!r}")
    else:
        ok(f"no index.html in {docs.name}: page checks skipped (scratch export)")
    # sub-site pages to compare with the root page: this folder when it is a sub-site, every sub-site when it is the root
    if site and ix.exists():
        pairs = [(site, docs, docs.parent / "index.html")]
    elif not site and ix.exists():
        pairs = [(s, Path(d), ix) for s, d in stamp_assets.subsites(str(docs))]
    else:
        pairs = []
    for s, d, root_ix in pairs:
        if not root_ix.exists():
            err(f"sub-site {s}: root page {root_ix.name} missing in {root_ix.parent.name}")
            continue
        try:
            want = stamp_assets.subsite_html(root_ix.read_text(encoding="utf-8"), s)
        except ValueError as e:
            err(f"sub-site {s}: cannot derive the page from the root page: {e}")
            continue
        got = (d / "index.html").read_text(encoding="utf-8")
        same = stamp_assets.unstamp(want) == stamp_assets.unstamp(got)
        (ok if same else err)(f"sub-site {s}: index.html " + ("matches the root page (modulo stamps)" if same else
                              "differs from the page derived from the root page: regenerate it"))
    # lazily loaded scripts (D.loadScript): each exists in the shared assets, and no script builds a page-relative
    # 'assets/' URL (a sub-site page lives one folder down: it would 404 there while the root page works)
    adir = Path(stamp_assets.assets_dir_for(str(docs.resolve())))
    if adir.is_dir():
        lazy, rel = set(), []
        for js in sorted(adir.glob("*.js")):
            if js.name.endswith(".min.js"):
                continue
            t = js.read_text(encoding="utf-8")
            lazy |= set(re.findall(r"loadScript\(\s*'([\w.-]+)'\s*\)", t))
            rel += [js.name for _ in re.finditer(r"""['"]assets/['"]\s*\+""", t)]
        missing = sorted(n for n in lazy if not (adir / n).exists())
        (ok if lazy and not missing else err)(f"lazy-loaded scripts {sorted(lazy)}: " + (f"missing {missing}" if missing else
                                              f"all in {adir.parent.name}/{adir.name}/"))
        (ok if not rel else err)("no page-relative 'assets/' script URL" + (f": {sorted(set(rel))}" if rel else ""))
    pages = REPO / "docs"
    if pages.exists():
        n = dir_size(pages)
        (ok if n < PAGES_LIMIT else err)(f"docs/ total {n/1e6:.0f} MB (GitHub Pages limit 1 GB)")


SFC_PASS = {"MOVE", "AND", "OR", "PULSE"}
SFC_INFER = set("LHRM")


def sfc_config(val):
    """Decode a transition wrapper's Configuration value (CONTRACT: '<nrec>,(type,index,value)*'; type 1 = Steps array,
    value 0 = from-step, 1 = to-step) -> (from, to) step indexes, or None when it is not that format."""
    if val is None or str(val).strip() == "":
        return None
    try:
        nums = [int(x) for x in str(val).split(",")]
    except ValueError:
        return None
    if len(nums) != 1 + 3 * nums[0]:
        return None
    recs = [nums[i:i + 3] for i in range(1, len(nums), 3)]
    return [r[1] for r in recs if r[0] == 1 and r[2] == 0], [r[1] for r in recs if r[0] == 1 and r[2] == 1]


def check_sfc(conn, man, fileset, task_entry, tkey_of, card):
    """data/sfc.json (SFC topology + start chain) against the index, re-derived from CONTRACT.md without tools/dcdas/sfc.py.
    `card(full)` returns the variable's card (or None); `task_entry(tkey)` the task shard entry."""
    has_file, key = "sfc.json" in fileset, (man.get("files") or {}).get("sfc")
    if not has_file and key is None:
        ok("no sfc.json and no manifest files.sfc: sequence checks skipped (older export)")
        return
    if key != "sfc.json" or not has_file:
        err(f"manifest files.sfc {key!r}, sfc.json {'present' if has_file else 'missing'}")
        if not has_file:
            return
    doc = load(DATA_DIR / "sfc.json")
    sfc, edges, cut = doc.get("sfc") or {}, doc.get("edges") or [], doc.get("cut") or []
    bad = [0]

    def fail(msg):
        bad[0] += 1
        if bad[0] <= 8:
            err("sfc: " + msg)

    # ---- key set: tasks whose direct child is a userblock named Evaluate_Transitions
    want = {f"{r[0]}|{r[1]}" for r in conn.execute(
        "SELECT t.ctrl, t.path FROM block e JOIN block t ON t.id=e.parent_id "
        "WHERE e.kind='userblock' AND e.name='Evaluate_Transitions' AND t.kind='task'")}
    if set(sfc) != want:
        fail(f"keys != SFC tasks in DB: only in sfc.json {sorted(set(sfc) - want)[:3]}, only in DB {sorted(want - set(sfc))[:3]}")

    def block_row(ctrl, path):
        return conn.execute("SELECT b.id, b.line_no, b.kind, b.block_type, b.is_opaque, g.file_path, g.name FROM block b "
                            "LEFT JOIN program g ON g.id=b.program_id WHERE b.ctrl=? AND b.path=?", (ctrl, path)).fetchone()

    def file_of(ctrl, br):
        return br[5] or f"{ctrl}/_{br[6] or ''}.xml"

    def children(ctrl, path):
        r = block_row(ctrl, path)
        if not r:
            return []
        return conn.execute("SELECT id, name, line_no FROM block WHERE parent_id=? AND kind='userblock' "
                            "ORDER BY coalesce(line_no, 0), id", (r[0],)).fetchall()

    def pin_of(bid, name):
        return conn.execute("SELECT connection, value FROM pin WHERE block_id=? AND name=? ORDER BY id", (bid, name)).fetchone()

    def index_of(bid, rx):
        en = pin_of(bid, "Enable")
        m = re.search(rx, (en[0] or "") if en else "")
        return int(m.group(1)) if m else None

    vars_seen, vbad = set(), []

    def need_var(full, where):
        if full in vars_seen:
            return
        vars_seen.add(full)
        if not conn.execute("SELECT 1 FROM variable WHERE full_name=?", (full,)).fetchone():
            vbad.append(f"{where}: {full} not in the index")
        elif card(full) is None:
            vbad.append(f"{where}: {full} has no var card")

    n_steps = n_trans = n_cfg = n_bad_cfg = 0
    for k, t in sfc.items():
        ctrl, base = k.split("|", 1)
        tb = block_row(ctrl, base)
        if tb is None:
            continue                                  # reported by the key-set check
        if t.get("line") != tb[1] or t.get("file") != file_of(ctrl, tb):
            fail(f"{k}: line/file {t.get('line')} {t.get('file')!r} vs DB {tb[1]} {file_of(ctrl, tb)!r}")
        for p, full in (t.get("iface") or {}).items():
            need_var(full, f"{k} iface {p}")
        ps = children(ctrl, base + "/Perform_Step_Actions")
        steps = t.get("steps") or []
        if [s.get("n") for s in steps] != [r[1] for r in ps]:
            fail(f"{k}: steps {[s.get('n') for s in steps][:5]} vs Perform_Step_Actions userblocks {[r[1] for r in ps][:5]}")
        else:
            for s, r in zip(steps, ps):
                n_steps += 1
                i = index_of(r[0], r"\.Steps\[(\d+)\]")
                if s.get("i") != i or s.get("line") != r[2]:
                    fail(f"{k} step {s['n']}: i {s.get('i')} line {s.get('line')} vs Enable .Steps[{i}] line {r[2]}")
        ts = children(ctrl, base + "/Evaluate_Transitions")
        trans = t.get("trans") or []
        if [x.get("n") for x in trans] != [r[1] for r in ts]:
            fail(f"{k}: transitions {[x.get('n') for x in trans][:5]} vs Evaluate_Transitions userblocks {[r[1] for r in ts][:5]}")
        else:
            for x, r in zip(trans, ts):
                n_trans += 1
                i = index_of(r[0], r"\.Transitions\[(\d+)\]")
                if x.get("i") != i or x.get("line") != r[2]:
                    fail(f"{k} transition {x['n']}: i {x.get('i')} line {x.get('line')} vs Enable .Transitions[{i}] line {r[2]}")
                cfg = pin_of(r[0], "Configuration")
                dec = sfc_config(cfg[1] if cfg else None)
                if dec is None:
                    n_bad_cfg += 1
                    if x.get("bad") != 1 or "f" in x or "t" in x:
                        fail(f"{k} transition {x['n']}: Configuration {cfg and cfg[1]!r} not decodable but "
                             f"bad={x.get('bad')} f={x.get('f')} t={x.get('t')}")
                else:
                    n_cfg += 1
                    if [x.get("f"), x.get("t")] != list(dec) or "bad" in x:
                        fail(f"{k} transition {x['n']}: f {x.get('f')} t {x.get('t')} vs Configuration {cfg[1]!r} -> {list(dec)}")
        for s in steps + trans:
            if s.get("flag"):
                need_var(s["flag"], f"{k} {s.get('n')} flag")
            for a in s.get("a") or []:
                for w in a.get("w") or []:
                    need_var(w, f"{k} {s.get('n')} action {a.get('n')}")
            for rv in s.get("r") or []:
                need_var(rv[0], f"{k} {s.get('n')} condition")

    def source_ok(key, name, sk):
        t = sfc.get(key) or {}
        return any(s.get("n") == name for s in t.get("steps" if sk == "s" else "trans") or [])

    # ---- start-chain edges: endpoints, sources, every hop against the index and the task shards, flags
    n_hops = n_egd = n_d = 0
    pin_cache, seen_edge = {}, set()
    for e in edges:
        tag = f"edge {e.get('f')} {e.get('s')} -> {e.get('t')}"
        if e.get("f") not in sfc or e.get("t") not in sfc or e.get("f") == e.get("t"):
            fail(f"{tag}: f/t not two distinct sfc keys")
            continue
        if e.get("sk") not in ("s", "t") or not source_ok(e["f"], e.get("s"), e.get("sk")):
            fail(f"{tag}: source {e.get('sk')!r} {e.get('s')!r} is not a step/transition of {e['f']}")
        ek = (e["f"], e.get("s"), e.get("sk"), e["t"])
        if ek in seen_edge:
            fail(f"{tag}: duplicate edge")
        seen_edge.add(ek)
        p = e.get("p") or []
        run = (sfc[e["t"]].get("iface") or {}).get("RUN")
        if not p or "v" not in p[0] or p[-1].get("v") != run:
            fail(f"{tag}: path does not run from a variable to the target's RUN {run!r} (ends {p[-1] if p else None})")
        for j, h in enumerate(p):
            if "v" in h:
                need_var(h["v"], tag)
            elif "egd" in h:
                n_egd += 1
                prv = p[j - 1].get("v") if j else None
                nxt = p[j + 1].get("v") if j + 1 < len(p) else None
                hit = conn.execute("""SELECT 1 FROM egd_consumed c JOIN variable pv ON pv.id=c.producer_var_id
                                      JOIN variable lv ON lv.id=c.local_var_id
                                      WHERE c.exchange_id=? AND c.consumer_ctrl=? AND pv.full_name=? AND lv.full_name=?""",
                                   (h.get("egd"), h.get("to"), prv, nxt)).fetchone()
                if not hit:
                    fail(f"{tag}: EGD hop {h} ({prv} -> {nxt}) not in egd_consumed")
            elif "k" in h:
                n_hops += 1
                ctrl, path = h["k"].split("|", 1)
                ent = task_entry(tkey_of(ctrl, path))
                if not ent or h["k"] not in (ent.get("b") or {}):
                    fail(f"{tag}: block {h['k']} not in its task shard")
                ck = (h["k"], h.get("pin"))
                if ck not in pin_cache:
                    br = block_row(ctrl, path)
                    rows = conn.execute("SELECT line_no, direction, dir_source FROM pin WHERE block_id=? AND name=?",
                                        (br[0], h.get("pin"))).fetchall() if br else []
                    pin_cache[ck] = (br, rows)
                br, rows = pin_cache[ck]
                if not br or not rows:
                    fail(f"{tag}: pin {h['k']}.{h.get('pin')} not in the index")
                    continue
                ats = {f"{file_of(ctrl, br)}:{r[0]}" for r in rows}
                if h.get("at") not in ats:
                    fail(f"{tag}: {h['k']}.{h['pin']} at {h.get('at')!r} vs DB {sorted(ats)}")
                if h.get("ds") not in {f"{r[1] or '?'}/{r[2] or '-'}" for r in rows}:
                    fail(f"{tag}: {h['k']}.{h['pin']} ds {h.get('ds')!r} vs DB {[(r[1], r[2]) for r in rows]}")
                want_op = 1 if br[4] else None
                want_ub = 1 if not br[4] and br[2] != "block" else None
                if h.get("type") != (br[3] or br[2]) or h.get("op") != want_op or h.get("ub") != want_ub:
                    fail(f"{tag}: {h['k']} type/op/ub {h.get('type')!r}/{h.get('op')}/{h.get('ub')} "
                         f"vs DB {br[3] or br[2]!r}/{want_op}/{want_ub}")
            else:
                fail(f"{tag}: unknown path element {h}")
        typed = [h for h in p if "type" in h]
        d = 1 if all(h.get("ub") or h.get("op") or h["type"] in SFC_PASS for h in typed) else 0
        inf = 1 if any(h.get("ds", "").startswith("?") or h.get("ds", "").split("/")[-1] in SFC_INFER for h in typed) else 0
        op = 1 if any(h.get("op") for h in typed) else None
        n_d += d
        if e.get("d") != d or e.get("inf") != inf or e.get("op") != op:
            fail(f"{tag}: d/inf/op {e.get('d')}/{e.get('inf')}/{e.get('op')} vs path rule {d}/{inf}/{op}")
        # w: the literal SRC of the last block passed, only when it is a MOVE whose SRC is an N:/E: constant
        w = None
        if typed and typed[-1]["type"] == "MOVE":
            ctrl, path = typed[-1]["k"].split("|", 1)
            br = block_row(ctrl, path)
            src = conn.execute("SELECT conn_kind, connection FROM pin WHERE block_id=? AND name='SRC' ORDER BY id",
                               (br[0],)).fetchone() if br else None
            if src and src[0] in ("N", "E") and src[1]:
                w = src[1].split(":", 1)[-1]
        if e.get("w") != w:
            fail(f"{tag}: w {e.get('w')!r} vs last MOVE SRC literal {w!r}")
    for row in cut:
        if len(row) != 2 or row[0] not in sfc or not (source_ok(row[0], row[1], "s") or source_ok(row[0], row[1], "t")):
            fail(f"cut row {row} is not a step/transition of a sequence")
    for m in vbad[:5]:
        fail(m)
    bad[0] += max(0, len(vbad) - 5)
    (ok if not bad[0] else err)(
        f"sfc.json: {len(sfc)} sequences == DB SFC tasks; {n_steps} steps / {n_trans} transitions == wrapper userblocks "
        f"(Enable index; {n_cfg} Configurations re-decoded, {n_bad_cfg} undecodable flagged bad); {len(edges)} start-chain "
        f"edges ({n_d} direct) with {n_hops} pin hops (task shard, file:line, ds, type) and {n_egd} EGD hops; "
        f"{len(vars_seen)} variables in the index with cards; {len(cut)} cut sources; {bad[0]} mismatches")


def main(docs):
    global KEY, DATA_DIR
    data = Path(docs) / "data"
    DATA_DIR = data
    check_pages(docs)
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
    # value_overrides.csv: every variable with an override row (any status) and every variable whose value differs from
    # its snapshot value is checked. CONTRACT: d.vo only while the row is in effect (status applied AND the variable's
    # value is the corrected value AND differs from the snapshot value); a value may differ from the snapshot only then
    try:
        vo_rows = conn.execute("""SELECT v.id, v.full_name, o.status, o.value, o.snap_value, o.date, o.basis,
                                         (o.status='applied' AND v.value = o.value AND v.value IS NOT v.snap_value)
                                  FROM value_override o JOIN variable v ON v.ctrl=o.ctrl AND v.name=o.name""").fetchall()
    except sqlite3.OperationalError:
        vo_rows = []
    vo_applied = {r[0]: r for r in vo_rows if r[7]}
    vo_diff = {r[0]: r[1] for r in conn.execute("SELECT id, full_name FROM variable WHERE value IS NOT snap_value")}
    (ok if set(vo_diff) == set(vo_applied) else err)(
        f"value overrides: {len(vo_diff)} variables differ from their snapshot value, {len(vo_applied)} override rows in effect"
        + (f"; differ e.g. {sorted(vo_diff.get(i) or vo_applied[i][1] for i in set(vo_diff) ^ set(vo_applied))[:5]}"
           if set(vo_diff) != set(vo_applied) else ""))
    sample.extend(r[0] for r in vo_rows)
    sample.extend(i for i in vo_diff if i not in {r[0] for r in vo_rows})
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
        if (d.get("val") or None) != (v["value"] or None):      # empty values are omitted from the card
            nbad += 1
            err(f"card value mismatch {full}: {d.get('val')!r} vs db {v['value']!r}")
        if vid in vo_applied:
            o = vo_applied[vid]
            if d.get("val") != o[3] or d.get("vo") != [o[4], o[5], o[6]]:
                nbad += 1
                err(f"card value override {full}: val {d.get('val')!r} vo {d.get('vo')!r} vs corrected {o[3]!r} [{o[4]!r}, {o[5]!r}, {o[6]!r}]")
        elif "vo" in d:
            nbad += 1
            err(f"card {full} carries d.vo without an applied value override")
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
    ok(f"value overrides: {len(vo_applied)} in-effect rows were in the card sample (d.val corrected, d.vo = snapshot/date/basis), "
       f"{len(vo_rows) - len(vo_applied)} other rows checked to carry no d.vo (mismatches are counted above)")
    # every var shard: the cards carrying d.vo are exactly the in-effect rows; a sub row flags a corrected source value
    # (9th field 1) exactly when its source variable is one of them
    want_vo = {r[1] for r in vo_applied.values()}
    got_vo, sub_bad = set(), []
    for i in range(4096):
        s_ = f"{i:03x}"
        if s_ not in shard_cache:
            try:
                shard_cache[s_] = load(data / "var" / f"{s_}.json")["v"]
            except FileNotFoundError:
                shard_cache[s_] = {}
        for full, card in shard_cache[s_].items():
            if "vo" in (card.get("d") or {}):
                got_vo.add(full)
            for row in card.get("subs") or []:
                flagged = len(row) > 8 and row[8] == 1
                if flagged != (row[4] in want_vo if row[4] else False):
                    sub_bad.append(f"{full}.{row[0]}")
    (ok if got_vo == want_vo else err)(
        f"value overrides: {len(got_vo)} cards in all var shards carry d.vo, {len(want_vo)} rows in effect"
        + (f"; differ {sorted(got_vo ^ want_vo)[:5]}" if got_vo != want_vo else ""))
    (ok if not sub_bad else err)(f"alarm sub rows: corrected-source flag matches the in-effect rows ({len(sub_bad)} mismatches"
                                 + (f", e.g. {sub_bad[:3]})" if sub_bad else ")"))
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
    orows = conn.execute("SELECT DISTINCT p.block_id FROM pin p JOIN block b ON b.id=p.block_id "
                         "WHERE p.origin IS NOT NULL AND b.drawn=0").fetchall()
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
    # ---- blocks of encrypted programs, known only from the printed sheets (block.drawn): flagged, every pin from the
    #      drawing and cited, no recovered-interface counters; the program entry counts them; card refs cite the sheet
    dbad = 0
    drows = conn.execute("SELECT id, ctrl, path, kind FROM block WHERE drawn=1").fetchall()
    for bid, c_, path_, kind_ in random.sample(drows, min(30, len(drows))):
        key = f"{c_}|{path_}"
        ent = task_entry(tkey_of(c_, path_))
        blkj = ent["b"].get(key) if ent else None
        want_pins = sorted(r[0] for r in conn.execute("SELECT name FROM pin WHERE block_id=?", (bid,)))
        got = (blkj or {}).get("pins", [])
        pv = (ent or {}).get("pv") or {}
        if (blkj is None or blkj.get("dr") != 1 or blkj.get("rc") or blkj.get("opaque") or blkj.get("line") is not None
                or sorted(p[0] for p in got) != want_pins or any(p[11] != "g" or p[2] != "G" for p in got)
                or any(f"{key}#{p[0]}" not in pv for p in got)):
            dbad += 1
            if dbad <= 5:
                err(f"drawn block {key}: dr {(blkj or {}).get('dr')} rc {(blkj or {}).get('rc')} pins {len(got)} vs {len(want_pins)}")
    n_dr_web = {}
    for c_ in sorted({r[1] for r in drows}):
        for p_ in load(data / "program" / f"{c_}.json")["programs"]:
            if p_.get("dr"):
                n_dr_web[(c_, p_["name"])] = p_["dr"]
                if not p_.get("enc"):
                    dbad += 1
                    err(f"program {c_}/{p_['name']}: carries drawn blocks but is not flagged encrypted")
    n_dr_db = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT b.ctrl, g.name, count(*) FROM block b JOIN program g ON g.id=b.program_id WHERE b.drawn=1 AND b.kind<>'task' GROUP BY 1,2")}
    if n_dr_web != n_dr_db:
        dbad += 1
        err(f"program dr counts: web {len(n_dr_web)} programs / {sum(n_dr_web.values())} blocks vs DB {len(n_dr_db)} / {sum(n_dr_db.values())}")
    # a card's ref into a drawn block cites the sheet (9th field), and that program is no longer in the card's `enc`
    crows = conn.execute("SELECT v.full_name, b.ctrl, b.path, p.name, c.pdf, c.page, c.cell, g.name FROM pin p "
                         "JOIN block b ON b.id=p.block_id AND b.drawn=1 JOIN variable v ON v.id=p.var_id "
                         "JOIN program g ON g.id=b.program_id JOIN pin_cite c ON c.pin_id=p.id WHERE p.direction='O'").fetchall()
    for full, c_, path_, pin_, pdf_, page_, cell_, prog_ in random.sample(crows, min(30, len(crows))):
        s = sh(full)
        if s not in shard_cache:
            try:
                shard_cache[s] = load(data / "var" / f"{s}.json")["v"]
            except FileNotFoundError:
                shard_cache[s] = {}
        card = shard_cache[s].get(full) or {}
        refs = [r for r in card.get("w", []) if r[0] == c_ and r[2] == path_ and r[4] == pin_]
        if card.get("w_more") and not refs:
            continue                              # beyond the card's ref cap
        if len(refs) != 1 or len(refs[0]) < 9 or refs[0][8] != [pdf_, page_, cell_] or prog_ in (card.get("enc") or []):
            dbad += 1
            if dbad <= 5:
                err(f"card {full}: writer ref {c_}|{path_}.{pin_} cite {refs[0][8:] if refs else None} vs {[pdf_, page_, cell_]}, enc {card.get('enc')}")
    n_xml = conn.execute("SELECT count(*) FROM block b JOIN program g ON g.id=b.program_id WHERE b.drawn=1 AND g.block_count<>0").fetchone()[0]
    if n_xml:
        dbad += 1
        err(f"{n_xml} drawn blocks sit in programs whose XML has blocks of its own")
    (ok if not dbad else err)(f"sampled {min(30, len(drows))} of {len(drows)} blocks drawn only on the printed sheets "
                               f"({len(n_dr_db)} encrypted programs), {dbad} mismatches")
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
    # ---- execution order printed beside each block's title (block_cite): the block record's eo / pc
    has_bc = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='block_cite'").fetchone()
    brows = conn.execute("SELECT b.ctrl, b.path, c.exec_order, c.pdf, c.page, c.cell FROM block_cite c JOIN block b ON b.id=c.block_id").fetchall() if has_bc else []
    ebad = 0
    for ctrl_, path_, eo_, pdf_, page_, cell_ in random.sample(brows, min(20, len(brows))):
        rec = ((task_entry(tkey_of(ctrl_, path_)) or {}).get("b") or {}).get(f"{ctrl_}|{path_}") or {}
        if rec.get("eo") != eo_ or rec.get("pc") != [pdf_, page_, cell_]:
            ebad += 1
            if ebad <= 5:
                err(f"execution order {ctrl_}|{path_}: {rec.get('eo')} {rec.get('pc')} vs {eo_} {[pdf_, page_, cell_]}")
    nolist = conn.execute("SELECT b.ctrl, b.path FROM block b WHERE b.kind='block' AND b.id NOT IN (SELECT block_id FROM block_cite) "
                          "ORDER BY random() LIMIT 2000").fetchall() if has_bc else []
    for ctrl_, path_ in random.sample(nolist, min(10, len(nolist))):
        rec = ((task_entry(tkey_of(ctrl_, path_)) or {}).get("b") or {}).get(f"{ctrl_}|{path_}") or {}
        if "eo" in rec:
            ebad += 1
            err(f"execution order on an unprinted block {ctrl_}|{path_}: {rec.get('eo')}")
    (ok if brows and not ebad else err)(f"sampled {min(20, len(brows))} of {len(brows)} blocks with a printed execution order (+ unprinted ones carry none), {ebad} mismatches")
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
        # coverage: the diagram's "no downstream" rule needs a vu row for every output pin's variable of the task
        want_vu = {r[0] for r in conn.execute(
            """SELECT DISTINCT v.full_name FROM pin p JOIN block b ON b.id=p.block_id JOIN variable v ON v.id=p.var_id
               WHERE b.task_id=? AND p.direction='O'""", (tid,))}
        vu_missing = want_vu - set(ent.get("vu", {}))
        if vu_missing:
            tbad += 1
            if tbad <= 5:
                err(f"task {tkey}: {len(vu_missing)} of {len(want_vu)} output-pin variables missing from vu (re-export?)")
        if bad_len or got_vd != want_vd:
            tbad += 1
            if tbad <= 5:
                err(f"task {tkey}: {bad_len} pins not 12 fields; vd {len(got_vd)} vs {len(want_vd)} expected")
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

    # ---- sequences (SFC tasks): data/sfc.json against the index
    def card_of(full):
        s_ = sh(full)
        if s_ not in shard_cache:
            try:
                shard_cache[s_] = load(data / "var" / f"{s_}.json")["v"]
            except FileNotFoundError:
                shard_cache[s_] = {}
        return shard_cache[s_].get(full)
    check_sfc(conn, man, fileset, task_entry, tkey_of, card_of)
    print()
    print(f"{len(ERRORS)} error(s)" if ERRORS else "ALL OK")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else str(dbm.web_dir(REPO))))
