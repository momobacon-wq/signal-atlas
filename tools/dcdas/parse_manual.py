# -*- coding: utf-8 -*-
"""vendor block-library manuals (PDF) -> tools/pin_dir_table.csv  (block_type, pin_name, direction, source_doc, page).

The manuals are run through `pdftotext -layout` (cached in %TEMP%/dcdas_manual/<doc_id>.txt, never in the repo) and
scanned with a small line state machine:

* section  = a title line `Some Words (BLOCK_TYPE)` (optionally numbered `12 Some Words (TYPE)`, optionally with the
  `(TYPE)` wrapped alone onto the next line) that is either listed in the document's own table of contents
  followed by a `Block Category:` line.  The 2008 GEI-100679D revision has no such markers: there the title must be in
  the document's own table of contents (`Analog Input (AI) ....`, or bare `STARTER ....`, then a bare column-0 name
  starts the section).  Figure captions `TYPE Block` are only reported when they disagree with the section (they do
  not switch it: ARRAY_RUNG shows a `RUNG Block`).
* direction = the last `Inputs` / `Input` / `Outputs` / `Output` / `State(s)` (any case, optional `(Cont.)` or
  `(continued)`) sub-heading -> I / O / S.  `Parameters` tables (ANALOG_ALARM H_SP.., DCS CTL/MODE_OPT/FL_*_T) are
  real XML pins read by the block, so they are emitted as I (counted separately in the log); other sub-headings
  (`Attributes`, `Global Variables`, ...) reset the direction so their tables are ignored.
* table     = opens at a header line starting with `Name` / `Pin Name` while a direction is pending; page-break
  boilerplate (`Public Information`, `Instruction Guide  GEI-...  203`, `12  GEI-100682AO  ...`) and repeated headers
  keep it open; a blank-line run followed by a non-row line, or a non-indented non-row line, closes it.
* row       = `NAME  <2+ spaces> ...` at column 0..3 (name may carry `[ ]`, footnote daggers, or a ` 1` footnote
  digit) relative to the header's indentation.  Deeper-indented lines are wrapped descriptions; `{Device}` rows,
  check/dash legends and dagger footnotes are skipped; a `{Device}` output row is the DCS main output, named `OUT`
  in the XML.  A `↓` row expands `IN1 ↓ IN16` / `A ↓ H` ranges, and an `IN1 ↓ INn` template expands to the
  section's "up to N inputs" (default 32).
* derived   = for block types used in the checkout: `X_STATUS` variants and LOGIC_BUILDER_SC documented only by
  reference (DERIVED_EXTRA), and `X_Vn` version variants whose own tables are summaries (pins missing there are
  filled from `X_V(n-1)` .. `X`); source_doc is written as `<doc>~<BASE>` so they can be told apart or dropped.

Two table families are handled by the same rules: Standard Block Library (`Name  Data Type  Description`,
GEI-100682 / S0014) and DCS Block Library (`Name  Description  Data Type  Initial Value  Visibility  Interface Type`,
GEI-100679 / S0010).

Precedence: the PDF list order (default: S0014-B > S0014-0 > GEI-100679 > S0010-0 > backups); the first document that
defines (block_type, pin_name) wins and its source_doc/page are kept.  Conflicting directions are logged.

Public API
  run(repo_dir, pdfs, log)        -> writes tools/pin_dir_table.csv (+ empty tools/pin_dir_overrides.csv if absent)
                                     and prints per-manual statistics and the coverage of the checkout's block types.
  load_into_db(conn, repo_dir)    -> DELETE + INSERT pin_dir_table / pin_dir_override from the two CSV files.
  parse_text(text, doc_id)        -> (rows, stats) for one extracted manual (unit-testable without PDFs).
"""
import csv
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

def default_pdfs():
    """Manual PDFs come from the local config (%LOCALAPPDATA%\\dcdas\\config.json "manual_pdfs", precedence order:
    first wins) or env DCDAS_MANUAL_PDFS (os.pathsep-separated). Nothing site-specific is kept in the repo."""
    from .db import config
    env = os.environ.get("DCDAS_MANUAL_PDFS")
    if env:
        return [Path(x) for x in env.split(os.pathsep) if x.strip()]
    return [Path(x) for x in config().get("manual_pdfs", [])]


