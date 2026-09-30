# -*- coding: utf-8 -*-
"""parse_pei - read the configuration tool's PRINTED reports and turn the printed
logic sheets into pin-level evidence.

Why this exists: the checkout's XML hides two things the drawings show plainly.
  * encrypted programs - the XML has no blocks at all, the print draws them
  * opaque UserBlocks  - the XML shows an instance with no interface, the print
                         names every pin and the wire on it
Until now that gap was closed by hand (tools/xref_manual.csv, transcribed from
photographs of these same sheets).

This module NEVER writes to the index. It fills a separate corpus DB (print.sqlite)
of raw observations; promoting them into pin rows is a later, separate step. The
corpus is kept out of index.sqlite on purpose: the index is deleted and rebuilt
whenever the schema or the checkout changes, and re-parsing 11k sheets costs
minutes, so the two lifecycles stay apart. For the same reason the corpus stores
block PATHS and variable NAMES, never index row ids.

Report kinds in the export (one set per controller):
  <CTRL>_P.pdf   printed logic sheets       <- this module
  <CTRL>_C.pdf   variable cross-reference   (confirmation only: measured 0
                 corrections over 327k variables, so it is not ingested)
  <CTRL>_D.pdf   device summary / revisions

Sheet geometry (reverse-engineered; every number verified against the real files):
  page       1190.52 x 841.92 pt (A3 landscape), rotation 0
  body       the single stroked rect [21,21,1168.7,713.9], stroke width 0.36
  grid       columns A..Z at x = 53.00 + 43.276*i, rows 00..29 at y = 37.70 + 22.730*j
  PIN STUB   a 2.0-4.5 pt horizontal segment at stroke width >= 0.9 butted against
             a block edge, drawn once per SHOWN pin - wired or not: an unwired pin
             has a bare stub or one carrying its grey default value. Left stub =
             input side, right stub = output side. The whole parser keys on this.
  pin label  a span inside the block rect within 8 pt of its own edge and at least
             3 pt closer to that edge than to the opposite one
  wire label the nearest free span whose gap to the stub's free end is <= 6 pt
  colours    0x191970 explicit pin label, 0xA9A9A9 the pin's default value: the pin
             itself holds no variable or constant (wire_kind 'default'); it may
             still be fed by a line from another block, whose link is stored on
             the source pin. 0x008000 comment / numeric literal, 0xFFFF00 exec-
             order badge. A black literal is usually a constant set on the pin.
  name       span at font size ~4.6, black -> block instance name

Measured against the index over 44 tasks (3 controllers, 750 blocks, 3.7k pin rows):
  block names      750/750 found, 0 spurious
  exec order       750/750 exact
  pin name         1930 agree, 23 disagree - ALL 23 on two opaque macros whose
                   indexed pins are recovered guesses (origin='decl', dir_source='R')
  side->direction  1846/1856 agree, 10 disagree - the same two macros
  i.e. zero disagreements against a pin the XML states plainly.
Unlabelled gate-symbol stubs (AND/OR/NOT/LATCH/timers) get pin=None on purpose:
matching them positionally onto IN1/IN2/IN3 measured 73%, and a wrong pin name is
a wrong wire.
"""
import hashlib
import os
import re
import sqlite3
from pathlib import Path

from . import db as dbm

BODY = (21.0, 21.0, 1168.7, 713.9)
COL_X0, COL_DX = 53.00, 43.2760
ROW_Y0, ROW_DY = 37.70, 22.7300
STUB_LW = 0.9              # >= this stroke width is wire / stub ink
STUB_LEN = (2.0, 4.5)      # pt, a pin stub's length
ROW_TOL = 3.2              # pt, stub <-> label vertical tolerance
PIN_LBL_OFF = 8.0          # pt, pin label -> its own block edge
LBL_GAP = 6.0              # pt, stub free end -> wire label
GLUE = 1.6                 # pt, shape pieces this close are one block symbol
DARKRED = (0.5019609928131104, 0.0, 0.0)

SHEETREF = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Z]\d{1,2}$")
LINKREF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
NUMLIT = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
EQNCHARS = re.compile(r"[*+/><=&|^-]")
BOOLLIT = {"TRUE", "FALSE"}            # compared upper-cased
# an enumeration / bit-set literal wired to a pin: "MAN-AUTO-LOCK", "AVAIL-MOM_OUT"
# (2+ characters a token, so the equation 'A-B' on a CALC stays an equation)
ENUMLIT = re.compile(r"^[A-Z][A-Z0-9_]+(-[A-Z][A-Z0-9_]+)+$")
# bare marker glyphs the sheet prints beside a wire; never a variable
MARKERS = {"EGD", "A", "N", "S", "R", "T", "P", "I", "O", "BQ", "L", "H"}
# program / task / block names may carry "-" and "&" (e.g. "ST_LPExhP-TAL")
SWPATH = re.compile(r"^[A-Za-z0-9_&-]+(\.[A-Za-z0-9_&-]+)*$")

PRINT_SCHEMA = "7"

