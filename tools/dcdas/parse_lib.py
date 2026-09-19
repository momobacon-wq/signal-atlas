# -*- coding: utf-8 -*-
"""Library folders (<dir>/Library.xml) -> lib_pin_usage + library_help.   Stage "lib", project-wide.

Each `<dir>/_*.xml` (PI class GeCss.Config.Blockware.UserBlockLibrary) is `UserBlockLibrary` (attr Name = the
LibraryType that controller programs reference) whose direct children are DEFINITIONS:
  ProgramDef  (library program; children: Pin Usage=… interface, TopUserBlock instances, ZK)
  UserBlock   (macro definition; Name = BlockType of controller UserBlock instances; children: Pin Usage=…, Block, ZK)
  TaskDef     (task definition; Name = BlockType of controller TopUserBlock instances, e.g. AI_Median)
  TopUserBlock (never seen at top level in this checkout, accepted anyway)
  ZK2188310901404A = encrypted blob.  A definition with only ZK children (no Pin/Block) is opaque.

lib_pin_usage(library, def_name, pin_name, usage):
  * one row per interface pin (direct <Pin> child of a definition), usage = Pin@Usage (Input|Output|Const|State)
  * one row per definition with pin_name='' and usage='__def__' (readable) or '__opaque__' (only ZK children)
  * extra rows with usage from nested UserBlock (macro) INSTANCES inside a definition, keyed by their BlockType
    (INSERT OR IGNORE, definition rows always win) - gives the interface of macros whose own definition is opaque
    (LDLG2_, MSTRTR_2S_, ...). TopUserBlock instances are NOT used: TaskDef pin names are templates
    (k_{Unit}{Device}DesignDP) that instances substitute, so instance pin names never match the type.
library_help(block_type PK, library, mht_path, def_file): definitions carrying LocalHelpFile, definitions with an
  adjacent <Name>.mht, and the library root Name with an adjacent <Name>.mht (INSERT OR IGNORE; parse_hmi may add
  controller-side rows, we only delete rows whose def_file points into a library folder = our own).

Memory: iterparse events=('start','end') with a depth counter; every element is cleared at its end event and its
previous siblings deleted, so the 164 MB _FFBIO.xml never materialises (only the current element + ancestors live).
"""
import time
from pathlib import Path

from lxml import etree

from .db import Batch
from .inventory import head_info, library_folders, rel_to_root

ZK_TAG = "ZK2188310901404A"
DEF_TAGS = ("ProgramDef", "UserBlock", "TaskDef", "TopUserBlock")
INST_TAGS = ("UserBlock",)       # macro instances keep the macro's pin names; TopUserBlock (TaskDef) instances
                                 # substitute {Unit}{Device} templates into pin names -> useless at type level
CONTENT_TAGS = ("Block", "UserBlock", "TopUserBlock")     # a definition with any of these (or Pin) is readable

DEF_SQL = "INSERT OR REPLACE INTO lib_pin_usage(library,def_name,pin_name,usage) VALUES(?,?,?,?)"
INST_SQL = "INSERT OR IGNORE INTO lib_pin_usage(library,def_name,pin_name,usage) VALUES(?,?,?,?)"
HELP_SQL = "INSERT OR IGNORE INTO library_help(block_type,library,mht_path,def_file) VALUES(?,?,?,?)"