TABLE_CSV = "pin_dir_table.csv"
OVERRIDE_CSV = "pin_dir_overrides.csv"
TABLE_HEADER = ["block_type", "pin_name", "direction", "source_doc", "page"]
OVERRIDE_HEADER = ["block_type", "pin_name", "direction", "note"]

PIN_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9][A-Z0-9_]*$")     # canonical pin name (1XMTR_P is real)
PIN_LOOSE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")      # accepted only when the manual clearly uses lowercase
_TEMPLATE_N_RE = re.compile(r"^[A-Z][A-Z0-9_]*n$")         # INn / OUTn / SELn  (the "n-th" template row)

# --- line classifiers ------------------------------------------------------------------------------------------------
_TITLE_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?(?:[A-Z][^()]*?)?\(([A-Z_][A-Za-z0-9_]*)\)\s*$")
_BARE_TITLE_RE = re.compile(r"^([A-Z][A-Z0-9_]+)\s*$")                       # old GEI-100679D: `STARTER` alone
_TOC_PAREN_RE = re.compile(r"\(([A-Z_][A-Za-z0-9_]*)\)\s*\.{3,}\s*\d*\s*$", re.M)
_TOC_BARE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*\.{3,}\s*\d*\s*$", re.M)
_CAPTION_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*) Block\s*$")
_SUBHEAD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z /]*?)(?:\s*\((?:Cont\.?|continued)\))?\s*$")
_DIR_WORDS = {"input": "I", "inputs": "I", "output": "O", "outputs": "O", "input pins": "I", "output pins": "O",
              "state": "S", "states": "S", "parameter": "P", "parameters": "P"}   # P -> emitted as I, counted
_OTHER_SUBHEADS = {"attribute", "attributes", "constant", "constants", "global variable", "global variables",
                   "global pins", "internal", "internals", "enumerations", "truth table", "block mode visible pins"}
_HEADER_RE = re.compile(r"^(\s{0,12})(?:Pin Name|Input Pin|Output Pin|Name)\s{2,}\S")
_ROW_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_]*)(\[\s*\])?[\u2020\u2021*]*(?:\s\d)?\s{2,}(\S.*)$")
_NAME_ONLY_RE = re.compile(r"^([A-Z0-9][A-Z0-9_]*)(\[\s*\])?[\u2020\u2021*]*\s*$")
_DEVICE_ROW_RE = re.compile(r"^\s{0,3}\{Device\}\s{2,}")                    # DCS main output pin, named OUT in XML
_FOOTDIGIT_RE = re.compile(r"^(.+_[ST])[12]$")                                # HW_TAMPER_S1 = HW_TAMPER_S + footnote 1
_BAD_TITLECASE_RE = re.compile(r"^(In|Out)\d*$")                              # revAG typos for IN / OUT16
_ARROW_RE = re.compile(r"^\s*[\u2193\u2191]")
_FOOT_RE = re.compile(r"^\s{0,3}[{\u2713\u2014\u2020\u2021*\u2022]")            # {Device} / check / dash / dagger
_DTYPE_RE = re.compile(r"^(BOOL|REAL|LREAL|INT|UINT|DINT|UDINT|SINT|USINT|LINT|ULINT|BYTE|WORD|DWORD|STRING|ANY\w*|"
                       r"Constant\b|Immediate\b|Variable\b)")
_UPTO_RE = re.compile(r"up to (\d+)\s+(?:inputs?|outputs?|operands?|signals?|values?|selects?|variables?)", re.I)
_BOILER_RE = re.compile(
    r"^\s*(Public Information\s*$|Instruction Guide\s{2,}|\d+\s{2,}GEI-\d+|GEI-\d+[A-Z]*\s+Mark\*?\s*VIe|"
    r"Mark\*? VIe (Control|Controller)\b.*\d+\s*$)")
_RANGE_RE = re.compile(r"^([A-Z][A-Z0-9_]*?)(\d+)$")
_TEMPLATE_MAX = 32                                                            # `IN1 (arrow) INn` when no "up to N"