PRINT_DDL = r"""
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS print_pdf(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, ctrl TEXT NOT NULL,
  sha256 TEXT, size_bytes INTEGER, page_count INTEGER, device_name TEXT,
  print_major TEXT, print_minor TEXT, tz_offset_min INTEGER, tool_version TEXT,
  gate_status TEXT CHECK(gate_status IN (
    'pass','unreadable','truncated','wrong_device','mixed_device',
    'revision_mismatch','tool_version_mismatch')),
  gate_note TEXT, n_sheets INTEGER DEFAULT 0, n_tasks INTEGER DEFAULT 0,
  n_other INTEGER DEFAULT 0, scanned_at TEXT);
CREATE TABLE IF NOT EXISTS print_sheet(
  id INTEGER PRIMARY KEY, pdf_id INTEGER NOT NULL, page INTEGER NOT NULL,
  ctrl TEXT, sw_path TEXT, program TEXT, task TEXT, block_prefix TEXT,
  sheet_no TEXT, cont_on TEXT, mod_rev TEXT, owner_path TEXT,
  kind TEXT CHECK(kind IN ('sheet','tasks','other')),
  map_method TEXT CHECK(map_method IN ('exact','repair','internals','encrypted','none')),
  UNIQUE(pdf_id, page));
CREATE INDEX IF NOT EXISTS ix_print_sheet_path ON print_sheet(ctrl, sw_path);
CREATE TABLE IF NOT EXISTS print_pin(
  id INTEGER PRIMARY KEY, sheet_id INTEGER NOT NULL, cell TEXT,
  block_label TEXT NOT NULL, block_path TEXT, exec_order INTEGER,
  pin_name TEXT, side TEXT CHECK(side IN ('L','R')),
  direction TEXT CHECK(direction IN ('I','O')),
  wire_kind TEXT CHECK(wire_kind IN ('var','field','const','default','offpage','link','equation','marker','none')),
  wire_text TEXT, var_name TEXT, wire_field TEXT);
CREATE INDEX IF NOT EXISTS ix_print_pin_sheet ON print_pin(sheet_id);
CREATE INDEX IF NOT EXISTS ix_print_pin_block ON print_pin(block_path, pin_name);
CREATE INDEX IF NOT EXISTS ix_print_pin_var ON print_pin(var_name);
"""


# ------------------------------------------------------------------ local paths
def pei_dir() -> Path:
    """Folder holding the <CTRL>_P.pdf exports. Local setting, never in the repo."""
    p = os.environ.get("DCDAS_PEI") or dbm.config().get("pei_dir")
    if p:
        return Path(p)
    return dbm.src_root() / "PEI" / "PEI"


def print_db_path() -> Path:
    p = os.environ.get("DCDAS_PRINT_DB") or dbm.config().get("print_db")
    return Path(p) if p else dbm.local_dir() / "print.sqlite"


def open_print(path: Path = None, create=True, partial=False) -> sqlite3.Connection:
    """partial: the caller will scan only some controllers / pages. An old-schema corpus
    is rebuilt from scratch, so a partial scan must refuse rather than silently drop
    every other controller."""
    path = path or print_db_path()
    if not create and not path.exists():
        raise SystemExit(f"print corpus not built: {path}  (run: py tools/dcdas.py print-scan)")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    if create and conn.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
        r = conn.execute("SELECT value FROM meta WHERE key='print_schema'").fetchone()
        if not r or r[0] != PRINT_SCHEMA:          # derived data: rebuilt, never migrated
            if partial:
                conn.close()
                raise SystemExit(f"print corpus is schema {r[0] if r else '?'}, this code writes "
                                 f"{PRINT_SCHEMA}: run a FULL print-scan (no --ctrl / --pages) to rebuild it")
            conn.executescript("DROP TABLE IF EXISTS print_pin; DROP TABLE IF EXISTS print_sheet; "
                               "DROP TABLE IF EXISTS print_pdf; DROP TABLE IF EXISTS meta;")
    conn.executescript(PRINT_DDL)
    return conn


def open_print_ro(path: Path = None) -> sqlite3.Connection:
    """The corpus for reading only (print-check, print-show, the pointer in show): no DDL,
    no pragmas, so a reader can never alter it or race a running print-scan into a bad state."""
    path = path or print_db_path()
    if not path.exists():
        raise SystemExit(f"print corpus not built: {path}  (run: py tools/dcdas.py print-scan)")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    r = conn.execute("SELECT value FROM meta WHERE key='print_schema'").fetchone()
    if not r or r[0] != PRINT_SCHEMA:     # an old corpus means other things by its kinds
        conn.close()
        raise SystemExit(f"print corpus is schema {r[0] if r else '?'}, this code reads {PRINT_SCHEMA}: "
                         "run  py tools/dcdas.py print-scan")
    return conn


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- title block
def read_header(page):
    """Read only the cells this tool needs: Device Name, Software Path, Sh. No.,
    Cont. on Sh., Module Revision. Anchored on the printed LABEL words, because
    the title block is not always at the same y (logic sheets put it at y~713,
    the program task tables at y~781).

    The other title-block cells carry customer / site / personnel identifiers and
    are deliberately never read, so they cannot reach the repo or the site.
    """
    ws = [w for w in page.get_text("words") if w[1] > 600]
    lab = {}
    for w in ws:
        lab.setdefault(w[4], []).append(w)

    def anchor(*names):
        for n in names:
            if n in lab:
                w = min(lab[n], key=lambda t: t[0])
                return w[0], w[1]
        return None

    def label(first, second):
        """The cell whose label is the two words '<first> <second>' on one line. The strip
        can carry the first word elsewhere too (a second 'Software' in another cell), and
        taking the leftmost one then reads the wrong cell."""
        for w in sorted(lab.get(first, []), key=lambda t: t[0]):
            if any(abs(t[1] - w[1]) < 1.0 and w[2] <= t[0] <= w[2] + 8.0 for t in lab.get(second, [])):
                return w[0], w[1]
        return None

    def value(a, xmax=1e9, dy=5.7):
        if a is None:
            return ""
        x0, y0 = a
        v = [w for w in ws if x0 - 3 <= w[0] < xmax and y0 + dy - 1.2 <= w[1] < y0 + dy + 2.2]
        v.sort(key=lambda w: w[0])
        return " ".join(w[4] for w in v)

    a_sw, a_dev = label("Software", "Path"), label("Device", "Name")
    a_sh, a_cont, a_mod = anchor("No."), anchor("Cont."), anchor("Module")
    path = value(a_sw, a_sw[0] + 260) if a_sw else ""
    # the cell clips long paths, sometimes right after a dot
    path = path.rstrip(".")
    # A Software Path is always a dotted identifier chain: anything else is a misread
    # and is dropped, rather than stored as a path that addresses nothing.
    if not SWPATH.match(path):
        path = ""
    prog, _, task = path.partition(".")
    return {
        "device": value(a_dev, a_dev[0] + 120) if a_dev else "",
        "path": path, "program": prog, "task": task,
        "sheet": value((a_sh[0] - 11, a_sh[1]), a_sh[0] + 130) if a_sh else "",
        "cont_on": value(a_cont, (a_sh[0] - 12) if a_sh else 1e9) if a_cont else "",
        "mod_rev": value(a_mod, a_mod[0] + 120) if a_mod else "",
    }


