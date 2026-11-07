"""Sequential function charts (SFC tasks): deterministic topology and the start chain between them.

An SFC task is a task with a direct child userblock `Evaluate_Transitions`.  Its layout in the checkout:

  <Program>/<Task>/Perform_Step_Actions/<Step>          step wrapper; Enable = <Task>.Steps[n]  (n = step index, 0 = initial)
  <Program>/<Task>/Perform_Step_Actions/<Step>/<Action> action userblocks (ACTION_CONTROL inside carries the qualifier)
  <Program>/<Task>/Evaluate_Transitions/<Transition>    transition wrapper; Enable = <Task>.Transitions[n]
      .Configuration (pin value)  "<nrec>, (type,index,value)*": type 1 = Steps array, 2 = Transitions array,
                                  3 = Transitions_Progression_Status; from-steps = type 1 with value 0,
                                  to-steps = type 1 with value 1.  Type 4 (seen only as (4,0,1), in the transitions
                                  that have a from-step but no to-step) is not verified and is ignored by the decoder
      /Transition_Condition*      the condition userblock (RUNG / COMPARE / ... blocks inside)

Start chain: the variables a step raises (the mirror of the wrapper's `<Step>_Active_Flag` output pin, plus every variable an
action block inside the step writes) are followed downstream - readers, their outputs, `L:` wiring, task/macro interface
pins, EGD copies to other controllers - until another SFC's `RUN` interface variable is reached.  The walking rules are the
ones of `query._Trace.down_var/down_block` (readers I/S, outputs O, declared-at-pin variables, pin mirrors, reverse `L:`,
interface pins entered through their inner `L:<pin>` pins); every pin passed records its direction source so inferred hops
(L/H/R/M/?) are visible.  Lookups go through indexed columns plus an in-memory (ctrl, path) map loaded once (ix_block_path
exists; the map saves one query per path lookup on a walk that resolves many paths).

Walk limits: MAX_HOPS is tested when a variable is dequeued (blocks reached from it by `L:` wiring are still walked, so a
listed path can hold a block or two more than MAX_HOPS); a walk stopped by MAX_HOPS is not reported in `cut` - only one that
hits MAX_NODES is.  Visited variables / blocks are shared by all branches of one source, so the single path kept per target
is the first one found (breadth-first) and `d` is judged on that path only.
"""
import re
from collections import deque

INFER = set("LHRM")
MAX_HOPS = 10          # block hops from a step variable, tested at variables (see the module docstring)
MAX_NODES = 15000      # visited blocks per step (fan-out guard; 15000 leaves no step truncated on either site, ~1 min export)
_RE_STEP = re.compile(r"\.Steps\[(\d+)\]")
_RE_TRAN = re.compile(r"\.Transitions\[(\d+)\]")
IFACE_PINS = ("RUN", "RESET", "STATUS", "COMPLETE", "MODE", "Steps", "Transitions")


def parse_config(val):
    """Decode a transition wrapper's Configuration value -> (from_steps, to_steps) as step indexes, or None."""
    try:
        nums = [int(x) for x in str(val).split(",")]
    except (TypeError, ValueError):
        return None
    if not nums:
        return None
    rest = nums[1:]
    if len(rest) % 3 or len(rest) // 3 != nums[0]:
        return None
    recs = [tuple(rest[i:i + 3]) for i in range(0, len(rest), 3)]
    return ([i for t, i, v in recs if t == 1 and v == 0], [i for t, i, v in recs if t == 1 and v == 1])