def doc_id_of(pdf: Path) -> str:
    """Short document id from the file name: S0014-B, S0010-0, GEI-100679, STD-revAG, DCS-revB."""
    name = pdf.name
    m = re.search(r"\b(S\d{4}-[0-9A-Z])\b", name)
    if m:
        return m.group(1)
    m = re.search(r"\b(GEI-\d{6})", name)
    if m:
        return m.group(1)
    m = re.search(r"rev([A-Z0-9]+)", name)
    if m:
        return ("DCS-rev" if "DCS" in name.upper() else "STD-rev") + m.group(1)
    return re.sub(r"[^A-Za-z0-9]+", "_", pdf.stem)[:20]


def cache_dir() -> Path:
    return Path(os.environ.get("TEMP") or os.environ.get("TMP") or Path.home()) / "dcdas_manual"


def pdftotext_exe() -> Optional[str]:
    return os.environ.get("DCDAS_PDFTOTEXT") or shutil.which("pdftotext")


def extract_text(pdf: Path, log=print) -> Tuple[Path, float]:
    """pdftotext -layout into the cache; re-run when the cache is missing or older than the PDF. Returns (txt, secs)."""
    cd = cache_dir()
    cd.mkdir(parents=True, exist_ok=True)
    txt = cd / (doc_id_of(pdf) + ".txt")
    force = os.environ.get("DCDAS_MANUAL_REFRESH") == "1"
    if txt.exists() and not force and txt.stat().st_mtime >= pdf.stat().st_mtime and txt.stat().st_size > 0:
        return txt, 0.0
    exe = pdftotext_exe()
    if not exe:
        raise SystemExit("pdftotext not found on PATH (set DCDAS_PDFTOTEXT)")
    t0 = time.time()
    subprocess.run([exe, "-layout", "-enc", "UTF-8", str(pdf), str(txt)], check=True)
    return txt, time.time() - t0


# --- parser ----------------------------------------------------------------------------------------------------------
def _expand_range(a: str, b: str) -> List[str]:
    """IN1..IN16 -> IN2..IN15 ; A..H -> B..G ; OUTA..OUTF -> OUTB..OUTE ; otherwise []."""
    ma, mb = _RANGE_RE.match(a), _RANGE_RE.match(b)
    if ma and mb and ma.group(1) == mb.group(1):
        lo, hi = int(ma.group(2)), int(mb.group(2))
        if 0 <= lo < hi <= 256:
            return [f"{ma.group(1)}{i}" for i in range(lo + 1, hi)]
    if len(a) == len(b) and a[:-1] == b[:-1] and a[-1].isalpha() and b[-1].isalpha() and a[-1] < b[-1]:
        return [a[:-1] + chr(c) for c in range(ord(a[-1]) + 1, ord(b[-1]))]       # A..H, OUTA..OUTF (hex)
    return []


