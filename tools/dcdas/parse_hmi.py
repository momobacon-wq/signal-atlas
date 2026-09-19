# -*- coding: utf-8 -*-
"""Project-wide HMI / symbol tables -> hmi_point, hmi_menu, format_spec, alarm_class, watch, library_help.

Sources (all relative to the checkout root):
  HmiScreens/navigation/tp_actPt_navPointSearchDbStd.csv   utf-8-sig, no header, rows FullPoint,UnitPrefix,Screen
  variable.display_screen                                  second hmi_point source (source='display_screen')
  HmiScreens/navigation/CIMNavigationMenuItemsStd.csv      header row (Block Name, Menu Item Name, ...)
  FormatSpecifications.xml                                 FormatSpecificationSet/FormatSpec(Name, Units, EngMin, EngMax, Prec, ...)
  AlarmClasses.xml                                         AlarmClass(Name, Description, Priority)
  <CTRL>/Watches/*.Watch                                   ToolConfig/ToolElements/ToolElement(Name, DataSourceName)
  <CTRL>/_<Program>.xml root attrs                         Program@LocalHelpFile -> <library dir>/X.mht (root element only)

Nav CSV full_point rule: a FullPoint is kept as-is when it matches variable.full_name, when its first segment is a
controller of the checkout, or when its first segment equals the first segment of its own UnitPrefix (EMAP1SVR rows);
otherwise UnitPrefix is prepended (`AGC1.X` + `G11.` -> `G11.AGC1.X`, the consumer-side naming of EGD copies;
`EKT50BF901_XQ01.bq` + `G11.` -> `G11.EKT50BF901_XQ01.bq`). When the prepended form matches a variable it wins
(`L11.L30SS` -> `G11.L11.L30SS`). Matching is exact first, then case-insensitive (the configuration tool names are
case-insensitive; `G11.L28fd` -> variable `G11.L28FD`); full_point keeps the CSV spelling.
Stage is project-wide and idempotent: rows it owns are deleted then re-inserted, one commit per section.
"""
import csv
import json
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List

from lxml import etree

from .db import Batch
from .inventory import Controller, controllers, library_folders, rel_to_root

NAV_CSV = Path("HmiScreens") / "navigation" / "tp_actPt_navPointSearchDbStd.csv"
MENU_CSV = Path("HmiScreens") / "navigation" / "CIMNavigationMenuItemsStd.csv"
FORMAT_XML = "FormatSpecifications.xml"
ALARM_CLASS_XML = "AlarmClasses.xml"
LOGIC_GLOB = "_*.xml"