class _Index:
    """Cached, index-backed lookups over one read-only connection."""

    PIN_COLS = ("id", "block_id", "name", "direction", "dir_source", "conn_kind", "connection", "var_id",
                "tgt_block_id", "tgt_pin", "address", "value", "line_no")

    def __init__(self, conn):
        self.conn = conn
        self.block = {}        # id -> dict
        self.by_path = {}      # (ctrl, path) -> id
        self.children = {}     # parent id -> [ids] (document order)
        for r in conn.execute("SELECT b.id, b.ctrl, b.path, b.name, coalesce(b.block_type,''), b.kind, b.parent_id, b.line_no, "
                              "b.is_opaque, pr.file_path FROM block b JOIN program pr ON pr.id=b.program_id ORDER BY b.id"):
            self.block[r[0]] = {"id": r[0], "ctrl": r[1], "path": r[2], "name": r[3], "type": r[4], "kind": r[5],
                                "parent": r[6], "line": r[7], "opaque": r[8], "file": r[9]}
            self.by_path[(r[1], r[2])] = r[0]
            if r[6] is not None:
                self.children.setdefault(r[6], []).append(r[0])
        for v in self.children.values():
            v.sort(key=lambda i: (self.block[i]["line"] or 0, i))
        self._pins, self._var, self._mirror = {}, {}, {}

    def _pinrows(self, sql, args):
        return [dict(zip(self.PIN_COLS, r)) for r in self.conn.execute(
            "SELECT " + ",".join(self.PIN_COLS) + " FROM pin WHERE " + sql, args)]

    def pins(self, bid):
        if bid not in self._pins:
            self._pins[bid] = self._pinrows("block_id=? ORDER BY id", (bid,))
        return self._pins[bid]

    def pins_of_var(self, vid):
        return self._pinrows("var_id=? ORDER BY id", (vid,))

    def pins_to(self, bid, pin):
        return self._pinrows("tgt_block_id=? AND tgt_pin=? ORDER BY id", (bid, pin))

    def var(self, vid):
        if vid not in self._var:
            r = self.conn.execute("SELECT id, ctrl, name, full_name, decl_connection, address FROM variable WHERE id=?", (vid,)).fetchone()
            self._var[vid] = dict(zip(("id", "ctrl", "name", "full", "decl", "address"), r)) if r else None
        return self._var[vid]

    def var_id(self, full):
        ctrl, _, name = full.partition(".")
        r = self.conn.execute("SELECT id FROM variable WHERE ctrl=? AND name=?", (ctrl, name)).fetchone()
        return r[0] if r else None

    def mirrors(self, pin_id):
        if pin_id not in self._mirror:
            self._mirror[pin_id] = [tuple(r) for r in self.conn.execute("SELECT var_id, kind FROM pin_mirror WHERE pin_id=?", (pin_id,))]
        return self._mirror[pin_id]

    def descendants(self, bid):
        out, stack = [], list(self.children.get(bid, []))[::-1]
        while stack:
            i = stack.pop()
            out.append(i)
            stack.extend(self.children.get(i, [])[::-1])
        return out

    def decl_pin(self, v):
        """The pin at which variable v is declared ('Program.Task[.Block..].Pin'; names may contain dots)."""
        parts = (v["decl"] or "").split(".")
        for i in range(2, len(parts)):
            bid = self.by_path.get((v["ctrl"], "/".join(parts[:i])))
            if bid:
                pn = ".".join(parts[i:])
                for p in self.pins(bid):
                    if p["name"] == pn:
                        return p
        return None

    def var_at_pin(self, p):
        """The variable declared at pin p (interface pins and 'declared at pin' block pins carry only an address)."""
        b = self.block[p["block_id"]]
        decl = b["path"].replace("/", ".") + "." + p["name"]
        if p["address"]:
            r = self.conn.execute("SELECT id FROM variable WHERE ctrl=? AND address=? AND decl_connection=?",
                                  (b["ctrl"], p["address"], decl)).fetchone()
            if r:
                return r[0]
        return None

    def var_pins(self, v, dirs):
        """Pins carrying variable v in directions `dirs` (same rule as query._var_pins): pins wired to it, plus its
        declaring block pin, or - when declared at a task/macro interface pin - the inner pins wired to that pin."""
        pins = [p for p in self.pins_of_var(v["id"]) if (p["direction"] or "?") in dirs]
        dp = self.decl_pin(v)
        if dp and dp["var_id"] != v["id"] and all(dp["id"] != q["id"] for q in pins):
            if self.block[dp["block_id"]]["kind"] == "block":
                if (dp["direction"] or "?") in dirs:
                    pins.append(dp)
            else:
                for q in self.pins_to(dp["block_id"], dp["name"]):
                    if q["conn_kind"] == "P" and (q["direction"] or "?") in dirs and all(q["id"] != x["id"] for x in pins):
                        pins.append(q)
        return pins

    def hop(self, p):
        b = self.block[p["block_id"]]
        h = {"k": f"{b['ctrl']}|{b['path']}", "pin": p["name"], "type": b["type"] or b["kind"],
             "ds": (p["direction"] or "?") + "/" + (p["dir_source"] or "-"), "at": f"{b['file']}:{p['line_no']}"}
        if b["opaque"]:
            h["op"] = 1            # encrypted macro: walked like a block through its recovered pins, internals not visible
        elif b["kind"] != "block":
            h["ub"] = 1            # task / macro interface pin (the path passes through, the logic is inside)
        return h