def parse_text(text: str, doc_id: str, log=print) -> Tuple[List[tuple], dict]:
    """Return ([(block_type, pin_name, direction, doc_id, page), ...], stats) for one extracted manual."""
    has_cat = "Block Category" in text            # newer manuals mark every block section; then the TOC is not needed
    toc_paren = set() if has_cat else set(_TOC_PAREN_RE.findall(text))
    toc_bare = set() if has_cat else set(_TOC_BARE_RE.findall(text)) - toc_paren
    lines: List[Tuple[int, str]] = []
    for pno, chunk in enumerate(text.split("\f"), start=1):
        for l in chunk.split("\n"):
            lines.append((pno, l.rstrip("\r")))
    n = len(lines)

    def next_nonblank(i: int) -> str:
        for j in range(i + 1, min(n, i + 4)):
            s = lines[j][1].strip()
            if s:
                return s
        return ""

    rows: List[tuple] = []
    seen = set()
    sections: "OrderedDict[str, dict]" = OrderedDict()
    stats = {"doc": doc_id, "toc": len(toc_paren) + len(toc_bare), "sections": 0, "rows": 0, "tables": 0,
             "no_table": [], "caption_mismatch": [], "lowercase": Counter(), "skipped_names": Counter(),
             "range_expanded": 0, "template_expanded": 0, "param_pins": 0, "state_pins": 0,
             "other_subheads": Counter()}

    block = None            # current block type
    pending = None          # direction announced by the last sub-heading
    table_dir = None        # direction of the open table (None = no table open)
    hdr_indent = 0          # indentation of the open table's header line
    blank_run = 0
    last_name = None        # last accepted pin in the open table (for arrow ranges)
    arrow = False           # an arrow row was seen since last_name
    sec_max_n = None        # "up to N inputs" phrase of the current section
    trace_block = os.environ.get("DCDAS_MANUAL_TRACE")   # debug: print the state machine for one block type

    def trace(ev, page, i, line):
        if trace_block and block == trace_block:
            log(f"    [{doc_id} p{page} L{i+1}] {ev:9s} dir={table_dir or '-'} pend={pending or '-'} "
                f"blank={blank_run} | {line[:70].rstrip()}")

    def add(name: str, direction: str, page: int):
        if direction == "P":                                   # parameter pins are read by the block -> input
            stats["param_pins"] += 1
            direction = "I"
        key = (block, name)
        if key in seen:
            return
        seen.add(key)
        rows.append((block, name, direction, doc_id, page))
        sections[block]["rows"] += 1
        stats["rows"] += 1
        if direction == "S":
            stats["state_pins"] += 1

    def close_table():
        nonlocal table_dir, last_name, arrow, pending
        table_dir, last_name, arrow, pending = None, None, False, None

    def accept_name(raw: str, rest: str) -> Optional[str]:
        if PIN_RE.match(raw):
            return raw
        if PIN_LOOSE_RE.match(raw) and not _TEMPLATE_N_RE.match(raw) and raw[0].isupper() and not raw.islower() \
                and not _BAD_TITLECASE_RE.match(raw):
            if raw[1:].islower():
                if _DTYPE_RE.match(rest):                  # `Unshelve  BOOL  ...` : title-case pin with a data type
                    stats["lowercase"][raw] += 1
                    return raw
            else:                                          # NVal
                stats["lowercase"][raw] += 1
                return raw
        stats["skipped_names"][raw] += 1
        return None

    for i, (page, line) in enumerate(lines):
        s = line.strip()
        if not s:
            blank_run += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        # --- section title ---------------------------------------------------------------------------------------
        m = _TITLE_RE.match(line)
        if m and (m.group(1) in toc_paren or next_nonblank(i).startswith("Block Category")):
            close_table()
            block = m.group(1)
            sections.setdefault(block, {"page": page, "rows": 0, "tables": 0})
            blank_run, sec_max_n = 0, None
            continue
        m = _BARE_TITLE_RE.match(line)
        if m and indent == 0 and m.group(1) in toc_bare:
            close_table()
            block = m.group(1)
            sections.setdefault(block, {"page": page, "rows": 0, "tables": 0})
            blank_run, sec_max_n = 0, None
            continue
        m = _CAPTION_RE.match(line)
        if m:
            if block and m.group(1) != block:
                stats["caption_mismatch"].append((block, m.group(1), page))
            if table_dir is not None:
                close_table()
            blank_run = 0
            continue
        if block is None:
            blank_run = 0
            continue
        # --- boilerplate at page breaks: transparent -----------------------------------------------------------
        if _BOILER_RE.match(line):
            continue
        # --- sub-headings ----------------------------------------------------------------------------------------
        m = _SUBHEAD_RE.match(line)
        if m and len(s) < 40:
            word = m.group(1).strip().lower()
            if word in _DIR_WORDS:
                if table_dir is not None:
                    close_table()
                pending = _DIR_WORDS[word]
                blank_run = 0
                trace("subhead", page, i, line)
                continue
            if word in _OTHER_SUBHEADS:
                stats["other_subheads"][word] += 1
                close_table()
                blank_run = 0
                continue
        # --- table header ----------------------------------------------------------------------------------------
        m = _HEADER_RE.match(line)
        if m:
            if table_dir is not None:
                pass                                   # header repeated after a page break
            elif pending:
                table_dir = pending
                hdr_indent = len(m.group(1))
                stats["tables"] += 1
                sections[block]["tables"] += 1
            blank_run = 0
            last_name, arrow = None, False
            trace("header", page, i, line)
            continue
        if table_dir is None:
            m = _UPTO_RE.search(line)
            if m:
                sec_max_n = max(sec_max_n or 0, int(m.group(1)))
            blank_run = 0
            continue
        # --- inside a table --------------------------------------------------------------------------------------
        if _ARROW_RE.match(line):
            arrow = True
            blank_run = 0
            continue
        if _DEVICE_ROW_RE.match(line) and table_dir == "O":
            add("OUT", "O", page)
            last_name, arrow, blank_run = None, False, 0
            continue
        m = None
        if indent <= hdr_indent + 2:
            m = _ROW_RE.match(line.lstrip(" ")) or _NAME_ONLY_RE.match(line.lstrip(" "))
        if m:
            raw = m.group(1)
            rest = m.group(3) if m.lastindex and m.lastindex >= 3 else ""
            blank_run = 0
            if raw.endswith("_") and i + 1 < n:                # name wrapped onto the next line (LAST_ / CURTIME)
                nxt = lines[i + 1][1].strip().split(" ")[0]
                if PIN_RE.match(nxt):
                    raw += nxt
            mf = _FOOTDIGIT_RE.match(raw)
            if mf and (block, mf.group(1)[:-1] + "R") in seen:
                raw = mf.group(1)
            if raw.lower() in ("name", "note", "where", "block", "description"):
                continue
            if _TEMPLATE_N_RE.match(raw):                  # `INn` after `IN1 (arrow)` -> IN2..IN<max>
                if arrow and last_name:
                    mr = _RANGE_RE.match(last_name)
                    if mr and mr.group(1) == raw[:-1]:
                        hi = sec_max_n or _TEMPLATE_MAX
                        for k in range(int(mr.group(2)) + 1, hi + 1):
                            add(f"{mr.group(1)}{k}", table_dir, page)
                            stats["template_expanded"] += 1
                last_name, arrow = None, False
                continue
            name = accept_name(raw, rest)
            if name is None:
                continue
            if arrow and last_name:
                for x in _expand_range(last_name, name):
                    add(x, table_dir, page)
                    stats["range_expanded"] += 1
            add(name, table_dir, page)
            last_name, arrow = name, False
            trace("row", page, i, line)
            continue
        if _FOOT_RE.match(line):                           # {Device} row, legend, footnote
            blank_run = 0
            continue
        if blank_run == 0 and indent > hdr_indent + 2:
            continue                                       # wrapped description / header continuation
        if blank_run == 0 and re.search(r"\S\s{2,}\S", line):
            continue                                       # multi-column line whose first cell is not a pin
        # non-row line after a blank run (or a non-indented one): the table is over
        trace("CLOSE", page, i, line)
        close_table()
        blank_run = 0

    stats["sections"] = len(sections)
    stats["no_table"] = [(b, d["page"]) for b, d in sections.items() if d["rows"] == 0]
    stats["section_pages"] = {b: d["page"] for b, d in sections.items()}
    return rows, stats


