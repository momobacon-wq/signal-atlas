# -*- coding: utf-8 -*-
"""Post stage: infer pin.direction / pin.dir_source for EVERY pin row and fill the pin_dir decision table.

Called after all parsers:  run(conn, repo_dir, log)   (repo_dir = tools/ or the repo root; passed to
parse_manual.load_into_db which reloads tools/pin_dir_table.csv + tools/pin_dir_overrides.csv into the DB).

direction:  'I' the pin READS what it is connected to, 'O' the pin WRITES it, 'S' state/const/other, '?' unknown.
dir_source: which evidence decided it (letters are the same in pin.dir_source and pin_dir.source):
  U  Pin@Usage on the pin itself (Input->I, Output->O, Const/State/others->S) - interface pins of tasks/macros incl.
     the auto pins Heartbeat/Enable/BlockCPUTicks; also the P special case below, and (for the per-type table)
     lib_pin_usage definition pins of that block_type (majority over libraries, ties skipped).
  T  tools/pin_dir_overrides.csv (highest), then tools/pin_dir_table.csv (manual PDFs).
  C  constant rule: (block_type,pin) that carries >=1 'N:'/'E:' connection and is targeted by zero 'L:' -> I.
  L  vote over 'L:Block.Pin' references: the holder is an input candidate (n_carries), the target an output
     candidate (n_targeted). O if n_targeted >= 3*n_carries (>0); I if n_carries >= 3*n_targeted (>0).
  H  name heuristics (regexes O_RE / I_RE below).
  -  nothing decided -> '?'.

Pin key: every (block_type, pin) decision is keyed on coalesce(pin.lib_name, pin.name).  Pins carrying a LibName
template ('{Device}', '{Device}{Type}', '{Device}{BlockSuffix}') have a different expanded name on every instance
(the AI block's output pin is named after its Device attribute, the manual calls it OUT), so the template is the
only stable key; the override CSV lists them under the template name (e.g. AI,{Device},O).

Per-pin precedence (highest first):
  1. Pin@Usage on the pin itself (U).
  2. The per-(block_type,pin_name) decision table when it was decided by U (lib / instance usage) or T (manual).
  3. conn_kind 'P' (pin wired to an interface pin of the enclosing macro/task, 'L:Y'): holder direction = that
     interface pin's Usage (Output->O, Input/Const->I), dir_source 'U'.  Interface pins with Usage=State are
     macro-internal state that inner blocks both write and read (measured: CALC.OUT->L:mu and CALC.A->L:mu in G11
     CombustorAutotune), so a State target decides nothing.
     NOTE: the task text ranked P above everything; measured on all 15 controllers the manual contradicts P for
     2,332 of 15,389 manual-covered P pins (2,324 are inner INPUT pins such as LATCH.SET reading the macro's own
     Output interface pin 'L:Alert_x' as feedback), while P vs U/C/L/H disagree on <=9 pins each.  P therefore ranks
     below T/U and above C/L/H; it still decides ~16k pins on block types the manual does not cover (AI output pins
     named after the device tag, site macros, SFC blocks).
  4. The rest of the decision table (C > L > H > ?), joined through block.block_type.

Vote details: an 'L:' holder whose own Usage is 'Output' (455 of 13,480 L pins in G11/WSC1/BOPE1: macro output
interface pins wired to an inner block) is the WRITER of the wire, so it casts no input/output vote at all
(its own direction is fixed by U).  Everything is computed from GROUP BY aggregates into Python dicts, the
decision table goes into a TEMP table and pins are updated with UPDATE ... FROM (SQLite >= 3.33) - no per-row
Python loop over the ~1.1M pin rows.

Also exposed for the CLI / query module:
  lint(conn, limit)      variables written by more than one direction='O' pin.
  coverage(conn, limit)  per controller: fraction of connected pins (conn_kind V/L/P/D) with direction '?', plus
                         the top unknown (block_type,pin_name) by instance count.
"""
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from . import parse_manual

O_RE = re.compile(r"^(OUT|OUT_A|NOT_A|DEST|Q|STATUS|OUT\d+|.*_OUT|OUT_VAL|OUT_VAL\d+)$")
I_RE = re.compile(r"^(IN\d*|IN\d+[A-Z]{1,2}|SRC|ENABLE|SET|RESET|A|B|C|D|.*_IN|IN_\d+|TRIG|PU_DEL|DO_DEL|EQN|EN|IN[AB]\d+)$")
# IN[AB]\d+ added for SELECTOR INA3..INB16 (manual lists only INA1/2/16, INB1/2/16; 3,460 connected pins were '?')