def sfc_tasks(ix, ctrl=None):
    """[(ctrl, program, task, task block id)] for every SFC task, sorted."""
    out = []
    for b in ix.block.values():
        if b["kind"] == "userblock" and b["name"] == "Evaluate_Transitions" and b["parent"] in ix.block:
            t = ix.block[b["parent"]]
            if t["kind"] == "task" and (ctrl is None or t["ctrl"] == ctrl):
                prog, task = t["path"].split("/", 1)
                out.append((t["ctrl"], prog, task, t["id"]))
    return sorted(out)


def sfc_key(ctrl, program, task):
    return f"{ctrl}|{program}/{task}"


def topology(ix, ctrl, program, task):
    """Steps, transitions and interface of one SFC task; block references are task-shard keys `CTRL|path`."""
    base = f"{program}/{task}"
    tid = ix.by_path.get((ctrl, base))
    if not tid:
        return None
    vname = lambda vid: (ix.var(vid) or {}).get("full")
    iface = {}
    for p in ix.pins(tid):
        if p["name"] in IFACE_PINS:
            vid = p["var_id"] or ix.var_at_pin(p)
            if vid and vname(vid):
                iface[p["name"]] = vname(vid)
    steps = []
    psa = ix.by_path.get((ctrl, base + "/Perform_Step_Actions"))
    for sid in ix.children.get(psa, []):
        sb = ix.block[sid]
        if sb["kind"] != "userblock":
            continue
        pins = ix.pins(sid)
        en = next((p for p in pins if p["name"] == "Enable"), None)
        m = _RE_STEP.search(en["connection"] or "") if en else None
        s = {"i": int(m.group(1)) if m else None, "n": sb["name"], "line": sb["line"]}
        for p in pins:
            if p["name"].endswith("_Active_Flag"):
                fl = [vname(v) for v, k in ix.mirrors(p["id"]) if k == "O" and vname(v)]
                if fl:
                    s["flag"] = fl[0]
        acts = []
        for aid in ix.children.get(sid, []):
            ab = ix.block[aid]
            if ab["kind"] != "userblock":
                continue
            qual, outs = None, set()
            for iid in ix.descendants(aid):
                for p in ix.pins(iid):
                    if ix.block[iid]["type"] == "ACTION_CONTROL" and p["name"] == "Action_Qualifier":
                        qual = p["connection"]
                    elif p["direction"] == "O" and p["var_id"] and vname(p["var_id"]):
                        outs.add(vname(p["var_id"]))
            a = {"n": ab["name"]}
            if qual:
                a["q"] = qual
            if outs:
                a["w"] = sorted(outs)
            acts.append(a)
        if acts:
            s["a"] = acts
        steps.append(s)
    trans = []
    eva = ix.by_path.get((ctrl, base + "/Evaluate_Transitions"))
    for trid in ix.children.get(eva, []):
        trb = ix.block[trid]
        if trb["kind"] != "userblock":
            continue
        pins = ix.pins(trid)
        en = next((p for p in pins if p["name"] == "Enable"), None)
        cfg = next((p for p in pins if p["name"] == "Configuration"), None)
        m = _RE_TRAN.search(en["connection"] or "") if en else None
        dec = parse_config(cfg["value"]) if cfg and cfg["value"] else None
        t = {"i": int(m.group(1)) if m else None, "n": trb["name"], "line": trb["line"]}
        for p in pins:
            if p["name"].endswith("_Active_Flag"):
                fl = [vname(v) for v, k in ix.mirrors(p["id"]) if k == "O" and vname(v)]
                if fl:
                    t["flag"] = fl[0]
        if dec:
            t["f"], t["t"] = dec
        else:
            t["bad"] = 1           # Configuration missing or not decodable: from/to unknown
        cond = [c for c in ix.children.get(trid, []) if ix.block[c]["kind"] == "userblock" and ix.block[c]["name"].startswith("Transition_Condition")]
        if cond:
            t["c"] = ix.block[cond[0]]["name"]
            reads = {}
            for iid in ix.descendants(cond[0]):
                for p in ix.pins(iid):
                    if p["var_id"] and p["direction"] != "O" and p["conn_kind"] == "V" and vname(p["var_id"]):
                        reads.setdefault(vname(p["var_id"]), p["dir_source"] or "-")
            if reads:
                t["r"] = [[n, s] for n, s in sorted(reads.items())]
        trans.append(t)
    return {"line": ix.block[tid]["line"], "file": ix.block[tid]["file"], "iface": iface, "steps": steps, "trans": trans}