MENU_COLS = ("Block Name", "Menu Item Name", "Sub Menu Item Name", "Item Name", "Cim Screen FileName", "Unit")


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _i(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


def _clear(el):
    el.clear()
    while el.getprevious() is not None:
        del el.getparent()[0]


class FullNameIndex:
    """variable.full_name -> id, exact first then case-insensitive (~319k entries)."""

    def __init__(self, conn):
        self.exact = {r[0]: r[1] for r in conn.execute("SELECT full_name,id FROM variable")}
        self.lower: Dict[str, int] = {}
        for k, v in self.exact.items():
            self.lower.setdefault(k.lower(), v)

    def get(self, name: str):
        v = self.exact.get(name)
        if v is None:
            v = self.lower.get(name.lower())
        return v


# ------------------------------------------------------------------------------------------- hmi_point (navcsv)
def nav_full_point(fp: str, up: str, full: FullNameIndex, ctrl_names) -> tuple:
    """-> (full_point, var_id|None). See module docstring for the prefix rule."""
    vid = full.get(fp)
    if vid is not None:
        return fp, vid
    pre = up + fp
    if up:
        vid = full.get(pre)
        if vid is not None:
            return pre, vid
    first = fp.split(".", 1)[0]
    if first in ctrl_names or (up and first == up.split(".", 1)[0]):
        return fp, None
    return (pre if up else fp), None


def parse_nav_csv(conn, root: Path, full: FullNameIndex, ctrl_names, log=print) -> dict:
    p = root / NAV_CSV
    st = {"csv_rows": 0, "inserted": 0, "resolved": 0, "prefixed": 0, "dup": 0, "bad": 0}
    conn.execute("DELETE FROM hmi_point WHERE source='navcsv'")
    if not p.exists():
        log(f"  WARN missing {rel_to_root(p, root)}")
        conn.commit()
        return st
    b = Batch(conn, "INSERT OR IGNORE INTO hmi_point(full_point,unit_prefix,screen,source,var_id) "
                    "VALUES(?,?,?,'navcsv',?)")
    seen = set()
    with open(p, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            st["csv_rows"] += 1
            if len(row) < 3 or not row[0].strip():
                st["bad"] += 1
                continue
            fp, up, screen = row[0].strip(), row[1].strip(), row[2].strip()
            full_point, var_id = nav_full_point(fp, up, full, ctrl_names)
            if full_point != fp:
                st["prefixed"] += 1
            if var_id is not None:
                st["resolved"] += 1
            key = (full_point, screen)
            if key in seen:
                st["dup"] += 1
                continue
            seen.add(key)
            b.add((full_point, up, screen, var_id))
    b.flush()
    st["inserted"] = b.n
    conn.commit()
    return st


# ------------------------------------------------------------------------------------ hmi_point (display_screen)
def display_screen_points(conn) -> int:
    conn.execute("DELETE FROM hmi_point WHERE source='display_screen'")
    cur = conn.execute("""INSERT OR IGNORE INTO hmi_point(full_point,unit_prefix,screen,source,var_id)
        SELECT full_name, ctrl||'.', trim(display_screen), 'display_screen', id
          FROM variable WHERE display_screen IS NOT NULL AND trim(display_screen)<>''""")
    conn.commit()
    return cur.rowcount


# ------------------------------------------------------------------------------------------------------ hmi_menu
def parse_menu_csv(conn, root: Path, log=print) -> int:
    p = root / MENU_CSV
    conn.execute("DELETE FROM hmi_menu")
    if not p.exists():
        log(f"  WARN missing {rel_to_root(p, root)}")
        conn.commit()
        return 0
    b = Batch(conn, "INSERT INTO hmi_menu(block,menu,submenu,item,screen,unit) VALUES(?,?,?,?,?,?)")
    with open(p, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            b.add(tuple((row.get(c) or "").strip() for c in MENU_COLS))
    b.flush()
    conn.commit()
    return b.n


# --------------------------------------------------------------------------------------------------- format_spec
def parse_format_specs(conn, root: Path, log=print) -> int:
    p = root / FORMAT_XML
    conn.execute("DELETE FROM format_spec")
    if not p.exists():
        log(f"  WARN missing {FORMAT_XML}")
        conn.commit()
        return 0
    b = Batch(conn, "INSERT OR REPLACE INTO format_spec(name,units,low,high,decimals,raw_json) VALUES(?,?,?,?,?,?)")
    for _ev, el in etree.iterparse(str(p), events=("end",), tag="FormatSpec", huge_tree=True):
        a = el.attrib
        name = a.get("Name")
        if name:
            b.add((name, a.get("Units"), _f(a.get("EngMin")), _f(a.get("EngMax")), _i(a.get("Prec")),
                   json.dumps(dict(a), ensure_ascii=False)))
        _clear(el)
    b.flush()
    conn.commit()
    return b.n


# --------------------------------------------------------------------------------------------------- alarm_class
def parse_alarm_classes(conn, root: Path, log=print) -> int:
    p = root / ALARM_CLASS_XML
    conn.execute("DELETE FROM alarm_class")
    if not p.exists():
        log(f"  WARN missing {ALARM_CLASS_XML}")
        conn.commit()
        return 0
    b = Batch(conn, "INSERT OR REPLACE INTO alarm_class(name,description,priority) VALUES(?,?,?)")
    for _ev, el in etree.iterparse(str(p), events=("end",), tag="AlarmClass", huge_tree=True):
        a = el.attrib
        if a.get("Name"):
            b.add((a.get("Name"), a.get("Description"), _i(a.get("Priority"))))
        _clear(el)
    b.flush()
    conn.commit()
    return b.n


# --------------------------------------------------------------------------------------------------------- watch
def parse_watches(conn, root: Path, ctrls: Iterable[Controller], log=print) -> dict:
    """<CTRL>/Watches/*.Watch -> watch. ctrl = folder controller; var_id via variable(ctrl=DataSourceName, name)."""
    conn.execute("DELETE FROM watch")
    b = Batch(conn, "INSERT INTO watch(ctrl,watch_file,var_name,datasource,var_id) VALUES(?,?,?,?,?)")
    idx: Dict[str, Dict[str, int]] = {}
    st = {"files": 0, "empty": 0, "rows": 0, "resolved": 0, "per_ctrl": {}}
    for c in ctrls:
        wd = c.folder / "Watches"
        if not wd.is_dir():
            continue
        t0 = time.time()
        n0 = b.n
        nfiles = 0
        for p in sorted(wd.glob("*.Watch")):
            nfiles += 1
            rel = rel_to_root(p, root)
            k = 0
            try:
                for _ev, el in etree.iterparse(str(p), events=("end",), tag="ToolElement", huge_tree=True):
                    name = el.get("Name")
                    ds = el.get("DataSourceName") or c.name
                    if name:
                        if ds not in idx:
                            idx[ds] = {r[0]: r[1] for r in
                                       conn.execute("SELECT name,id FROM variable WHERE ctrl=?", (ds,))}
                        vid = idx[ds].get(name)
                        if vid is not None:
                            st["resolved"] += 1
                        b.add((c.name, rel, name, ds, vid))
                        k += 1
                    _clear(el)
            except etree.XMLSyntaxError as e:
                log(f"  WARN {rel}: {e}")
            if k == 0:
                st["empty"] += 1
        b.flush()
        conn.commit()
        st["files"] += nfiles
        st["per_ctrl"][c.name] = (nfiles, b.n - n0)
        log(f"  watch {c.name:7s} files {nfiles:4d}  rows {b.n - n0:6d}  {time.time()-t0:5.1f}s")
    st["rows"] = b.n
    return st


# -------------------------------------------------------------------------------------------------- library_help
def root_attrib(path: Path) -> dict:
    """Attributes of the root element only (first 'start' event); the body is never parsed."""
    it = etree.iterparse(str(path), events=("start",), huge_tree=True)
    a = {}
    for _ev, el in it:
        a = dict(el.attrib)
        break
    del it
    return a


_SEP_RE = re.compile(r"[\\/]+")


def _last_component(win_path: str) -> str:
    parts = [x for x in _SEP_RE.split(win_path or "") if x]
    return parts[-1] if parts else ""


def parse_library_help(conn, root: Path, ctrls: Iterable[Controller], log=print) -> dict:
    """Program@Name -> (library folder holding Program@LocalHelpFile, mht rel path, '_<Name>.xml' rel path).

    Search order: the last component of Program@LibraryContainerWorkingDirectory (if it is a library folder of the
    checkout), then inventory.library_folders(). File-name matching is case-insensitive. First controller wins for a
    block_type; a differing mapping from another controller is logged and counted as a conflict.
    """
    conn.execute("DELETE FROM library_help")
    libs = library_folders(root)
    lib_names = {d.name for d in libs}
    files: Dict[str, Dict[str, str]] = {}
    for d in libs:
        files[d.name] = {q.name.lower(): q.name for q in d.iterdir()
                         if q.is_file() and (q.suffix.lower() == ".mht"
                                             or (q.name.startswith("_") and q.suffix.lower() == ".xml"))}
    st = {"programs": 0, "with_help": 0, "rows": 0, "mht_missing": 0, "def_missing": 0, "conflicts": 0}
    seen: Dict[str, tuple] = {}
    rows: List[tuple] = []
    for c in ctrls:
        for p in sorted(c.folder.glob(LOGIC_GLOB)):
            st["programs"] += 1
            try:
                a = root_attrib(p)
            except etree.XMLSyntaxError as e:
                log(f"  WARN {rel_to_root(p, root)}: {e}")
                continue
            help_file = (a.get("LocalHelpFile") or "").strip()
            if not help_file:
                continue
            st["with_help"] += 1
            name = a.get("Name") or p.stem[1:]
            lcwd = _last_component(a.get("LibraryContainerWorkingDirectory") or "")
            order = ([lcwd] if lcwd in lib_names else []) + [d.name for d in libs if d.name != lcwd]
            library = mht_path = def_file = None
            hk = help_file.lower()
            for ln in order:
                real = files[ln].get(hk)
                if real:
                    library = ln
                    mht_path = rel_to_root(root / ln / real, root)
                    break
            if library is None:
                st["mht_missing"] += 1
                library = lcwd or None
            if library:
                real = files.get(library, {}).get(f"_{name}.xml".lower())
                if real:
                    def_file = rel_to_root(root / library / real, root)
            if def_file is None:
                st["def_missing"] += 1
            row = (name, library, mht_path, def_file)
            if name in seen:
                if seen[name] != row:
                    st["conflicts"] += 1
                    log(f"  WARN library_help {name}: {c.name} says {row[1:]} vs {seen[name][1:]}")
                continue
            seen[name] = row
            rows.append(row)
    conn.executemany("INSERT OR IGNORE INTO library_help(block_type,library,mht_path,def_file) VALUES(?,?,?,?)", rows)
    conn.commit()
    st["rows"] = len(rows)
    return st


# ----------------------------------------------------------------------------------------------------------- run
def run(conn, root: Path, log=print) -> dict:
    """Project-wide stage: hmi_point(navcsv, display_screen), hmi_menu, format_spec, alarm_class, watch, library_help."""
    stats = {}
    ctrls = controllers(root)
    ctrl_names = {c.name for c in ctrls}

    t0 = time.time()
    full = FullNameIndex(conn)
    st = parse_nav_csv(conn, root, full, ctrl_names, log)
    del full
    stats["hmi_point_navcsv"] = st
    log(f"  hmi_point navcsv   {st['inserted']:7d}  (csv {st['csv_rows']}, dup {st['dup']}, bad {st['bad']}, "
        f"prefixed {st['prefixed']}, var_id {st['resolved']})  {time.time()-t0:5.1f}s")

    t0 = time.time()
    n = display_screen_points(conn)
    stats["hmi_point_display_screen"] = n
    log(f"  hmi_point display  {n:7d}  {time.time()-t0:5.1f}s")

    t0 = time.time()
    n = parse_menu_csv(conn, root, log)
    stats["hmi_menu"] = n
    log(f"  hmi_menu           {n:7d}  {time.time()-t0:5.1f}s")

    t0 = time.time()
    n = parse_format_specs(conn, root, log)
    stats["format_spec"] = n
    log(f"  format_spec        {n:7d}  {time.time()-t0:5.1f}s")

    t0 = time.time()
    n = parse_alarm_classes(conn, root, log)
    stats["alarm_class"] = n
    log(f"  alarm_class        {n:7d}  {time.time()-t0:5.1f}s")

    t0 = time.time()
    st = parse_watches(conn, root, ctrls, log)
    stats["watch"] = st
    log(f"  watch              {st['rows']:7d}  (files {st['files']}, empty {st['empty']}, var_id {st['resolved']})  "
        f"{time.time()-t0:5.1f}s")

    t0 = time.time()
    st = parse_library_help(conn, root, ctrls, log)
    stats["library_help"] = st
    log(f"  library_help       {st['rows']:7d}  (programs {st['programs']}, with help {st['with_help']}, "
        f"mht missing {st['mht_missing']}, def missing {st['def_missing']}, conflicts {st['conflicts']})  "
        f"{time.time()-t0:5.1f}s")
    return stats