def revision_stamps(page):
    """-> (build_major, build_minor) exactly as printed ('3/5/2025 08:51:08').

    The stamps live in the title block's first column (cell x 21.0-148.6), label
    row at y, value row at y+5.5.  Both words of the value (date and time) must be
    taken, and nothing from the next cell across: 'Print Date' sits at x 149.4 and
    bleeds into the value if the window is a fixed +150 pt.
    'Last Modified' is deliberately NOT used: on some controllers it carries the
    day the sheet was printed rather than the build.
    """
    ws = [w for w in page.get_text("words") if w[1] > 600]
    out = {}
    for w in ws:
        if w[4] != "Build" or w[0] > 60:  # noqa: E501 - the stamps are column 1 only
            continue
        qual = [t[4] for t in ws if abs(t[1] - w[1]) < 1.0 and w[2] <= t[0] < w[0] + 40]
        key = "major" if "Major" in qual else ("minor" if "Minor" in qual else None)
        if not key:
            continue
        v = [t for t in ws if 18.0 <= t[0] < 145.0 and w[1] + 4.0 <= t[1] < w[1] + 8.0]
        v.sort(key=lambda t: t[0])
        out[key] = " ".join(t[4] for t in v).strip()
    return out.get("major", ""), out.get("minor", "")


def tool_version(page):
    """The configuration tool's version, from the title block's 7th column
    (x 658.7-786.1) on the same row as 'Device Name'.

    Located by CELL, not by matching the printed label: the label is the vendor's
    product name, which must not appear in this repo, and a bare version pattern
    would also match the Module Revision cell a few columns away.
    """
    ws = [w for w in page.get_text("words") if w[1] > 600]
    a = [w for w in ws if w[4] == "Device"]
    if not a:
        return ""
    y = min(a, key=lambda t: t[0])[1]
    v = [t for t in ws if 650.0 <= t[0] < 790.0 and y + 4.0 <= t[1] < y + 8.0]
    v.sort(key=lambda t: t[0])
    t = " ".join(x[4] for x in v).strip()
    return t if re.match(r"^V\d{2}\.\d{2}\.\d{2}[A-Z]?$", t) else ""


def page_kind(page):
    """'sheet' = a logic sheet, 'tasks' = a Program task table, 'other' = front matter."""
    for d in page.get_drawings():
        r = d["rect"]
        if (abs(r.x0 - BODY[0]) < 1.5 and abs(r.y0 - BODY[1]) < 1.5
                and abs(r.x1 - BODY[2]) < 1.5 and abs(r.y1 - BODY[3]) < 1.5):
            return "sheet"
    t = page.get_text("text")[:3000]
    if "Execution Order" in t and "Frame Multiplier" in t:
        return "tasks"
    return "other"


def grid_cell(x, y):
    c = min(max(int(round((x - COL_X0) / COL_DX)), 0), 25)
    r = min(max(int(round((y - ROW_Y0) / ROW_DY)), 0), 29)
    return "%s%02d" % (chr(65 + c), r)


# ----------------------------------------------------------------------- ink
def _collect_ink(page):
    shapes, stubs, badges = [], [], []
    for d in page.get_drawings():
        r = d["rect"]
        lw = d.get("width") or 0.0
        if r.y1 > BODY[3] + 1 or r.y0 < BODY[1] - 1:
            continue
        if r.width > 1000 and r.height > 600:          # the sheet frame
            continue
        if d["type"] == "f":
            # pure fills: dashed-wire dashes and white text knock-outs. Never a
            # block outline; keeping them merges a whole sheet into one block.
            continue
        if lw >= STUB_LW:
            for it in d["items"]:
                if it[0] == "l":
                    p, q = it[1], it[2]
                    if abs(p.y - q.y) < 0.25 and STUB_LEN[0] <= abs(q.x - p.x) <= STUB_LEN[1]:
                        stubs.append((min(p.x, q.x), max(p.x, q.x), p.y))
            continue
        f = d.get("fill")
        if f and max(abs(a - b) for a, b in zip(f, DARKRED)) < 0.02:
            badges.append(r)
            continue
        if f and tuple(f) == (1.0, 1.0, 1.0):          # white text knockout
            continue
        if (8.5 <= r.width <= 13.5 and 4.0 <= r.height <= 7.0 and d["type"] == "s"
                and len(d["items"]) == 1 and d["items"][0][0] == "re"):
            badges.append(r)                            # white exec-order badge
            continue
        # the hairline capsules round the compressed off-page refs are 20-60 x 2.4
        # pt; no real block symbol is under 4 pt tall. Only drop them at lw <= 0.40:
        # a 0.48 outline IS a block and dropping it costs every pin on it.
        if (r.height < 4.0 or r.width < 2.0) and lw <= 0.40:
            continue
        shapes.append(r)
    return shapes, stubs, badges