USAGE_DIR = {"Input": "I", "Output": "O"}          # anything else declared -> 'S'
CONNECTED = "('V','L','P','D')"


def usage_to_dir(usage) -> str:
    return USAGE_DIR.get(usage, "S")


def _heuristic(pin_name: str):
    if O_RE.match(pin_name):
        return "O"
    if I_RE.match(pin_name):
        return "I"
    return None


# ------------------------------------------------------------------------------------------- aggregates
def _aggregates(conn, log):
    """One GROUP BY pass over pin JOIN block -> {(type,pin): [n_inst, u_in, u_out, u_other, n_const, n_carries]}."""
    t0 = time.time()
    agg: Dict[Tuple[str, str], list] = {}
    for bt, pn, n, ui, uo, ux, nc, nl in conn.execute("""
        SELECT coalesce(b.block_type,''), coalesce(p.lib_name, p.name), count(*),
               sum(p.usage_declared='Input'), sum(p.usage_declared='Output'),
               sum(p.usage_declared IS NOT NULL AND p.usage_declared NOT IN ('Input','Output')),
               sum(p.conn_kind IN ('N','E')),
               sum(p.conn_kind='L' AND (p.usage_declared IS NULL OR p.usage_declared<>'Output'))
        FROM pin p JOIN block b ON b.id=p.block_id
        GROUP BY 1,2"""):
        agg[(bt, pn)] = [n, ui or 0, uo or 0, ux or 0, nc or 0, nl or 0]
    log(f"  aggregates: {len(agg)} (block_type,pin) keys   {time.time()-t0:.1f}s")

    t0 = time.time()
    targeted: Dict[Tuple[str, str], int] = {}
    for bt, pn, n in conn.execute("""
        SELECT coalesce(tb.block_type,''), coalesce(t.lib_name, p.tgt_pin), count(*)
        FROM pin p JOIN block tb ON tb.id=p.tgt_block_id
        LEFT JOIN pin t ON t.block_id=p.tgt_block_id AND t.name=p.tgt_pin
        WHERE p.conn_kind='L' AND p.tgt_pin IS NOT NULL
          AND (p.usage_declared IS NULL OR p.usage_declared<>'Output')
        GROUP BY 1,2"""):
        targeted[(bt, pn)] = n
    log(f"  L targets : {len(targeted)} keys, {sum(targeted.values())} references   {time.time()-t0:.1f}s")
    return agg, targeted


def _lib_usage(conn, log):
    """{(def_name, pin_name): (direction, n_rows)} from lib_pin_usage, majority over libraries; ties dropped."""
    votes: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for dn, pn, usage in conn.execute(
            "SELECT def_name, pin_name, usage FROM lib_pin_usage WHERE pin_name<>'' AND usage NOT LIKE '\\_\\_%' ESCAPE '\\'"):
        votes[(dn, pn)][usage_to_dir(usage)] += 1
    out, ties = {}, 0
    for k, c in votes.items():
        top = c.most_common(2)
        if len(top) > 1 and top[0][1] == top[1][1]:
            ties += 1
            continue
        out[k] = (top[0][0], sum(c.values()))
    log(f"  lib usage : {len(out)} (def,pin) keys usable, {ties} ties skipped")
    return out


def _manual(conn, log):
    over = {(r[0], r[1]): r[2] for r in conn.execute("SELECT block_type,pin_name,direction FROM pin_dir_override")}
    table = {(r[0], r[1]): r[2] for r in conn.execute("SELECT block_type,pin_name,direction FROM pin_dir_table")}
    log(f"  manual    : pin_dir_table {len(table)} rows, pin_dir_override {len(over)} rows")
    return over, table