def _peak_rss_mb():
    """Peak working set of this process in MB (Windows, ctypes; no psutil). None if unavailable."""
    try:
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        fn = k32.K32GetProcessMemoryInfo
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wt.DWORD]
        if fn(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.PeakWorkingSetSize / 1e6
    except Exception:
        pass
    return None


class FileStats:
    __slots__ = ("defs", "opaque", "pins", "pins_no_usage", "inst_pins", "help", "top_zk", "opaque_files",
                 "other_top", "help_missing")
    COUNTERS = ("defs", "opaque", "pins", "pins_no_usage", "inst_pins", "help", "top_zk", "opaque_files")

    def __init__(self):
        for k in self.COUNTERS:
            setattr(self, k, 0)
        self.other_top = {}
        self.help_missing = []

    def absorb(self, other):
        for k in self.COUNTERS:
            setattr(self, k, getattr(self, k) + getattr(other, k))
        for k, v in other.other_top.items():
            self.other_top[k] = self.other_top.get(k, 0) + v
        self.help_missing += other.help_missing


def parse_library_file(conn, lib: str, path: Path, root: Path, mhts: dict, log=print) -> FileStats:
    """Stream one _*.xml. `mhts` = {stem: Path} of the .mht files adjacent in the library folder."""
    st = FileStats()
    rel = rel_to_root(path, root)
    libdir = path.parent
    b_def = Batch(conn, DEF_SQL, 5000)
    b_inst = Batch(conn, INST_SQL, 5000)
    help_rows = []
    seen_help = set()

    def add_help(name, mht: Path):
        if name and name not in seen_help:
            seen_help.add(name)
            help_rows.append((name, lib, rel_to_root(mht, root), rel))
            if not mht.exists():
                st.help_missing.append(mht.name)

    depth = 0
    cur_name = None                 # Name of the current top-level definition
    cur_readable = False
    inst_stack = []                 # [(depth, BlockType)] of nested instances inside the current definition
    for ev, el in etree.iterparse(str(path), events=("start", "end"), huge_tree=True):
        if ev == "start":
            depth += 1
            tag = el.tag
            if depth == 1:
                root_name = el.get("Name") or path.name[1:-4]
                for cand in {root_name, path.name[1:-4]}:
                    if cand in mhts:
                        add_help(cand, mhts[cand])
            elif depth == 2:
                if tag in DEF_TAGS:
                    cur_name = el.get("Name", "")
                    cur_readable = False
                    inst_stack = []
                    st.defs += 1
                    lhf = el.get("LocalHelpFile")
                    if lhf:
                        add_help(cur_name, libdir / lhf)
                    elif cur_name in mhts:
                        add_help(cur_name, mhts[cur_name])
                elif tag == ZK_TAG:
                    st.top_zk += 1
                else:
                    st.other_top[tag] = st.other_top.get(tag, 0) + 1
            elif cur_name is not None:
                if depth == 3:
                    if tag == "Pin":
                        cur_readable = True
                        usage = el.get("Usage")
                        if usage is None:
                            st.pins_no_usage += 1
                        b_def.add((lib, cur_name, el.get("Name", ""), usage))
                        st.pins += 1
                    elif tag in CONTENT_TAGS:
                        cur_readable = True
                if tag in INST_TAGS:
                    bt = el.get("BlockType")
                    if bt:
                        inst_stack.append((depth, bt))
                elif tag == "Pin" and inst_stack and depth == inst_stack[-1][0] + 1:
                    b_inst.add((lib, inst_stack[-1][1], el.get("Name", ""), el.get("Usage")))
                    st.inst_pins += 1
        else:
            if inst_stack and inst_stack[-1][0] == depth:
                inst_stack.pop()
            if depth == 2 and cur_name is not None:
                if cur_readable:
                    b_def.add((lib, cur_name, "", "__def__"))
                else:
                    b_def.add((lib, cur_name, "", "__opaque__"))
                    st.opaque += 1
                cur_name = None
            depth -= 1
            el.clear()
            parent = el.getparent()
            if parent is not None:
                while el.getprevious() is not None:
                    del parent[0]
    if st.defs == 0 and st.top_zk > 0:
        st.opaque_files = 1     # every definition of this file is encrypted at top level
    b_def.flush()           # definition rows first (OR REPLACE), then instance evidence (OR IGNORE)
    b_inst.flush()
    if help_rows:
        conn.executemany(HELP_SQL, help_rows)
        st.help = len(help_rows)
    return st


def _help_unmatched_mht(conn, lib: str, d: Path, root: Path, mhts: dict) -> int:
    """`.mht` files in the folder that no definition claimed (their definitions are encrypted / named differently).
    Recorded as block_type = file stem with def_file = <lib>/Library.xml so they stay in this stage's delete scope."""
    used = {r[0] for r in conn.execute("SELECT mht_path FROM library_help WHERE def_file LIKE ?", (f"{lib}/%",))}
    rows = [(stem, lib, rel_to_root(p, root), rel_to_root(d / "Library.xml", root))
            for stem, p in sorted(mhts.items()) if rel_to_root(p, root) not in used]
    if rows:
        conn.executemany(HELP_SQL, rows)
    return len(rows)


def run(conn, root: Path, log=print) -> dict:
    """Parse every library folder. Owns lib_pin_usage entirely; owns library_help rows whose def_file is a library file."""
    t_all = time.time()
    conn.execute("DELETE FROM lib_pin_usage")
    stats = {}
    tot = FileStats()
    n_files = 0
    for d in library_folders(root):
        lib = d.name
        t0 = time.time()
        conn.execute("DELETE FROM library_help WHERE def_file LIKE ?", (f"{lib}/%",))
        mhts = {p.stem: p for p in d.glob("*.mht")}
        fs = FileStats()
        nf = 0
        for p in sorted(d.glob("_*.xml")):
            if "UserBlockLibrary" not in head_info(p)["class"]:
                log(f"  lib {lib}: skip {p.name} (class {head_info(p)['class'] or '?'})")
                continue
            s = parse_library_file(conn, lib, p, root, mhts, log)
            conn.commit()
            nf += 1
            fs.absorb(s)
        n_unmatched = _help_unmatched_mht(conn, lib, d, root, mhts) if mhts else 0
        fs.help += n_unmatched
        conn.commit()
        n_files += nf
        tot.absorb(fs)
        stats[lib] = {"files": nf, "opaque_files": fs.opaque_files, "defs": fs.defs, "opaque_defs": fs.opaque,
                      "pins": fs.pins, "inst_pins": fs.inst_pins, "help": fs.help, "help_unmatched_mht": n_unmatched,
                      "mht_files": len(mhts)}
        extra = f"  other-top={fs.other_top}" if fs.other_top else ""
        extra += f"  help-missing={fs.help_missing}" if fs.help_missing else ""
        log(f"  lib {lib:32s} files {nf:3d} (opaque {fs.opaque_files:3d})  defs {fs.defs:4d} (opaque {fs.opaque:2d})"
            f"  pins {fs.pins:6d}  inst {fs.inst_pins:6d}  help {fs.help:3d}/{len(mhts):3d}mht"
            f"  {time.time()-t0:5.1f}s{extra}")
    n_rows = conn.execute("SELECT count(*) FROM lib_pin_usage").fetchone()[0]
    n_help = conn.execute("SELECT count(*) FROM library_help").fetchone()[0]
    rss = _peak_rss_mb()
    log(f"  lib total: {n_files} files ({tot.opaque_files} fully opaque), {tot.defs} defs ({tot.opaque} opaque),"
        f" {tot.pins} def pins ({tot.pins_no_usage} w/o Usage), {tot.inst_pins} instance pins"
        f" -> lib_pin_usage {n_rows} rows, library_help {n_help} rows, {time.time()-t_all:.1f}s"
        + (f", peak RSS {rss:.0f} MB" if rss else ""))
    stats["_total"] = {"files": n_files, "opaque_files": tot.opaque_files, "defs": tot.defs,
                       "opaque_defs": tot.opaque, "pins": tot.pins, "inst_pins": tot.inst_pins, "rows": n_rows,
                       "help_rows": n_help}
    return stats