def parse_pdf(pdf: Path, log=print) -> Tuple[List[tuple], dict]:
    txt, secs = extract_text(pdf, log)
    doc_id = doc_id_of(pdf)
    text = txt.read_text(encoding="utf-8", errors="replace")
    t0 = time.time()
    rows, stats = parse_text(text, doc_id, log)
    stats.update({"pdf": pdf.name, "txt": str(txt), "pages": text.count("\f"), "extract_s": secs,
                  "parse_s": time.time() - t0})
    return rows, stats


# --- merge / write ---------------------------------------------------------------------------------------------------
def merge(per_doc: List[Tuple[List[tuple], dict]], log=print) -> List[tuple]:
    """First document (precedence order) wins per (block_type, pin_name); log direction conflicts."""
    out: Dict[Tuple[str, str], tuple] = {}
    conflicts = []
    for rows, st in per_doc:
        for r in rows:
            key = (r[0], r[1])
            if key in out:
                if out[key][2] != r[2]:
                    conflicts.append((key, out[key][3], out[key][2], r[3], r[2]))
                continue
            out[key] = r
    if conflicts:
        log(f"  direction conflicts between manuals: {len(conflicts)} (winner kept)")
        for (bt, pn), d1, x1, d2, x2 in conflicts[:12]:
            log(f"    {bt}.{pn}: {d1}={x1} vs {d2}={x2}")
    return sorted(out.values())