# ---------------------------------------------------------------------------------------------- decision
def decide(agg, targeted, lib, over, table):
    """-> {(type,pin): (direction, source, n_targeted, n_carries, n_const, n_usage)} for every key seen."""
    keys = set(agg) | set(targeted)
    out = {}
    for k in keys:
        n, ui, uo, ux, nc, nl = agg.get(k, (0, 0, 0, 0, 0, 0))
        nt = targeted.get(k, 0)
        n_usage = ui + uo + ux
        d = s = None
        if n_usage:
            best = max((ui, "I"), (uo, "O"), (ux, "S"))    # majority; tie -> the later tuple wins (S > O > I)
            d, s = best[1], "U"
        elif k in lib:
            d, s = lib[k][0], "U"
            n_usage = lib[k][1]
        elif k in over:
            d, s = over[k], "T"
        elif k in table:
            d, s = table[k], "T"
        elif nc > 0 and nt == 0:
            d, s = "I", "C"
        elif nt > 0 and nt >= 3 * nl:
            d, s = "O", "L"
        elif nl > 0 and nl >= 3 * nt:
            d, s = "I", "L"
        else:
            h = _heuristic(k[1])
            if h:
                d, s = h, "H"
        if d is None:
            d, s = "?", "-"
        out[k] = (d, s, nt, nl, nc, n_usage)
    return out


# ------------------------------------------------------------------------------------------------- run
def run(conn, repo_dir, log=print) -> dict:
    t_all = time.time()
    stats = {}
    # T tables (CSV -> DB); a missing CSV is not fatal for the stage
    try:
        stats["manual"] = parse_manual.load_into_db(conn, Path(repo_dir))
    except Exception as e:                               # noqa: BLE001
        log(f"  WARN manual tables not loaded: {e}")
        stats["manual"] = {"error": str(e)}

    agg, targeted = _aggregates(conn, log)
    lib = _lib_usage(conn, log)
    over, table = _manual(conn, log)
    t0 = time.time()
    dec = decide(agg, targeted, lib, over, table)
    src_count = Counter(v[1] for v in dec.values())
    log(f"  decided   : {len(dec)} keys  " + " ".join(f"{k}={src_count[k]}" for k in "UTCLH-") + f"   {time.time()-t0:.1f}s")

    # pin_dir table (owned entirely by this stage)
    t0 = time.time()
    conn.execute("DELETE FROM pin_dir")
    conn.executemany("INSERT INTO pin_dir(block_type,pin_name,direction,source,n_targeted,n_carries,n_const,n_usage) "
                     "VALUES(?,?,?,?,?,?,?,?)", [(k[0], k[1]) + v for k, v in dec.items()])
    conn.commit()
    stats["pin_dir_rows"] = len(dec)

    # per-pin update: table decision, then own Usage, then the P special case
    conn.execute("DROP TABLE IF EXISTS temp.dir_tab")
    conn.execute("CREATE TEMP TABLE dir_tab(block_type TEXT, pin_name TEXT, direction TEXT, source TEXT, "
                 "PRIMARY KEY(block_type, pin_name)) WITHOUT ROWID")
    conn.executemany("INSERT INTO temp.dir_tab VALUES(?,?,?,?)",
                     [(k[0], k[1], v[0], v[1]) for k, v in dec.items() if v[1] != "-"])
    conn.execute("UPDATE pin SET direction='?', dir_source='-'")
    cur = conn.execute("""UPDATE pin SET direction=d.direction, dir_source=d.source
        FROM block b, temp.dir_tab d
        WHERE b.id=pin.block_id AND d.block_type=coalesce(b.block_type,'') AND d.pin_name=coalesce(pin.lib_name, pin.name)""")
    n_tab = cur.rowcount
    cur = conn.execute("""UPDATE pin SET direction=CASE usage_declared WHEN 'Input' THEN 'I' WHEN 'Output' THEN 'O' ELSE 'S' END,
        dir_source='U' WHERE usage_declared IS NOT NULL AND usage_declared<>''""")
    n_own = cur.rowcount
    conn.execute("DROP TABLE IF EXISTS temp.p_dir")
    conn.execute("""CREATE TEMP TABLE p_dir AS
        SELECT p.id AS id, CASE t.usage_declared WHEN 'Output' THEN 'O' ELSE 'I' END AS direction
        FROM pin p JOIN pin t ON t.block_id=p.tgt_block_id AND t.name=p.tgt_pin
        JOIN block b ON b.id=p.block_id
        LEFT JOIN temp.dir_tab d ON d.block_type=coalesce(b.block_type,'') AND d.pin_name=coalesce(p.lib_name, p.name)
        WHERE p.conn_kind='P' AND t.usage_declared IN ('Input','Output','Const')
          AND (d.source IS NULL OR d.source NOT IN ('U','T'))""")
    cur = conn.execute("UPDATE pin SET direction=q.direction, dir_source='U' FROM temp.p_dir q "
                       "WHERE q.id=pin.id AND (pin.usage_declared IS NULL OR pin.usage_declared='')")
    n_p = cur.rowcount
    conn.execute("DROP TABLE IF EXISTS temp.p_dir")
    conn.execute("DROP TABLE IF EXISTS temp.dir_tab")
    conn.commit()
    log(f"  pin update: table {n_tab}, own usage {n_own}, P-special {n_p}   {time.time()-t0:.1f}s")
    stats.update({"pins_from_table": n_tab, "pins_own_usage": n_own, "pins_p_special": n_p})

    # report
    by_src = {r[0]: r[1] for r in conn.execute("SELECT dir_source, count(*) FROM pin GROUP BY 1")}
    by_dir = {r[0]: r[1] for r in conn.execute("SELECT direction, count(*) FROM pin GROUP BY 1")}
    log("  pins by source: " + " ".join(f"{k}={by_src.get(k,0)}" for k in "UTCLH-")
        + "   by direction: " + " ".join(f"{k}={by_dir.get(k,0)}" for k in "IOS?"))
    cov = coverage(conn, limit=0)
    for c in cov["controllers"]:
        log(f"  dir {c['ctrl']:7s} connected {c['n_connected']:8d}  unknown {c['n_unknown']:6d}  ({c['frac_unknown']*100:5.2f}%)")
    stats["by_source"], stats["by_direction"], stats["coverage"] = by_src, by_dir, cov["controllers"]
    log(f"  direction done {time.time()-t_all:.1f}s")
    return stats