# ------------------------------------------------------------------------------------------------ start chain
def walk(ix, start_vids, run_vars, own_key, egd):
    """Breadth-first downstream walk from one step's variables.
    Returns ({target sfc key: path}, truncated).  Only start paths count: a path that enters a task/macro interface
    pin named RESET or PAUSE (the SFC_Interface wrapper drives RUN False from those) is not followed through it."""
    found = {}
    q = deque(("v", vid, 0, ()) for vid in start_vids)
    seen_v, seen_b, seen_ip = set(), set(), set()
    n = 0

    def enter(h, hops, path):
        """Queue the work behind reader pin h: an ordinary block, or the inner pins behind an interface pin."""
        hop = ix.hop(h)
        hb = ix.block[h["block_id"]]
        if hb["kind"] == "block" or hb["opaque"]:
            # ordinary block, or an encrypted macro: no inner pins to enter, follow its (recovered) outputs like
            # query._Trace.down_block does
            q.append(("b", h["block_id"], hops + 1, path + (hop,)))
            return
        if h["name"].upper() in ("RESET", "PAUSE") or h["id"] in seen_ip:
            return
        seen_ip.add(h["id"])
        for ip in ix.pins_to(h["block_id"], h["name"]):
            if ip["conn_kind"] == "P" and ip["direction"] in ("I", "S"):
                q.append(("b", ip["block_id"], hops + 1, path + (hop, ix.hop(ip))))

    def leave(ip, hops, path):
        """Output reached interface pin `ip` of the enclosing macro/task (inner pin wired 'L:<pin>'): continue outside."""
        if ip["id"] in seen_ip:
            return
        seen_ip.add(ip["id"])
        path = path + (ix.hop(ip),)
        if ip["conn_kind"] == "V" and ip["var_id"]:
            q.append(("v", ip["var_id"], hops, path))
        else:
            dv = ix.var_at_pin(ip)
            if dv:
                q.append(("v", dv, hops, path))
        for mv, mk in ix.mirrors(ip["id"]):
            if mk == "O":
                q.append(("v", mv, hops, path))
        for h in ix.pins_to(ip["block_id"], ip["name"]):
            if h["conn_kind"] != "P" and h["direction"] in ("I", "S"):
                enter(h, hops, path)

    while q:
        kind, ident, hops, path = q.popleft()
        if kind == "v":
            if ident in seen_v:
                continue
            seen_v.add(ident)
            v = ix.var(ident)
            if not v:
                continue
            path = path + ({"v": v["full"]},)
            tgt = run_vars.get(ident)
            if tgt and tgt != own_key:
                if tgt not in found:
                    found[tgt] = path
                continue
            if hops >= MAX_HOPS:
                continue
            for p in ix.var_pins(v, ("I", "S")):
                enter(p, hops, path)
            for exch, local, cons in egd.get(ident, ()):
                q.append(("v", local, hops, path + ({"egd": exch, "to": cons},)))
        else:
            if ident in seen_b:
                continue
            seen_b.add(ident)
            n += 1
            if n > MAX_NODES:
                return found, True
            for p in ix.pins(ident):
                if p["direction"] != "O":
                    continue
                hop = ix.hop(p)
                if p["conn_kind"] == "V" and p["var_id"]:
                    q.append(("v", p["var_id"], hops, path + (hop,)))
                elif p["conn_kind"] == "A":
                    dv = ix.var_at_pin(p)
                    if dv:
                        q.append(("v", dv, hops, path + (hop,)))
                elif p["conn_kind"] == "P" and p["tgt_block_id"]:
                    ip = next((x for x in ix.pins(p["tgt_block_id"]) if x["name"] == p["tgt_pin"]), None)
                    if ip:
                        leave(ip, hops, path + (hop,))
                elif p["conn_kind"] == "L" and p["tgt_block_id"]:
                    q.append(("b", p["tgt_block_id"], hops + 1, path + (hop,)))
                for mv, mk in ix.mirrors(p["id"]):
                    if mk == "O":
                        q.append(("v", mv, hops, path + (hop,)))
                for h in ix.pins_to(ident, p["name"]):
                    if h["direction"] in ("I", "S"):
                        enter(h, hops, path + (hop,))
    return found, False


def inferred(path):
    """True when any pin on the path has an inferred direction source (L/H/R/M) or an unknown direction."""
    for h in path:
        ds = h.get("ds")
        if ds and (ds.startswith("?") or ds.split("/")[-1] in INFER):
            return True
    return False


PASS_TYPES = {"MOVE", "AND", "OR", "PULSE"}


def direct(path):
    """True when the path only passes copies and gates (MOVE / AND / OR / PULSE), EGD, task / macro interface pins and
    encrypted macros (`op`, flagged separately: their inside is not visible); anything else (RUNG, LATCH, timers,
    comparators, logic builders...) makes it an indirect influence."""
    return all(h.get("ub") or h.get("op") or h["type"] in PASS_TYPES for h in path if "type" in h)