# Blocks the manuals describe only by reference to another block.  `X_STATUS` = X with status operations enabled
# (S0014 "Status Monitoring (STATUS_MONITORING)", p. 250: same pins, values carry quality status).  LOGIC_BUILDER_SC =
# LOGIC_BUILDER + the State Change pins named in its text (S0014-B p. 147).  Derived rows get source_doc "<doc>~<BASE>".
DERIVED_EXTRA = {"LOGIC_BUILDER_SC": ("LOGIC_BUILDER", {"SCA_ENABLE": "I", "RESET": "I", "RESET_PB": "I",
                                                        "SCA": "O", "SCA_CUR": "O", "SCA_PRV": "O"})}


def _family_chain(t: str) -> List[str]:
    """M_O_V_V4 -> [M_O_V_V3, M_O_V_V2, M_O_V]; PID_MA_ENH_V3 -> [PID_MA_ENH_V2, PID_MA_ENH]; else []."""
    m = re.match(r"^(.*)_V(\d+)$", t)
    if not m:
        return []
    base, n = m.group(1), int(m.group(2))
    return [f"{base}_V{k}" for k in range(n - 1, 1, -1)] + [base]


def derive_rows(rows: List[tuple], wanted_types: Iterable[str], log=print) -> List[tuple]:
    """Rows for types in `wanted_types` (the checkout's block types) that the manuals document only by reference:
    * `X_STATUS` and LOGIC_BUILDER_SC (DERIVED_EXTRA): all pins copied from the base block;
    * `X_Vn` version variants: pins missing from the variant's own tables are filled from `X_V(n-1)` .. `X`
      (S0010-0 gives M_O_V_V2/V3/V4, S_O_V_V2/V3, STARTER_V2/V3, GRP_V2/V3 only summary tables; the full
      `Data Type / Initial Value` tables are in the family base).
    Derived rows carry source_doc `<doc>~<BASE>`."""
    have: Dict[str, List[tuple]] = {}
    for r in rows:
        have.setdefault(r[0], []).append(r)
    out = []
    for t in sorted(set(wanted_types)):
        base, extra, chain = None, {}, []
        if t in DERIVED_EXTRA:
            base, extra = DERIVED_EXTRA[t]
        elif t.endswith("_STATUS"):
            base = t[:-len("_STATUS")]
        else:
            chain = _family_chain(t)
        if base and t not in have and base in have:
            page = min(r[4] for r in have[base])
            for r in have[base]:
                out.append((t, r[1], r[2], f"{r[3]}~{base}", r[4]))
            for pin, d in extra.items():
                out.append((t, pin, d, f"{have[base][0][3]}~{base}", page))
            log(f"  derived {t:22s} <- {base} ({len(have[base])} + {len(extra)} pins)")
            continue
        if chain:
            mine = {r[1] for r in have.get(t, [])}
            added = Counter()
            for b in chain:
                for r in have.get(b, []):
                    if r[1] not in mine:
                        mine.add(r[1])
                        out.append((t, r[1], r[2], f"{r[3]}~{b}", r[4]))
                        added[b] += 1
            if added:
                log(f"  derived {t:22s} += " + ", ".join(f"{n} from {b}" for b, n in added.items())
                    + f" (own {len(have.get(t, []))})")
    return out


def _tools_dir(repo_dir: Path) -> Path:
    """Accept either the repo root or the tools/ folder (dcdas.py passes its own folder)."""
    repo_dir = Path(repo_dir)
    if (repo_dir / "dcdas.py").exists() or repo_dir.name == "tools":
        return repo_dir
    if (repo_dir / "tools" / "dcdas.py").exists():
        return repo_dir / "tools"
    return repo_dir


