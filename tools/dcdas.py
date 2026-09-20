# -*- coding: utf-8 -*-
"""dcdas - controller signal & logic index (Signal Atlas).

  py tools/dcdas.py build [--ctrl G11 ...] [--full] [--vars] [--logic] [--io] [--egd] [--hmi] [--lib] [--no-post]
  py tools/dcdas.py status
  py tools/dcdas.py find <pattern> [--ctrl X] [--limit 40]
  py tools/dcdas.py show <CTRL.NAME | NAME>
  py tools/dcdas.py trace <CTRL.NAME> [--up N] [--down N] [--max-lines 60]
  py tools/dcdas.py block <CTRL> <path>      | task <CTRL> <program> <task>
  py tools/dcdas.py io <tag|var|module>      | egd <var|ctrl> | screen <cim|var> | alarm <pattern>
  py tools/dcdas.py where <CTRL.NAME | CTRL block-path>
  py tools/dcdas.py lint | coverage | audit-type <BLOCK_TYPE> | pindir-import | export-web <docs_dir>
All commands accept --json. Output is deliberately compact (one fact per line) for Claude.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dcdas import db as dbm                      # noqa: E402
from dcdas import inventory as inv               # noqa: E402


def log(msg=""):
    print(msg, flush=True)


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ----------------------------------------------------------------------------------------------------- build
def cmd_build(a):
    root = dbm.src_root()
    if not root.exists():
        raise SystemExit(f"source root not found: {root} (set DCDAS_SRC or src_root in {dbm.config_path()})")
    path = dbm.db_path()
    if a.full and path.exists():
        path.unlink()
    conn = dbm.open_build(path)
    t0 = time.time()
    all_ctrls = inv.controllers(root)
    ctrls = [c for c in all_ctrls if not a.ctrl or c.name in a.ctrl]
    if a.ctrl and len(ctrls) != len(a.ctrl):
        raise SystemExit(f"unknown controller(s): {set(a.ctrl) - {c.name for c in ctrls}}")
    stages = [s for s in ("vars", "logic", "lib", "io", "egd", "hmi") if getattr(a, s)]
    if not stages:
        stages = ["vars", "logic", "lib", "io", "egd", "hmi"]
    log(f"source : {root}")
    log(f"db     : {path}")
    log(f"ctrls  : {' '.join(c.name for c in ctrls)}")
    log(f"stages : {' '.join(stages)}{'' if a.no_post else ' + post (resolve, direction, fts)'}")

    for c in ctrls:
        conn.execute("INSERT OR REPLACE INTO controller(name,kind,product_version,major_rev,minor_rev,last_mod,"
                     "coherency,redundancy,platform,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (c.name, c.kind, c.product_version, c.major_rev, c.minor_rev, c.last_mod, c.coherency,
                      c.redundancy, c.platform, now_iso()))
    conn.commit()

    if "vars" in stages:
        from dcdas import parse_vars
        log("[vars]")
        parse_vars.run(conn, root, ctrls, log)
    if "logic" in stages:
        from dcdas import parse_logic
        log("[logic]")
        parse_logic.run(conn, root, ctrls, log)
    if "lib" in stages and not a.ctrl:
        from dcdas import parse_lib
        log("[lib]")
        parse_lib.run(conn, root, log)
    if "io" in stages:
        from dcdas import parse_io
        log("[io]")
        parse_io.run(conn, root, ctrls, log)
    if "egd" in stages:
        from dcdas import parse_egd
        log("[egd]")
        parse_egd.run(conn, root, ctrls, log)
    if "hmi" in stages and not a.ctrl:
        from dcdas import parse_hmi
        log("[hmi]")
        parse_hmi.run(conn, root, log)

    # source_file ledger (size/mtime/coherency) for the files just consumed
    log("[ledger]")
    rows = []
    for sf in inv.source_files(root, [c.name for c in ctrls] if a.ctrl else None):
        coh = ""
        if sf.kind in ("logic", "variables", "device", "library", "egd", "io"):
            try:
                coh = inv.head_info(sf.path)["coherency"]
            except OSError:
                pass
        rows.append((sf.rel, sf.ctrl, sf.kind, sf.size, sf.mtime, None, coh, now_iso()))
    conn.executemany("INSERT OR REPLACE INTO source_file(path,ctrl,kind,size,mtime,sha1,coherency,parsed_at) "
                     "VALUES(?,?,?,?,?,?,?,?)", rows)
    conn.commit()

    if not a.no_post:
        from dcdas import resolve, direction
        log("[resolve]")
        resolve.run(conn, root, ctrls, log)
        log("[direction]")
        direction.run(conn, HERE, log)
        resolve.refresh_mirror_kinds(conn, log)
        log("[fts]")
        resolve.rebuild_fts(conn, log)

    dbm.set_meta(conn, "source_root", str(root))
    dbm.set_meta(conn, "built_at", now_iso())
    dbm.set_meta(conn, "schema_version", dbm.SCHEMA_VERSION)
    tcws = sorted(root.glob("*.tcw"))
    if tcws:
        dbm.set_meta(conn, "toolbox_version", inv.head_info(tcws[0])["version"])
    conn.commit()
    log("[summary]")
    for t in ("controller", "program", "task", "block", "pin", "variable", "io_point", "egd_produced",
              "egd_consumed", "hmi_point", "watch"):
        try:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            log(f"  {t:13s} {n:9d}")
        except Exception:
            pass
    if a.vacuum:
        log("[vacuum]")
        conn.execute("VACUUM")
    conn.close()
    log(f"done in {time.time()-t0:.0f}s -> {path} ({path.stat().st_size/1e6:.0f} MB)")


# ---------------------------------------------------------------------------------------------------- status
def cmd_status(a):
    conn = dbm.open_ro()
    root = dbm.src_root()
    out = {"db": str(dbm.db_path()), "built_at": dbm.get_meta(conn, "built_at"),
           "toolbox_version": dbm.get_meta(conn, "toolbox_version"), "controllers": [], "stale": {}}
    live = {c.name: c for c in inv.controllers(root)}
    for r in conn.execute("SELECT name,kind,minor_rev,indexed_at FROM controller ORDER BY name"):
        c = live.get(r["name"])
        out["controllers"].append({"name": r["name"], "kind": r["kind"], "indexed_minor_rev": r["minor_rev"],
                                   "current_minor_rev": c.minor_rev if c else None,
                                   "changed": bool(c and c.minor_rev != r["minor_rev"])})
    st = inv.stale_files(conn, root)
    out["stale"] = {k: len(v) for k, v in st.items()}
    out["stale_examples"] = {k: v[:8] for k, v in st.items()}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"db: {out['db']}")
    print(f"built_at: {out['built_at']}   toolbox: {out['toolbox_version']}")
    for c in out["controllers"]:
        flag = "CHANGED" if c["changed"] else "ok"
        print(f"  {c['name']:7s} {c['kind']:8s} indexed {c['indexed_minor_rev']}  now {c['current_minor_rev']}  {flag}")
    print(f"files: new={out['stale']['new']} changed={out['stale']['changed']} missing={out['stale']['missing']}")
    for k in ("new", "changed", "missing"):
        for p in out["stale_examples"][k]:
            print(f"  {k}: {p}")
    if any(out["stale"].values()) or any(c["changed"] for c in out["controllers"]):
        print("STALE: run  py tools/dcdas.py build")
    else:
        print("fresh")


# ----------------------------------------------------------------------------------------------- query cmds
def _q(a, fn):
    from dcdas import query, render
    conn = dbm.open_ro()
    res = fn(query, conn)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    else:
        print(render.render(res, a))


def cmd_find(a):
    _q(a, lambda q, c: q.find(c, a.pattern, ctrl=a.ctrl, kind=a.kind, limit=a.limit))


def cmd_show(a):
    _q(a, lambda q, c: q.show(c, a.signal, all_rows=a.all))


def cmd_trace(a):
    _q(a, lambda q, c: q.trace(c, a.signal, up=a.up, down=a.down, max_lines=a.max_lines))


def cmd_block(a):
    _q(a, lambda q, c: q.block(c, a.ctrl_name, a.path))


def cmd_task(a):
    _q(a, lambda q, c: q.task(c, a.ctrl_name, a.program, a.task))


def cmd_io(a):
    _q(a, lambda q, c: q.io(c, a.key, ctrl=a.ctrl))


def cmd_egd(a):
    _q(a, lambda q, c: q.egd(c, a.key, page=a.page))


def cmd_screen(a):
    _q(a, lambda q, c: q.screen(c, a.key))


def cmd_alarm(a):
    _q(a, lambda q, c: q.alarm(c, a.pattern, ctrl=a.ctrl, limit=a.limit))


def cmd_where(a):
    _q(a, lambda q, c: q.where(c, a.key))


def cmd_lint(a):
    _q(a, lambda q, c: q.lint(c, limit=a.limit))


def cmd_audit_type(a):
    _q(a, lambda q, c: q.audit_type(c, a.block_type, ctrl=a.ctrl, limit=a.limit))


def cmd_coverage(a):
    _q(a, lambda q, c: q.coverage(c, limit=a.limit))


def cmd_pindir_import(a):
    from dcdas import parse_manual
    parse_manual.run(HERE, a.pdf, log)


def cmd_export_web(a):
    from dcdas import export_web
    conn = dbm.open_ro()
    key = None if a.no_encrypt else dbm.web_passphrase(a.key_file)
    export_web.run(conn, Path(a.docs), HERE, log, passphrase=key)


# ------------------------------------------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="dcdas", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("--json", action="store_true")
        p.set_defaults(fn=fn)
        return p

    p = add("build", cmd_build, help="parse the checkout into the SQLite index")
    p.add_argument("--ctrl", nargs="*", help="only these controllers (skips project-wide stages)")
    p.add_argument("--full", action="store_true", help="delete the DB first")
    for s in ("vars", "logic", "lib", "io", "egd", "hmi"):
        p.add_argument(f"--{s}", action="store_true", help=f"run only the {s} stage (combinable)")
    p.add_argument("--no-post", action="store_true", help="skip resolve/direction/fts post-processing")
    p.add_argument("--vacuum", action="store_true")

    add("status", cmd_status, help="is the index up to date with the checkout?")
    p = add("find", cmd_find); p.add_argument("pattern"); p.add_argument("--ctrl"); p.add_argument("--kind", default="var"); p.add_argument("--limit", type=int, default=40)
    p = add("show", cmd_show); p.add_argument("signal"); p.add_argument("--all", action="store_true")
    p = add("trace", cmd_trace); p.add_argument("signal"); p.add_argument("--up", type=int, default=0); p.add_argument("--down", type=int, default=0); p.add_argument("--max-lines", type=int, default=60)
    p = add("block", cmd_block); p.add_argument("ctrl_name"); p.add_argument("path")
    p = add("task", cmd_task); p.add_argument("ctrl_name"); p.add_argument("program"); p.add_argument("task")
    p = add("io", cmd_io); p.add_argument("key"); p.add_argument("--ctrl")
    p = add("egd", cmd_egd); p.add_argument("key"); p.add_argument("--page")
    p = add("screen", cmd_screen); p.add_argument("key")
    p = add("alarm", cmd_alarm); p.add_argument("pattern"); p.add_argument("--ctrl"); p.add_argument("--limit", type=int, default=40)
    p = add("where", cmd_where); p.add_argument("key")
    p = add("lint", cmd_lint); p.add_argument("--limit", type=int, default=40)
    p = add("coverage", cmd_coverage); p.add_argument("--limit", type=int, default=40)
    p = add("audit-type", cmd_audit_type, help="per-pin audit of one block type"); p.add_argument("block_type"); p.add_argument("--ctrl"); p.add_argument("--limit", type=int, default=200)
    p = add("pindir-import", cmd_pindir_import); p.add_argument("--pdf", nargs="*", help="manual PDFs (default: known set)")
    p = add("export-web", cmd_export_web); p.add_argument("docs", nargs="?", default=str(HERE.parent / "docs"))
    p.add_argument("--key-file", help="file holding the site passphrase (default: web_key_file in the local config)")
    p.add_argument("--no-encrypt", action="store_true", help="plain JSON export (local testing only; never publish)")

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    main()