def _writer_const(ix, path):
    """The literal a MOVE writes into the RUN variable at the end of the path (e.g. 'True'), if it is a constant."""
    for h in reversed(path):
        if "type" in h:
            if h["type"] != "MOVE":
                return None
            bid = ix.by_path.get(tuple(h["k"].split("|", 1)))
            src = next((p for p in ix.pins(bid) if p["name"] == "SRC"), None) if bid else None
            if src and src["conn_kind"] in ("N", "E") and src["connection"]:
                return src["connection"].split(":", 1)[-1]
            return None
    return None


def _source_const(ix, sfc_key_, sk, name, full):
    """The literal the source step's / transition's own logic writes into its start variable `full` (a MOVE inside the
    wrapper with a constant SRC, e.g. '0' when the step clears a command), or None (flag variable, not a constant...)."""
    ctrl, base = sfc_key_.split("|", 1)
    wrapper = f"{base}/{'Perform_Step_Actions' if sk == 's' else 'Evaluate_Transitions'}/{name}/"
    vid = ix.var_id(full)
    if not vid:
        return None
    vals = set()
    for p in ix.pins_of_var(vid):
        b = ix.block.get(p["block_id"])
        if p["direction"] != "O" or not b or b["ctrl"] != ctrl or not b["path"].startswith(wrapper):
            continue
        if b["type"] != "MOVE":
            return None
        src = next((x for x in ix.pins(b["id"]) if x["name"] == "SRC"), None)
        if not src or src["conn_kind"] not in ("N", "E") or not src["connection"]:
            return None
        vals.add(src["connection"].split(":", 1)[-1])
    return vals.pop() if len(vals) == 1 else None


def chain(ix, topo):
    """Edges from a step or transition to an SFC:
    [{"f": sfcKey, "s": name, "sk": "s"|"t", "t": sfcKey, "p": path, "inf": 0|1, "d": 0|1, "w": const?, "sv": const?}]
    and truncated sources.  `sv` = the literal the source's own MOVE writes into the start variable (e.g. '0': it clears it)."""
    run_vars = {}
    for key, t in topo.items():
        rv = t["iface"].get("RUN")
        vid = ix.var_id(rv) if rv else None
        if vid:
            run_vars[vid] = key
    egd = {}
    for exch, local, cons, prod in ix.conn.execute(
            "SELECT exchange_id, local_var_id, consumer_ctrl, producer_var_id FROM egd_consumed "
            "WHERE producer_var_id IS NOT NULL AND local_var_id IS NOT NULL"):
        egd.setdefault(prod, []).append((exch, local, cons))
    edges, cut = [], []
    for key, t in topo.items():
        own = {t["iface"].get("Steps"), t["iface"].get("Transitions")}
        sources = [("s", s) for s in t["steps"]] + [("t", tr) for tr in t["trans"]]
        for sk, s in sources:
            names = ([s["flag"]] if s.get("flag") else []) + [w for a in s.get("a", []) for w in a.get("w", [])]
            starts = [vid for vid in (ix.var_id(nm) for nm in names if nm not in own) if vid]
            if not starts:
                continue
            found, truncated = walk(ix, starts, run_vars, key, egd)
            if truncated:
                cut.append([key, s["n"]])
            for tgt in sorted(found):
                path = list(found[tgt])
                e = {"f": key, "s": s["n"], "sk": sk, "t": tgt, "p": path, "inf": 1 if inferred(path) else 0,
                     "d": 1 if direct(path) else 0}
                if any(h.get("op") for h in path):
                    e["op"] = 1
                w = _writer_const(ix, path)
                if w is not None:
                    e["w"] = w
                sv = _source_const(ix, key, sk, s["n"], path[0]["v"]) if path and "v" in path[0] else None
                if sv is not None:
                    e["sv"] = sv
                edges.append(e)
    return edges, cut


def export(conn, log=print, ctrl=None):
    """Everything the web page needs: {"sfc": {key: topology}, "edges": [...], "cut": [...]}."""
    ix = _Index(conn)
    topo = {}
    for c, prog, task, _tid in sfc_tasks(ix, ctrl):
        t = topology(ix, c, prog, task)
        if t:
            topo[sfc_key(c, prog, task)] = t
    edges, cut = chain(ix, topo)
    log(f"    {len(topo)} SFC tasks, {len(edges)} start-chain edges ({sum(e['d'] for e in edges)} direct)" + (f", {len(cut)} steps hit the walk limit" if cut else ""))
    return {"sfc": topo, "edges": edges, "cut": cut}