def write_table(rows: Iterable[tuple], path: Path) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(TABLE_HEADER)
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def ensure_overrides(path: Path) -> bool:
    if path.exists():
        return False
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(OVERRIDE_HEADER) + "\n")
        f.write("# manual corrections (direction I|O), loaded on every build, take precedence over pin_dir_table.csv; "
                "lines starting with # are ignored\n")
    return True


def read_csv(path: Path, header: List[str]) -> List[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rdr = csv.reader(f)
        first = True
        for rec in rdr:
            if not rec or not "".join(rec).strip() or rec[0].lstrip().startswith("#"):
                continue
            if first:
                first = False
                if [c.strip().lower() for c in rec[:len(header)]] == header:
                    continue
            d = {h: (rec[k].strip() if k < len(rec) else "") for k, h in enumerate(header)}
            out.append(d)
    return out


# --- checkout coverage -----------------------------------------------------------------------------------------------
_BT_RE = re.compile(rb'<Block\s[^>]*?\bBlockType="([^"]*)"')


def checkout_block_types(root: Path) -> Tuple[Counter, Dict[str, Counter]]:
    """Counter of BlockType on <Block> elements (not UserBlock/TopUserBlock) in <CTRL>/_*.xml, plus per controller."""
    total, per = Counter(), {}
    for d in sorted(Path(root).iterdir()):
        if not d.is_dir() or not (d / "Device.xml").exists():
            continue
        c = Counter()
        for f in glob.glob(str(d / "_*.xml")):
            with open(f, "rb") as fh:
                data = fh.read()
            for m in _BT_RE.finditer(data):
                c[m.group(1).decode("utf-8", "replace")] += 1
        per[d.name] = c
        total.update(c)
    return total, per


def coverage_report(rows: List[tuple], root: Optional[Path], log=print, top: int = 30) -> dict:
    try:
        from .db import src_root
    except ImportError:                                # run as a script
        src_root = None
    root = Path(root) if root else (src_root() if src_root else None)
    if not root or not root.exists():
        log(f"  checkout not found ({root}); coverage skipped")
        return {}
    t0 = time.time()
    used, per = checkout_block_types(root)
    covered_types = {r[0] for r in rows}
    cov = [bt for bt in used if bt in covered_types]
    inst_cov = sum(used[bt] for bt in cov)
    log(f"  checkout: {len(used)} distinct block types, {sum(used.values())} instances "
        f"({len(per)} controllers, {time.time()-t0:.1f}s)")
    log(f"  covered by manuals: {len(cov)} types / {inst_cov} instances "
        f"({100.0*len(cov)/max(1,len(used)):.0f}% of types, {100.0*inst_cov/max(1,sum(used.values())):.0f}% of instances)")
    unc = [(bt, c) for bt, c in used.most_common() if bt not in covered_types]
    log(f"  top {top} uncovered types by instance count (macros / user blocks expected):")
    for bt, c in unc[:top]:
        log(f"    {c:7d}  {bt}")
    return {"distinct": len(used), "instances": sum(used.values()), "covered_types": len(cov),
            "covered_instances": inst_cov, "uncovered": unc}


# --- entry points ----------------------------------------------------------------------------------------------------
def run(repo_dir, pdfs=None, log=print, checkout_root: Optional[Path] = None) -> dict:
    """Extract all manuals -> tools/pin_dir_table.csv. `pdfs` order = precedence (first wins)."""
    tools = _tools_dir(Path(repo_dir))
    pdf_list = [Path(p) for p in (pdfs or default_pdfs())]
    if not pdf_list:
        raise SystemExit("no manual PDFs configured (config.json manual_pdfs or DCDAS_MANUAL_PDFS)")
    log(f"pdftotext: {pdftotext_exe()}   cache: {cache_dir()}")
    per_doc = []
    t_all = time.time()
    for pdf in pdf_list:
        if not pdf.exists():
            log(f"  MISSING {pdf}")
            continue
        rows, st = parse_pdf(pdf, log)
        per_doc.append((rows, st))
        log(f"  {st['doc']:11s} {st['pages']:4d} pages  toc={st['toc']:3d} sections={st['sections']:3d} "
            f"tables={st['tables']:3d} pins={st['rows']:5d} (ranges +{st['range_expanded']}, templates "
            f"+{st['template_expanded']}, state {st['state_pins']}, parameters->I {st['param_pins']})  "
            f"extract {st['extract_s']:.1f}s parse {st['parse_s']:.1f}s   {st['pdf']}")
        if st["no_table"]:
            log(f"    sections without an Inputs/Outputs table ({len(st['no_table'])}): "
                + ", ".join(f"{b}@p{p}" for b, p in st["no_table"]))
        if st["caption_mismatch"]:
            log(f"    figure captions naming another block ({len(st['caption_mismatch'])}): "
                + ", ".join(f"{b}->{c}@p{p}" for b, c, p in st["caption_mismatch"][:15]))
        if st["lowercase"]:
            log(f"    mixed-case pin names accepted: {dict(st['lowercase'])}")
        if st["skipped_names"]:
            log(f"    row names rejected ({sum(st['skipped_names'].values())}): "
                + ", ".join(f"{k}x{v}" for k, v in st["skipped_names"].most_common(12)))
    rows = merge(per_doc, log)
    try:
        from .db import src_root
        croot = Path(checkout_root) if checkout_root else src_root()
    except ImportError:
        croot = Path(checkout_root) if checkout_root else None
    used = checkout_block_types(croot)[0] if croot and croot.exists() else Counter()
    rows = sorted(rows + derive_rows(rows, used, log))
    out = tools / TABLE_CSV
    n = write_table(rows, out)
    created = ensure_overrides(tools / OVERRIDE_CSV)
    types = {r[0] for r in rows}
    log(f"wrote {out} : {n} rows, {len(types)} block types, I={sum(1 for r in rows if r[2]=='I')} "
        f"O={sum(1 for r in rows if r[2]=='O')} S={sum(1 for r in rows if r[2]=='S')}  ({time.time()-t_all:.1f}s)")
    if created:
        log(f"created empty {tools / OVERRIDE_CSV}")
    cov = coverage_report(rows, croot, log)
    return {"rows": n, "types": len(types), "docs": [st for _, st in per_doc], "coverage": cov}


def load_into_db(conn, repo_dir) -> dict:
    """DELETE then INSERT pin_dir_table / pin_dir_override from tools/pin_dir_table.csv + tools/pin_dir_overrides.csv."""
    tools = _tools_dir(Path(repo_dir))
    table = read_csv(tools / TABLE_CSV, TABLE_HEADER)
    over = read_csv(tools / OVERRIDE_CSV, OVERRIDE_HEADER)
    conn.execute("DELETE FROM pin_dir_table")
    conn.executemany("INSERT OR REPLACE INTO pin_dir_table(block_type,pin_name,direction,source_doc,page) VALUES(?,?,?,?,?)",
                     [(r["block_type"], r["pin_name"], r["direction"], r["source_doc"],
                       int(r["page"]) if r["page"].isdigit() else None)
                      for r in table if r["block_type"] and r["pin_name"] and r["direction"] in ("I", "O", "S")])
    conn.execute("DELETE FROM pin_dir_override")
    conn.executemany("INSERT OR REPLACE INTO pin_dir_override(block_type,pin_name,direction,note) VALUES(?,?,?,?)",
                     [(r["block_type"], r["pin_name"], r["direction"], r["note"])
                      for r in over if r["block_type"] and r["pin_name"] and r["direction"] in ("I", "O", "S")])
    conn.commit()
    nt = conn.execute("SELECT count(*) FROM pin_dir_table").fetchone()[0]
    no = conn.execute("SELECT count(*) FROM pin_dir_override").fetchone()[0]
    return {"pin_dir_table": nt, "pin_dir_override": no}


if __name__ == "__main__":                              # py tools/dcdas/parse_manual.py [pdf ...]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run(Path(__file__).resolve().parents[1], [Path(p) for p in sys.argv[1:]] or None)