# ------------------------------------------------------------------------------------------- lint / coverage
def lint(conn, limit: int = 40) -> List[dict]:
    """Variables with more than one writer pin (direction='O'). Rows: full_name, n_writers, writers[]."""
    rows = []
    q = """SELECT v.id, v.full_name, count(*) AS n FROM pin p JOIN variable v ON v.id=p.var_id
           WHERE p.direction='O' GROUP BY v.id HAVING n>1 ORDER BY n DESC, v.full_name"""
    if limit:
        q += f" LIMIT {int(limit)}"
    for vid, full, n in conn.execute(q).fetchall():
        writers = [f"{b[0]}/{b[1]}.{b[2]} [{b[3]}] {b[5] or ''}:{b[4] or ''} {b[6]}".rstrip()
                   for b in conn.execute("""SELECT b.ctrl, b.path, p.name, coalesce(b.block_type,''), p.line_no,
                                                   pr.file_path, p.dir_source
                                            FROM pin p JOIN block b ON b.id=p.block_id
                                            JOIN program pr ON pr.id=b.program_id
                                            WHERE p.var_id=? AND p.direction='O' ORDER BY b.ctrl, b.path""", (vid,))]
        rows.append({"var_id": vid, "full_name": full, "n_writers": n, "writers": writers})
    return rows


def coverage(conn, limit: int = 20) -> dict:
    """Per controller: connected pins (conn_kind V/L/P/D) and how many are '?'; top unknown (type,pin) by count."""
    ctrls = []
    for ctrl, n_conn, n_unk in conn.execute(f"""
        SELECT b.ctrl, count(*), sum(p.direction='?') FROM pin p JOIN block b ON b.id=p.block_id
        WHERE p.conn_kind IN {CONNECTED} GROUP BY b.ctrl ORDER BY b.ctrl"""):
        ctrls.append({"ctrl": ctrl, "n_connected": n_conn, "n_unknown": n_unk or 0,
                      "frac_unknown": (n_unk or 0) / n_conn if n_conn else 0.0})
    top = []
    if limit:
        top = [{"block_type": bt, "pin_name": pn, "n": n} for bt, pn, n in conn.execute(f"""
            SELECT coalesce(b.block_type,''), coalesce(p.lib_name, p.name), count(*) AS n FROM pin p JOIN block b ON b.id=p.block_id
            WHERE p.direction='?' AND p.conn_kind IN {CONNECTED}
            GROUP BY 1,2 ORDER BY n DESC, 1, 2 LIMIT {int(limit)}""")]
    return {"controllers": ctrls, "top_unknown": top}