def _cluster(shapes, fitz):
    """Union shape pieces that touch (gate body + inverter bubble + divider)."""
    out = []
    for r in shapes:
        r = +r                      # PyMuPDF: a 0-area rect never .intersects(),
        if r.height < 0.1:          # which would split every OR gate in two
            r.y1 = r.y0 + 0.1
        if r.width < 0.1:
            r.x1 = r.x0 + 0.1
        out.append(r)
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            if out[i] is None:
                continue
            for j in range(i + 1, len(out)):
                if out[j] is None:
                    continue
                if (fitz.Rect(out[i]) + (-GLUE, -GLUE, GLUE, GLUE)).intersects(out[j]):
                    out[i] |= out[j]
                    out[j] = None
                    changed = True
    return [r for r in out if r is not None]


def _union(rs, fitz):
    if not rs:
        return None
    u = fitz.Rect(rs[0])
    for q in rs[1:]:
        u |= q
    return u


def _collect_spans(page):
    seen, out = set(), []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            hor = abs(ln["dir"][1]) < 0.01
            for s in ln["spans"]:
                t = s["text"].strip()
                if not t:
                    continue
                x0, y0, x1, y1 = s["bbox"]
                if y0 > BODY[3] or y1 < BODY[1]:
                    continue
                k = (round(x0, 1), round(y0, 1), t)     # spans are emitted twice
                if k in seen:
                    continue
                seen.add(k)
                out.append({"t": t, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                            "cy": (y0 + y1) / 2.0, "sz": round(s["size"], 1),
                            "color": s["color"], "font": s["font"], "hor": hor})
    return out


def _is_grid_label(s):
    return 5.4 < s["sz"] < 5.9 and (
        s["x0"] < 31 or s["x1"] > 1156 or s["y1"] < 27 or s["y0"] > 706)


def classify_wire(text, color, block_names):
    """-> wire_kind for the label found on a stub. Colour first: grey text is the pin's
    default value, printed when the pin itself holds no variable or constant ('default').
    That is NOT the same as unwired: a line from another block is stored on the SOURCE
    pin only, so the target pin still shows its grey default. A black literal is usually
    a constant set on the pin (ANALOG_ALARM prints its INH default in black, though).
    A variable-name lookup that overrides the look of the text happens in scan()."""
    if color == 0xA9A9A9:
        return "default"
    if NUMLIT.match(text) or text.upper() in BOOLLIT or ENUMLIT.match(text):
        return "const"
    if EQNCHARS.search(text):
        return "equation"
    if SHEETREF.match(text):
        return "offpage"
    if LINKREF.match(text) and text.split(".")[0] in block_names:
        return "link"
    if text in MARKERS:
        return "marker"
    return "var"


def parse_page(doc, pno, fitz):
    """pno is 0-based. -> one dict per pin STUB. A pin with nothing on it has a stub
    too: bare (wire_kind 'none') or carrying its grey default ('default'). 'none' means
    "no label at the stub's end": unwired, or a line drawn to another block whose label
    sits only at the far end. Hidden pins are not drawn at all."""
    page = doc[pno]
    hdr = read_header(page)
    shapes, stubs, badges = _collect_ink(page)
    rects = _cluster(shapes, fitz)
    spans = [s for s in _collect_spans(page) if not _is_grid_label(s)]

    names = [s for s in spans if 4.3 <= s["sz"] <= 4.9 and s["color"] == 0 and s["hor"]]
    badge_txt = [s for s in spans if s["color"] == 0xFFFF00 or
                 any(b.x0 - .6 <= s["x0"] and s["x1"] <= b.x1 + .6 and
                     b.y0 - 2.5 <= s["cy"] <= b.y1 + 2.5 for b in badges)]
    badge_ids = {id(s) for s in badge_txt}

    blocks, used = [], set()
    for n in sorted(names, key=lambda s: (s["y0"], s["x0"])):
        best, bd = None, 1e9
        for i, r in enumerate(rects):
            if i in used:
                continue
            if not (r.y0 - 12.0 <= n["y0"] <= r.y0 + 3.0):
                continue
            if not (r.x0 - 8.0 <= n["x0"] <= r.x0 + max(34.0, r.width * .8)):
                continue
            d = abs(n["y0"] - r.y0) * 3 + abs(n["x0"] - r.x0)
            if d < bd:
                best, bd = i, d
        nm = n["t"]
        if ":" in nm:                       # device blocks print "<instance>:<type>"
            nm = nm.split(":", 1)[0].strip()
        blocks.append({"name": nm, "rect": None if best is None else rects[best],
                       "span": n, "exec": None})
        if best is not None:
            used.add(best)

    # Block symbols drawn edge to edge merge into ONE cluster, and only the top name gets
    # it; the other blocks would be dropped with all their pins, and their stubs credited
    # to the first. A block left without a rectangle takes its OWN outline piece: the
    # frame whose top-left sits just left of its name and at most 18 pt below it (inside
    # the box for peer-health blocks). A device block's type text ('DUALSEL_V2') and a
    # symbol's text ('1+sTC') sit 11-36 pt right of the frame edge, so they never match.
    # The block that held the merged cluster is narrowed to its own outline the same way.
    def _outline(n):
        c = [q for q in shapes if n["x0"] - 5.5 <= q.x0 <= n["x0"] - 1.5
             and n["y0"] - 3.0 <= q.y0 <= n["y0"] + 18.0 and q.width >= 8.0 and q.height >= 4.0]
        if not c:
            return None
        o = min(c, key=lambda q: (q.y0, -q.width * q.height))
        return _union([q for q in shapes if (o + (-0.6, -0.6, 0.6, 0.6)).contains(q)], fitz)

    split = set()
    for b in blocks:
        if b["rect"] is None:
            o = _outline(b["span"])
            if o is not None:
                owner = next((a for a in blocks if a["rect"] is not None and a["rect"].contains(o)), None)
                b["rect"] = o
                if owner is not None:
                    split.add(id(owner))
    for a in blocks:
        if id(a) in split:
            o = _outline(a["span"])
            if o is not None:
                a["rect"] = o

    for s in badge_txt:                     # the exec-order badge sits right of the name
        cand = [b for b in blocks if abs(b["span"]["cy"] - s["cy"]) < 4.0
                and s["x0"] >= b["span"]["x1"] - 1]
        if cand and s["t"].strip().isdigit():
            b = min(cand, key=lambda b: s["x0"] - b["span"]["x1"])
            if b["exec"] is None:
                b["exec"] = int(s["t"])

    name_ids = {id(b["span"]) for b in blocks}
    body = [s for s in spans if id(s) not in badge_ids and id(s) not in name_ids]
    bnames = {b["name"] for b in blocks}
    allr = [b["rect"] for b in blocks if b["rect"] is not None]
    # a span inside SOME block rect can never be another block's wire label
    free = [s for s in body
            if not any(r.x0 - 0.6 <= s["x0"] and s["x1"] <= r.x1 + 0.6
                       and r.y0 <= s["cy"] <= r.y1 for r in allr)]
    stubs = sorted({(round(a, 1), round(b_, 1), round(c, 1)) for a, b_, c in stubs})

    def _claims(r):
        """-> [(stub, side, distance of its block end from the edge)] for one block."""
        out = []
        for st in stubs:
            sx0, sx1, sy = st
            if not (r.y0 - 3 <= sy <= r.y1 + 3):
                continue
            if abs(sx1 - r.x0) < 4.0:
                out.append((st, "L", abs(sx1 - r.x0)))
            elif abs(sx0 - r.x1) < 4.5:
                out.append((st, "R", abs(sx0 - r.x1)))
        # One stub is sometimes drawn as two touching segments; both butt the edge within
        # tolerance. Keep the INNER one: its free end is where the wire label starts
        # (measuring from the outer end loses labels, and catches notes 8 pt away).
        out.sort(key=lambda c: (c[1], c[0][2], c[2]))
        kept = []
        for c in out:
            twin = next((k for k in kept if k[1] == c[1] and abs(k[0][2] - c[0][2]) < 0.25
                         and k[0][0] - 0.3 <= c[0][1] and c[0][0] <= k[0][1] + 0.3), None)
            if twin is None:
                kept.append([c[0], c[1], c[2], c[0][0] if c[1] == "L" else c[0][1]])
            else:                # the label starts at the INNERMOST free end of the pair
                twin[3] = max(twin[3], c[0][0]) if c[1] == "L" else min(twin[3], c[0][1])
        return [tuple(k) for k in kept]

    live = [b for b in blocks if b["rect"] is not None and not b["name"].startswith("_COMMENT")]
    claims = {id(b): _claims(b["rect"]) for b in live}
    # a stub two blocks claim on the SAME side (a small block butted against a big frame):
    # the smallest block whose own rectangle holds the stub's row owns it
    owners = {}
    for b in live:
        for st, side, _, _ in claims[id(b)]:
            owners.setdefault((st, side), []).append(b)

    def _owns(b, st, side):
        cand = owners[(st, side)]
        if len(cand) == 1:
            return True
        inner = [x for x in cand if x["rect"].y0 - 0.5 <= st[2] <= x["rect"].y1 + 0.5]
        if not inner:
            return True
        return b is min(inner, key=lambda x: x["rect"].width * x["rect"].height)

    rows = []
    for b in live:
        r = b["rect"]
        inside = [s for s in body
                  if r.x0 - 1 <= s["x0"] and s["x1"] <= r.x1 + 1
                  and r.y0 - 1 <= s["cy"] <= r.y1 + 1
                  and s["color"] in (0x000000, 0x191970) and s["font"].endswith("F1")]
        for (sx0, sx1, sy), side, _, end in claims[id(b)]:
            if not _owns(b, (sx0, sx1, sy), side):
                continue
            pin = None
            for s in [s for s in inside if abs(s["cy"] - sy) <= ROW_TOL]:
                dl, dr = s["x0"] - r.x0, r.x1 - s["x1"]
                if side == "L" and dl <= PIN_LBL_OFF and dr - dl > 3.0:
                    pin = pin or s["t"]
                elif side == "R" and dr <= PIN_LBL_OFF and dl - dr > 3.0:
                    pin = pin or s["t"]
            tip = end                                   # the stub's (innermost) free end
            if side == "L":
                out = [s for s in free if s["x1"] <= tip + 0.8 and abs(s["cy"] - sy) <= ROW_TOL]
                out.sort(key=lambda s: tip - s["x1"])
            else:
                out = [s for s in free if s["x0"] >= tip - 0.8 and abs(s["cy"] - sy) <= ROW_TOL]
                out.sort(key=lambda s: s["x0"] - tip)
            lab = None
            for s in out:
                if s["sz"] < 3.0:                      # compressed secondary ref
                    continue
                gap = (tip - s["x1"]) if side == "L" else (s["x0"] - tip)
                if gap > LBL_GAP:                      # no label on this stub
                    break
                lab = s
                break
            kind = "none" if lab is None else classify_wire(lab["t"], lab["color"], bnames)
            rows.append({
                "device": hdr["device"], "sw_path": hdr["path"], "sheet": hdr["sheet"],
                "page": pno + 1, "block": b["name"], "exec": b["exec"],
                "pin": pin, "side": side, "direction": "I" if side == "L" else "O",
                "wire_kind": kind, "wire_text": lab["t"] if lab else None,
                "cell": grid_cell(tip, sy), "y": round(sy, 1),
            })
    rows.sort(key=lambda d: (d["block"], d["side"] == "R", d["y"]))
    return rows


# ---------------------------------------------------------------------- gate
def _parse_stamp(s):
    """The print's '3/5/2025 08:51:08' and the index's '2025-03-05T00:51:08'
    -> comparable datetime, or None."""
    import datetime
    s = (s or "").strip()
    for f in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S %p"):
        try:
            return datetime.datetime.strptime(s, f)
        except ValueError:
            pass
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None


def gate(path: Path, expect_ctrl: str, idx_conn, sample=8):
    """Decide whether one <CTRL>_P.pdf may be ingested at all.

    Order matters: a file that is not readable cannot be identified, and a file
    that belongs to another device must be refused before its revision is even
    compared. Only gate_status 'pass' may be ingested.
    """
    import fitz
    out = {"name": path.name, "ctrl": expect_ctrl, "size_bytes": path.stat().st_size,
           "sha256": sha256_of(path), "page_count": 0, "device_name": "",
           "print_major": "", "print_minor": "", "tz_offset_min": None,
           "tool_version": "", "gate_status": "unreadable", "gate_note": ""}

    with open(path, "rb") as f:
        f.seek(max(0, out["size_bytes"] - 4096))
        tail = f.read()
    if b"%%EOF" not in tail or b"startxref" not in tail:
        out["gate_status"] = "truncated"
        out["gate_note"] = "no %%EOF / startxref: the print job never wrote the file tail"
        return out
    try:
        doc = fitz.open(str(path))
    except Exception as e:                                  # noqa: BLE001
        out["gate_note"] = "cannot open: %s" % e
        return out
    out["page_count"] = doc.page_count
    if not doc.page_count or getattr(doc, "is_repaired", False):
        out["gate_status"] = "truncated"
        out["gate_note"] = "pages=%d repaired=%s" % (doc.page_count, getattr(doc, "is_repaired", None))
        doc.close()
        return out

    # identity comes from the Device Name cell on a spread of pages, never the file name
    step = max(1, doc.page_count // max(1, sample))
    devs, vers, stamps = {}, set(), []
    for pno in range(0, doc.page_count, step):
        pg = doc[pno]
        d = (read_header(pg)["device"] or "").strip()
        if d:
            devs[d] = devs.get(d, 0) + 1
        v = tool_version(pg)
        if v:
            vers.add(v)
        if len(stamps) < 3:
            a, b = revision_stamps(pg)
            if a and b:
                stamps.append((a, b))
    doc.close()
    out["device_name"] = max(devs, key=devs.get) if devs else ""
    out["tool_version"] = sorted(vers)[0] if vers else ""
    if stamps:
        out["print_major"], out["print_minor"] = stamps[0]

    if not devs:
        out["gate_note"] = "no Device Name cell on any sampled page"
        return out
    if len(devs) > 1:
        out["gate_status"] = "mixed_device"
        out["gate_note"] = "Device Name differs across pages: " + ", ".join(
            "%s x%d" % kv for kv in sorted(devs.items(), key=lambda t: -t[1]))
        return out
    if out["device_name"] != expect_ctrl:
        out["gate_status"] = "wrong_device"
        out["gate_note"] = ("named for %s but every sampled page says Device Name = %s"
                            % (expect_ctrl, out["device_name"]))
        return out

    row = idx_conn.execute("SELECT major_rev, minor_rev FROM controller WHERE name=?",
                           (expect_ctrl,)).fetchone()
    if row is None:
        out["gate_note"] = "controller %s is not in the index" % expect_ctrl
        return out
    off, note = revision_offset(out["print_major"], out["print_minor"], row[0], row[1])
    if off is None:
        out["gate_status"] = "revision_mismatch"
        out["gate_note"] = note
        return out
    out["tz_offset_min"] = off

    want = dbm.get_meta(idx_conn, "toolbox_version")
    if want and out["tool_version"] and out["tool_version"] != want:
        out["gate_status"] = "tool_version_mismatch"
        out["gate_note"] = "工具版本不符: printed %s, indexed %s" % (out["tool_version"], want)
        return out

    out["gate_status"] = "pass"
    return out


def revision_offset(print_major, print_minor, idx_major, idx_minor):
    """-> (offset in minutes, None) when the printed build stamps are the indexed build's, else (None, reason).
    The print stamps local time and the index keeps the build's own stamp, so the two must differ by ONE constant
    whole-quarter-hour offset. Anything else means the drawing is of a different build than the indexed checkout."""
    pmaj, pmin = _parse_stamp(print_major), _parse_stamp(print_minor)
    imaj, imin = _parse_stamp(idx_major), _parse_stamp(idx_minor)
    if not all((pmaj, pmin, imaj, imin)):
        return None, "cannot compare revisions (printed %r/%r, indexed %r/%r)" % (print_major, print_minor, idx_major, idx_minor)
    dmaj = (pmaj - imaj).total_seconds()
    dmin = (pmin - imin).total_seconds()
    if abs(dmaj - dmin) > 60 or dmaj % 900 != 0 or not (-12 * 3600 <= dmaj <= 14 * 3600):
        return None, ("printed build %s / %s vs indexed %s / %s (offset %.0f s / %.0f s)"
                      % (print_major, print_minor, idx_major, idx_minor, dmaj, dmin))
    return int(dmaj // 60), None


def gate_all(idx_conn, pdf_dir: Path = None, ctrls=None):
    """Gate every <CTRL>_P.pdf that exists. -> list of gate dicts, plus the
    controllers the index knows that have no print at all."""
    pdf_dir = pdf_dir or pei_dir()
    if not pdf_dir.exists():
        raise SystemExit(f"printed reports not found: {pdf_dir} "
                         f"(set DCDAS_PEI or \"pei_dir\" in {dbm.config_path()})")
    known = [r[0] for r in idx_conn.execute("SELECT name FROM controller ORDER BY name")]
    rows, missing = [], []
    for c in known:
        if ctrls and c not in ctrls:
            continue
        p = pdf_dir / ("%s_P.pdf" % c)
        if p.exists():
            rows.append(gate(p, c, idx_conn))
        else:
            missing.append(c)
    return rows, missing


def resolve_sheet_path(printed, parents, paths, progs=None):
    """Turn a sheet's printed Software Path into an index container path.

    -> (prefix, method, owner)
       'exact'     the path addresses a container the index knows; blocks drawn on
                   the sheet are <prefix>/<label>
       'repair'    the title block CLIPS its value at ~76-86 characters with no
                   ellipsis, so the printed path is a prefix of exactly one real
                   container path (ambiguous -> refused, never guessed)
       'internals' the path addresses a BLOCK, not a container: the sheet draws the
                   inside of that (opaque) macro instance, which the index cannot
                   represent at all.  owner = that block's path.
       'encrypted' the program is in the index but has no blocks at all (its XML is
                   encrypted), so the drawing is the ONLY source for this logic
       'none'      not resolvable; the sheet contributes no block paths
    """
    p = (printed or "").replace(".", "/")
    if not p:
        return None, "none", None
    if p in parents:
        return p, "exact", None
    if p in paths:
        return p, "internals", p
    # the program exists but the index has no logic for it at all (its XML is
    # encrypted): checked BEFORE the clipped-cell guard below, or a short path
    # short-circuits to 'none' and the drawing's only-source status is lost
    prog = (printed or "").split(".", 1)[0]
    encrypted = bool(progs) and prog in progs and not progs[prog]
    if len(p) < 60:                  # too short to be a clipped cell; never guess
        return None, ("encrypted" if encrypted else "none"), None
    # the cell clips INSIDE the last segment ('..._MAX_DEVICE_OUTPUTS_V' for
    # '..._MAX_DEVICE_OUTPUTS_V2'), so a candidate completes the text without
    # crossing a '/'.  Ambiguous -> refused.
    cand = {x for x in parents if x.startswith(p) and "/" not in x[len(p):]}
    if len(cand) == 1:
        return cand.pop(), "repair", None
    own = {x for x in paths if x.startswith(p) and "/" not in x[len(p):]}
    if len(own) == 1:
        o = own.pop()
        return o, "internals", o
    return None, ("encrypted" if encrypted else "none"), None


# --------------------------------------------------------------------- ingest
def scan(idx_conn, pconn, ctrls=None, pdf_dir: Path = None, pages=None, log=print):
    """Parse every readable <CTRL>_P.pdf into the print corpus. Writes nothing to
    the index. Re-running replaces a file's rows (keyed on the file name)."""
    import fitz
    pdf_dir = pdf_dir or pei_dir()
    if not pdf_dir.exists():
        raise SystemExit(f"printed reports not found: {pdf_dir} "
                         f"(set DCDAS_PEI or \"pei_dir\" in {dbm.config_path()})")
    known = [r[0] for r in idx_conn.execute("SELECT name FROM controller ORDER BY name")]
    files = []
    for c in known:
        if ctrls and c not in ctrls:
            continue
        p = pdf_dir / ("%s_P.pdf" % c)
        if p.exists():
            files.append((c, p))
    if not files:
        raise SystemExit("no <CTRL>_P.pdf found in %s" % pdf_dir)

    vars_by_ctrl, blocks_by_ctrl = {}, {}
    summary = {"pdfs": [], "sheets": 0, "pins": 0, "refused": []}

    for ctrl, path in files:
        g = gate(path, ctrl, idx_conn)
        pconn.execute("DELETE FROM print_pin WHERE sheet_id IN (SELECT s.id FROM print_sheet s "
                      "JOIN print_pdf p ON p.id=s.pdf_id WHERE p.name=?)", (path.name,))
        pconn.execute("DELETE FROM print_sheet WHERE pdf_id IN (SELECT id FROM print_pdf WHERE name=?)",
                      (path.name,))
        pconn.execute("DELETE FROM print_pdf WHERE name=?", (path.name,))
        cur = pconn.execute(
            "INSERT INTO print_pdf(name,ctrl,sha256,size_bytes,page_count,device_name,print_major,"
            "print_minor,tz_offset_min,tool_version,gate_status,gate_note,scanned_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))",
            (g["name"], ctrl, g["sha256"], g["size_bytes"], g["page_count"], g["device_name"],
             g["print_major"], g["print_minor"], g["tz_offset_min"], g["tool_version"],
             g["gate_status"], g["gate_note"]))
        pdf_id = cur.lastrowid
        summary["pdfs"].append({"name": g["name"], "ctrl": ctrl, "gate": g["gate_status"],
                                "note": g["gate_note"], "pages": g["page_count"]})
        if g["gate_status"] != "pass":
            log("  REFUSED %-14s %-22s %s" % (g["name"], g["gate_status"], g["gate_note"]))
            summary["refused"].append(g["name"])
            pconn.commit()
            continue

        if ctrl not in vars_by_ctrl:
            vars_by_ctrl[ctrl] = {r[0] for r in idx_conn.execute(
                "SELECT name FROM variable WHERE ctrl=?", (ctrl,))}
            paths = {r[0] for r in idx_conn.execute("SELECT path FROM block WHERE ctrl=?", (ctrl,))}
            blocks_by_ctrl[ctrl] = (paths, {p.rsplit("/", 1)[0] for p in paths if "/" in p},
                                    {r[0]: r[1] for r in idx_conn.execute(
                                        "SELECT name, block_count FROM program WHERE ctrl=?", (ctrl,))})
        vnames = vars_by_ctrl[ctrl]
        bpaths, bparents, bprogs = blocks_by_ctrl[ctrl]
        bnames_ctrl = {p.rsplit("/", 1)[-1] for p in bpaths}
        kids_of = {}
        for c in bparents:
            if "/" in c:
                kids_of.setdefault(c.rsplit("/", 1)[0], []).append(c)

        def children(p):
            return kids_of.get(p, [])

        doc = fitz.open(str(path))
        rng = range(doc.page_count) if not pages else [p - 1 for p in pages]
        n_sheet = n_tasks = n_other = n_pin = 0
        for pno in rng:
            pg = doc[pno]
            kind = page_kind(pg)
            h = read_header(pg)
            prefix, method, owner = (resolve_sheet_path(h["path"], bparents, bpaths, bprogs)
                                     if kind == "sheet" else (None, "none", None))
            cur = pconn.execute(
                "INSERT INTO print_sheet(pdf_id,page,ctrl,sw_path,program,task,block_prefix,sheet_no,"
                "cont_on,mod_rev,owner_path,kind,map_method) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pdf_id, pno + 1, ctrl, h["path"] or None, h["program"] or None, h["task"] or None,
                 prefix, h["sheet"] or None, h["cont_on"] or None, h["mod_rev"] or None, owner,
                 kind, method))
            sid = cur.lastrowid
            if kind != "sheet":
                n_tasks += (kind == "tasks")
                n_other += (kind == "other")
                continue
            n_sheet += 1
            parsed = parse_page(doc, pno, fitz)
            if prefix and method in ("exact", "repair") and parsed:
                # The title cell clips right before '.Action_Logic_<step>' on SFC action
                # sheets, so the printed path names the step, one level above the blocks.
                # Descend only into the ONE child container that holds the drawn labels.
                labels = {r["block"] for r in parsed}
                if not any("%s/%s" % (prefix, l) in bpaths for l in labels):
                    kids = [k for k in children(prefix)
                            if any("%s/%s" % (k, l) in bpaths for l in labels)]
                    if len(kids) == 1:
                        prefix = kids[0]
                        pconn.execute("UPDATE print_sheet SET block_prefix=? WHERE id=?", (prefix, sid))
            rows = []
            for r in parsed:
                # 'internals' sheets draw the inside of an opaque macro: those blocks
                # exist in no index row at all, so they keep block_path NULL and are
                # attributed to the owner instance through the sheet
                bp = ("%s/%s" % (prefix, r["block"])) if (prefix and method != "internals") else None
                if bp and bp not in bpaths:
                    bp = None            # drawn, but the index has no such block
                vn = fld = None
                t, kd = r["wire_text"], r["wire_kind"]
                if t and r["side"] == "R" and "." in t and t.split(".", 1)[0] == r["block"]:
                    # 'OR_8.OUT' on OR_8's own OUTPUT stub: the label of the facing
                    # block's input, caught by the window. (On an INPUT stub the same
                    # text is a real feedback wire - a seal-in rung, a PID's TV <- CVO.)
                    t, kd = None, "none"
                if t and kd != "default" and t in vnames:
                    # an exact variable name wins over the look of the text: 'S1.L4' is
                    # an EGD copy, not a sheet ref; 'k_A/B' is a name, not an equation
                    vn, kd = t, "var"
                elif kd == "var" and t:
                    if "." in t:
                        base, _, f = t.rpartition(".")
                        if base in vnames:
                            # 'X.PIN' where a block X exists is a block-to-block link that
                            # happens to share a variable's name, never a field of it
                            if (prefix and "%s/%s" % (prefix, base) in bpaths) or base in bnames_ctrl:
                                kd = "link"
                            else:
                                vn, fld, kd = base, f, "field"
                    elif t[0].isdigit() and t[1:] in vnames:
                        # a stray glyph printed over the label's first character
                        # ('0LpEcon...'): the rest is an exact name. wire_text keeps the
                        # raw text, so the evidence shown is still what was printed.
                        vn = t[1:]
                rows.append((sid, r["cell"], r["block"], bp, r["exec"], r["pin"], r["side"],
                             r["direction"], kd, t, vn, fld))
            pconn.executemany(
                "INSERT INTO print_pin(sheet_id,cell,block_label,block_path,exec_order,pin_name,side,"
                "direction,wire_kind,wire_text,var_name,wire_field) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                rows)
            n_pin += len(rows)
        doc.close()
        pconn.execute("UPDATE print_pdf SET n_sheets=?,n_tasks=?,n_other=? WHERE id=?",
                      (n_sheet, n_tasks, n_other, pdf_id))
        pconn.commit()
        summary["sheets"] += n_sheet
        summary["pins"] += n_pin
        log("  %-14s sheets=%-5d tasks=%-4d other=%-3d pins=%d"
            % (path.name, n_sheet, n_tasks, n_other, n_pin))

    pconn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('print_schema',?)", (PRINT_SCHEMA,))
    pconn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('scanned_at',datetime('now','localtime'))")
    pconn.commit()
    return summary
