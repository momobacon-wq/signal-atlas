# -*- coding: utf-8 -*-
"""Fixed-width text rendering of query.py result dicts (for Claude Code / terminals).

Rules: ASCII, English keywords, ONE fact per line, at most CAP lines per section unless args.all, then
'... +N more (--all)'. Direction is always printed as 'dir/src' (e.g. 'O/T', '?/-'), never guessed.
render(res, args) -> str.  args is the argparse namespace (only .all is looked at; may be missing).
"""

CAP = 40


def _s(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _cut(text, n=110):
    text = _s(text).replace("\n", " ").replace("\r", " ")
    return text if len(text) <= n else text[:n - 3] + "..."


def _all(args):
    return bool(getattr(args, "all", False))


class _Out:
    def __init__(self, args):
        self.lines = []
        self.cap = None if _all(args) else CAP
        # only 'show' has an --all flag; other commands get everything through --json
        self.hint = "--all" if (args is None or hasattr(args, "all")) else "--json for all"

    def line(self, text=""):
        self.lines.append(text)

    def head(self, text):
        self.lines.append(text)

    def rows(self, items, fn, total=None, indent="  "):
        """Emit fn(item) per item, capped; 'total' = true count when items is already truncated."""
        n = len(items)
        total = total if total is not None else n
        shown = items if self.cap is None else items[:self.cap]
        for it in shown:
            t = fn(it)
            if isinstance(t, (list, tuple)):
                for x in t:
                    self.lines.append(indent + x)
            elif t is not None:
                self.lines.append(indent + t)
        if total > len(shown):
            self.lines.append(f"{indent}... +{total - len(shown)} more ({self.hint})")

    def text(self):
        return "\n".join(self.lines)


# ------------------------------------------------------------------------------------------------ pieces
def _kv(o, key, val, indent="  "):
    if val is None or val == "" or val == []:
        return
    o.line(f"{indent}{key:<14s} {_cut(val, 160)}")


def _ambiguous(o, r):
    o.head(f"AMBIGUOUS '{r['key']}' matched by {r.get('matched_by', 'name')} in {len(r['candidates'])} places - use CTRL.NAME:")
    o.rows(r["candidates"], lambda c: f"{c['full_name']:<40s} {_s(c['datatype']):<8s} {_cut(c['description'], 70)}")


def _notfound(o, r):
    o.head(f"NOT FOUND '{r['key']}': {r.get('hint', '')}")
    if r.get("candidates"):
        o.line("  candidates:")
        o.rows(r["candidates"], lambda c: _s(c), indent="    ")


def _show(o, r):
    d = r["def"]
    o.head(f"SIGNAL {d['full_name']}  [{_s(d['datatype'])}]")
    o.line("DEF")
    _kv(o, "desc", d["description"])
    _kv(o, "address", d["address"])
    _kv(o, "value", d["value"])
    _kv(o, "scope", d["scope"])
    _kv(o, "egd_page", d["egd_page"])
    _kv(o, "alias", d["alias"])
    rng = []
    if d["units"]:
        rng.append(f"units {d['units']}")
    if d["disp_low"] is not None or d["disp_high"] is not None:
        rng.append(f"range {_s(d['disp_low'])}..{_s(d['disp_high'])}")
    if d["format_spec"]:
        rng.append(f"format_spec {d['format_spec']}")
    if rng:
        _kv(o, "units/range", "  ".join(rng))
    if d["control_constant"]:
        _kv(o, "const", "control constant")
    if d["is_program_local"]:
        _kv(o, "scope", "program-local variable")
    if d.get("sub_of"):
        _kv(o, "sub_of", f"{d['ctrl']}.{d['sub_of']}  (alarm attribute .{d['name'].rsplit('.', 1)[-1]} of that signal)")
    dc = d["decl"]
    decl = " ".join(x for x in (f"{dc['program']}.{dc['task']}" if dc["task"] else dc["program"], dc["at"]) if x)
    _kv(o, "decl", decl or f"({dc['connection'] or 'no declaration site'})")
    _kv(o, "referenced_in", ", ".join(d["referenced_in"]))
    if d["device_name"]:
        _kv(o, "device_name", f"{d['device_name']} (EGD consumed copy)")
    _kv(o, "producer", d["producer"])
    _kv(o, "display_screen", d["display_screen"])
    if d.get("interface"):
        i = d["interface"]
        _kv(o, "interface", f"{i['ref']} usage={_s(i['usage'])} {i['dir']} {i['at']}" + (" OPAQUE" if i["opaque"] else "")
            + f"  ({i['block_kind']} interface pin = this variable)")
    o.line(f"  {'source':<14s} {r['source']}")

    o.line(f"WRITERS ({r['writers_total']})")
    if not r["writers"]:
        o.line("  none")

    def _w(w):
        out = [f"{w['ref']} [{w['block_type']}] {w['dir']} {w['at']}" + (f" {w['note']}" if w.get("note") else "")]
        for i in w["inputs"]:
            out.append(f"    in {i['pin']} {i['dir']} <- {_cut(i['to'], 100)}")
        if w.get("inputs_more"):
            out.append(f"    in ... +{w['inputs_more']} more inputs (block {w['ref'].rsplit('.', 1)[0]})")
        return out
    o.rows(r["writers"], _w, total=r["writers_total"])

    o.line(f"READERS ({r['readers_total']})")
    if not r["readers"]:
        o.line("  none")
    last = [None]

    def _r(e):
        out = []
        if e["group"] != last[0]:
            last[0] = e["group"]
            out.append(f"-- {e['group']}")
        out.append(f"  {e['ref']} [{e['block_type']}] {e['dir']} {e['at']}" + (f" {e['note']}" if e.get("note") else ""))
        return out
    o.rows(r["readers"], _r, total=r["readers_total"])

    if r["unknown_total"]:
        o.line(f"UNKNOWN-DIR ({r['unknown_total']})")
        o.rows(r["unknown"], lambda e: f"{e['ref']} [{e['block_type']}] {e['dir']} {e['at']}" + (f" {e['note']}" if e.get("note") else ""), total=r["unknown_total"])

    o.line(f"IO ({len(r['io'])})")
    if not r["io"]:
        o.line("  none")
    o.rows(r["io"], _io_line)

    e = r["egd"]
    o.line("EGD")
    if not (e["produced"] or e["consumers"] or e["source"]):
        o.line("  none")
    for p in e["produced"]:
        o.line(f"  produced  {p['producer_ctrl']} page {_s(p['page'])} exch {p['exchange_id']} voffs {p['voffs']} "
               f"{_s(p['dtype'])} addr {_s(p['address'])} period {_s((p['period_ns'] or 0) / 1e6)}ms")
    o.rows(e["consumers"], lambda c: f"consumer  {c['consumer_ctrl']} {c['local_name']} exch {c['exchange_id']} voffs {c['voffs']} match {c['match_method']}")
    for s in e["source"]:
        o.line(f"  source    EGD from {s['producer_full_name']} exch {s['exchange_id']} page {_s(s['page'])} voffs {s['voffs']} "
               f"match {s['match_method']}" + ("" if s["producer_in_checkout"] else f" [{s['producer_status']}]"))
    if e["note"]:
        o.line(f"  note      {e['note']}")

    o.line(f"HMI ({len(r['hmi'])})")
    if not r["hmi"]:
        o.line("  none")
    o.rows(r["hmi"], lambda h: f"{h['screen']}  [{','.join(h['sources'])}]" + (f"  menu: {h['menu']}" if h["menu"] else ""))

    a = r["alarm"]
    if a:
        o.line("ALARM")
        _kv(o, "alarm_id", a["alarm_id"])
        cls = a["class"]
        if cls and a.get("class_description"):
            cls = f"{cls} ({a['class_description']}, priority {_s(a.get('class_priority'))})"
        _kv(o, "class", cls)
        _kv(o, "definition", a["definition"])
        _kv(o, "plant_area", a["plant_area"])
        _kv(o, "causes", a["potential_causes"])
        _kv(o, "action", a["operator_action"])
        _kv(o, "consequence", a["consequence"])
        _kv(o, "urgency", a["urgency"])
        if a["normal_severity"] is not None or a["active_severity"] is not None:
            _kv(o, "severity", f"normal {_s(a['normal_severity'])} active {_s(a['active_severity'])}")

    if r.get("alarm_subs"):
        o.line(f"ALARM SUB-PINS ({len(r['alarm_subs'])})   (alarm attributes of this signal; sub-variable = <signal>.<suffix>)")
        for x in r["alarm_subs"]:
            if x["source"]:
                tail = f"<- {x['source']['full_name']}" + (f" (const {x['source']['value']})" if x["source"]["const"] else f" (value {_s(x['source']['value'])})")
            elif x["connection"]:
                tail = f"<- {x['connection']}"
            else:
                tail = f"readers {x['readers']} writers {x['writers']}"
            o.line(f"  {x['suffix']:<8s} {_s(x['datatype']):<5s} {_s(x['dir'])}/{_s(x['dir_source'])}  cls {_s(x['class']):<6s} "
                   f"egd {_s(x['egd_page']):<8s} {tail}" + (f"  alias {x['alias']}" if x["alias"] else ""))
    if r["watch"]:
        o.line(f"WATCH ({len(r['watch'])})")
        o.rows(r["watch"], lambda w: f"{w['ctrl']} {w['watch_file']}" + (f" (datasource {w['datasource']})" if w["datasource"] and w["datasource"] != w["ctrl"] else ""))

    if r["drg"]:
        o.line("DRG")
        o.rows(r["drg"], lambda x: " ".join(t for t in (f"logic_drg {x['logic_drg']}" if x["logic_drg"] else "",
                                                       f"p_id {x['p_id']}" if x["p_id"] else "") if t))
    if r["encrypted"]:
        o.line("ENCRYPTED")
        o.rows(r["encrypted"], lambda p: f"{p}: encrypted program, not traceable")
    if r.get("hidden_ref"):
        o.line("HIDDEN-REF")
        o.rows(r["hidden_ref"], lambda p: f"{p}: ReferencedIn lists this program but no pin of this variable is indexed there "
                                          f"-> the reference sits inside an encrypted block (once seen in the tool, add it to tools/xref_manual.csv)")


def _io_line(p):
    scr = ", ".join(p["screws"]) if p.get("screws") else ""
    if p.get("dir_source") == "V":
        scr = (scr + "  " if scr else "") + "[direction by logic vote]"
    rng = ""
    if p.get("low_value") is not None or p.get("high_value") is not None:
        rng = f" range {_s(p.get('low_value'))}..{_s(p.get('high_value'))}"
    it = f" {p['input_type']}" if p.get("input_type") else ""
    board = f"{p['board']}" + (f"/{p['hw_form']}" if p.get("hw_form") else "") + (f" pos {p['position_r']}" if p.get("position_r") else "")
    return (f"{p['ctrl']} {_s(p['module'])} cab {_s(p['cabinet'])} {board if p.get('board') else '(internal)'} "
            f"{p['point']} tag {_s(p['device_tag'])} {_s(p['direction'])}{it}{rng}"
            + (f" var {p['var']}" if p.get("var") else "")
            + (f" screws {scr}" if scr else "") + f" {p['at']}")


def _trace(o, r):
    o.head(f"TRACE {r['signal']} [{_s(r['datatype'])}]  up={r['up']} down={r['down']} max-lines={r['max_lines']}")
    if r["up"]:
        o.line("UP (<= writers, then their inputs)")
        for ln in r["up_lines"]:
            o.line("  " * (ln["d"] + 1) + ln["text"])
    if r["down"]:
        o.line("DOWN (=> readers, then their outputs)")
        for ln in r["down_lines"]:
            o.line("  " * (ln["d"] + 1) + ln["text"])


def _block(o, r):
    o.head(f"BLOCK {r['ctrl']}/{r['path']}  [{_s(r['block_type'])}] kind {r['block_kind']}" + (" OPAQUE" if r["is_opaque"] else "") + f"  {r['at']}")
    _kv(o, "program", r["program"])
    _kv(o, "task", r["task"])
    _kv(o, "parent", r["parent"])
    _kv(o, "desc", r["description"])
    _kv(o, "version", r["version"])
    _kv(o, "logic_drg", r["logic_drg"])
    _kv(o, "p_id", r["p_id"])
    _kv(o, "device", r["device"])
    _kv(o, "hmi_linked", r["hmi_linked_object"])
    if r.get("library_help") and r["library_help"].get("mht_path"):
        _kv(o, "help", r["library_help"]["mht_path"])
    rc = r.get("recovered") or {}
    if r["is_opaque"]:
        if rc.get("decl") or rc.get("link") or rc.get("pair") or rc.get("xref") or rc.get("vote") or rc.get("print"):
            o.line(f"OPAQUE MACRO: internals encrypted; interface recovered from evidence: {rc.get('print', 0)} from the printed sheets + "
                   f"{rc.get('decl', 0)} declared variables + "
                   f"{rc.get('link', 0)} L: links + {rc.get('pair', 0)} same-number pairing + {rc.get('vote', 0)} 2oo3-voter inference + "
                   f"{rc.get('xref', 0)} verified cross-reference rows "
                   f"(directions G = drawn side of the printed sheet, R = recovered, L = from link, T = hand-verified; "
                   f"the sheets show only wired pins, and pairing and voter rows are inferred)")
        elif r.get("catalogue"):
            o.line("OPAQUE MACRO: interface encrypted; no wiring visible. Library catalogue for this type (names + usage only):")
            o.rows(r["catalogue"], lambda c: f"{c['pin']:<24s} {c['usage']}")
        else:
            o.line("OPAQUE MACRO: interface encrypted; no visible pins and no library catalogue")
    o.line(f"PINS ({len(r['pins'])})")
    o.rows(r["pins"], lambda p: f"{p['pin']:<24s} {p['dir']:<4s} {p['conn_kind']:<2s} {_cut(p['to'], 90)}"
           + (f" usage={p['usage']}" if p["usage"] else "") + (f" value={p['value']}" if p["value"] not in (None, "") else "")
           + (f" alias={p['alias']}" if p["alias"] else "") + (f" [{p['origin']}]" if p.get("origin") else "")
           + (f" {_cut(p['desc'], 50)}" if p.get("desc") else "") + f" {p['at']}")
    if r["attrs"]:
        o.line(f"ATTRS ({len(r['attrs'])})")
        o.rows(r["attrs"], lambda a: f"{a['name']} = {_cut(a['value'], 100)}")
    if r["children"]:
        o.line(f"CHILDREN ({len(r['children'])})")
        o.rows(r["children"], lambda c: f"{c['path']} [{c['block_type'] or c['kind']}]" + (" OPAQUE" if c["is_opaque"] else "")
               + (f" {_cut(c['description'], 60)}" if c["description"] else ""))


def _task(o, r):
    o.head(f"TASK {r['ctrl']}/{r['program']}/{r['task']}  [{r['block_type'] or 'task'}]  blocks {r['n_blocks']}  {r['at']}"
           + ("  PROGRAM ENCRYPTED" if r["program_encrypted"] else ""))
    _kv(o, "logic_drg", r["logic_drg"])
    if r["interface_pins"]:
        o.line(f"INTERFACE PINS ({len(r['interface_pins'])})")
        o.rows(r["interface_pins"], lambda p: f"{p['pin']:<24s} {p['dir']:<4s} {p['conn_kind']:<2s} {_cut(p['to'], 90)}"
               + (f" usage={p['usage']}" if p["usage"] else ""))
    o.line(f"BLOCKS ({r['n_blocks']})")
    o.rows(r["blocks"], lambda b: f"{b['path']} [{b['block_type'] or b['kind']}]" + (" OPAQUE" if b["is_opaque"] else "")
           + f" pins {b['n_pins']}" + (f" drg {b['logic_drg']}" if b["logic_drg"] else "")
           + (f" {_cut(b['description'], 50)}" if b["description"] else "") + f" line {b['line_no']}",
           total=r["n_blocks"])


def _find(o, r):
    what = r.get("what", "var")
    if what == "alarm":
        return _alarm(o, r)
    n = len(r["rows"])
    o.head(f"FIND '{r['pattern']}' kind={what}" + (f" ctrl={r['ctrl']}" if r.get("ctrl") else "") + f"  {n} shown" + (f", +{r['more']} more (--limit)" if r.get("more") else ""))
    if r.get("fts_error"):
        o.line(f"  (fts unavailable: {r['fts_error']})")
    if what == "var":
        o.rows(r["rows"], lambda v: f"{v['full_name']:<38s} {_s(v['datatype']):<7s} {','.join(v['flags']) or '-':<14s} {_cut(v['description'], 60)}"
               + (f"  alias {v['alias']}" if v["alias"] else ""), total=n)
    elif what == "block":
        o.rows(r["rows"], lambda b: f"{b['ctrl']}/{b['path']} [{b['block_type'] or b['bkind']}] {_cut(b['description'], 50)} {b['at']}", total=n)
    elif what == "io":
        o.rows(r["rows"], _io_line, total=n)
    elif what == "screen":
        o.rows(r["rows"], lambda s: f"{s['screen']:<50s} points {s['n_points']}" + (f"  menu: {s['menu']}" if s.get("menu") else ""), total=n)
    if not r["rows"]:
        o.line("  none")


def _io(o, r):
    o.head(f"IO '{r['key']}' matched by {r['matched_by']}: {r['n']} point(s)")
    o.rows(r["rows"], _io_line)


def _egd_ctrl(o, r):
    o.head(f"EGD {r['ctrl']} producer_id {_s(r['producer_id'])}" + (f"  page={r['page']}" if r.get("page") else ""))
    o.line("PAGES")
    o.rows(r["pages"], lambda p: f"{_s(p['page']):<12s} exchanges {p['n_exchanges']:>3d}  vars {p['n_vars']:>6d}")
    o.line(f"EXCHANGES ({len(r['exchanges'])})")
    o.rows(r["exchanges"], lambda x: f"exch {x['exchange_id']:>3d} page {_s(x['page']):<10s} period {x['period_ms']:g}ms len {_s(x['data_length'])} "
           f"vars {x['n_vars']}" + (f" (novar {x['n_novar']})" if x["n_novar"] else "")
           + f" consumers {x['consumers'] or 'none in checkout'}" + (f" ({x['n_consumed']} bindings)" if x["n_consumed"] else "")
           + ("  [DCS export page]" if x["dcs_export"] else ""))
    if r.get("variables") or r.get("page"):
        o.line(f"VARIABLES on page {r['page']} ({len(r['variables'])}{'+' if r.get('variables_more') else ''})")
        o.rows(r["variables"], lambda v: f"exch {v['exchange_id']:>3d} voffs {v['voffs']:>5d} {v['var_name']:<34s} {_s(v['dtype']):<6s} "
               + (f"{_cut(v['description'], 60)}" if v["var_id"] else "[no variable row]"),
               total=len(r["variables"]) + r.get("variables_more", 0))
    o.line("CONSUMED (by producer)")
    if not r["consumed"]:
        o.line("  none")
    o.rows(r["consumed"], lambda c: f"from {c['producer_ctrl']:<10s} vars {c['n']:>5d} exchanges {c['n_exchanges']:>2d}  match both={c['n_both']} name={c['n_name']} voffs={c['n_voffs']} none={c['n_none']}"
           + (f"  [{c['producer_status']}]" if c["n_ext"] == c["n"] else ""))


def _egd_var(o, r):
    o.head(f"EGD {r['signal']}  egd_page {_s(r['egd_page'])}" + (f"  device_name {r['device_name']}" if r.get("device_name") else ""))
    if not (r["produced"] or r["consumers"] or r["source"]):
        o.line("  none")
    for p in r["produced"]:
        o.line(f"  produced  {p['producer_ctrl']} page {_s(p['page'])} exch {p['exchange_id']} voffs {p['voffs']} {_s(p['dtype'])} addr {_s(p['address'])} period {_s((p['period_ns'] or 0) / 1e6)}ms")
    o.rows(r["consumers"], lambda c: f"consumer  {c['consumer_ctrl']} {c['local_name']} exch {c['exchange_id']} voffs {c['voffs']} match {c['match_method']} local_addr {_s(c['local_address'])}")
    for s in r["source"]:
        o.line(f"  source    EGD from {s['producer_full_name']} exch {s['exchange_id']} page {_s(s['page'])} voffs {s['voffs']} match {s['match_method']}"
               + ("" if s["producer_in_checkout"] else f" [{s['producer_status']}]"))
    if r["note"]:
        o.line(f"  note      {r['note']}")


def _screen(o, r):
    o.head(f"SCREEN {r['screen']}  points {r['n_points']} (resolved to variables {r['n_resolved']})")
    for m in r["menu"]:
        o.line(f"  menu   {m['path']}" + (f"  unit {m['unit']}" if m.get("unit") else ""))
    o.rows(r["points"], lambda p: f"{p['full_point']:<40s} " + (f"{_s(p['datatype']):<7s} {_cut(p['description'], 60)}" if p["var"] else "[no variable]")
           + f"  [{','.join(p['sources'])}]", total=r["n_points"])


def _screen_var(o, r):
    o.head(f"SCREENS of {r['signal']}" + (f"  display_screen {r['display_screen']}" if r.get("display_screen") else ""))
    if not r["screens"]:
        o.line("  none")
    o.rows(r["screens"], lambda h: f"{h['screen']}  [{','.join(h['sources'])}]" + (f"  menu: {h['menu']}" if h["menu"] else ""))


def _alarm(o, r):
    o.head(f"ALARM '{r['pattern']}'" + (f" ctrl={r['ctrl']}" if r.get("ctrl") else "") + f"  {len(r['rows'])} shown" + (f", +{r['more']} more (--limit)" if r.get("more") else ""))
    if not r["rows"]:
        o.line("  none")

    def _a(v):
        out = [f"{v['full_name']:<38s} {_s(v['alarm_class']):<8s} {_s(v['alarm_definition']):<10s} {_cut(v['description'], 70)}"]
        extra = " ".join(t for t in (f"area {v['plant_area']}" if v["plant_area"] else "",
                                     f"urgency {v['urgency']}" if v["urgency"] else "") if t)
        if extra:
            out.append(f"    {extra}")
        if v["potential_causes"]:
            out.append(f"    causes: {_cut(v['potential_causes'], 120)}")
        if v["operator_action"]:
            out.append(f"    action: {_cut(v['operator_action'], 120)}")
        return out
    o.rows(r["rows"], _a)


def _where(o, r):
    o.head(f"WHERE {r['target']}")
    o.rows(r["items"], lambda i: f"{i['at']:<44s} {i['what']}", total=len(r["items"]) + r.get("more", 0))


def _lint(o, r):
    o.head("LINT")
    pt = r.get("multi_writer_patterns", {})
    o.line(f"MULTI-WRITER variables: {r['n_multi_writer']} (plain {pt.get('plain', 0)}, duplicate {pt.get('duplicate', 0)}, sfc {pt.get('sfc', 0)}; showing {len(r['multi_writer'])})")
    o.line("  (>= 2 distinct ordinary blocks write it; interface pins and array elements do not count. plain = check these,")
    o.line("   cross-program first; duplicate = same block type with identical inputs in two places; sfc = SFC scaffolding)")
    o.rows(r["multi_writer"], lambda m: [f"{m['full_name']}  [{m['pattern']}] block writers {m['n_block_writers']} in {m['n_programs']} program(s), all writer pins {m['n_writers']}"] + [f"    {w}" for w in m["writers"]]
           + ([f"    ... +{m['writers_more']} more writers (show {m['full_name']} --all)"] if m["writers_more"] else []))
    o.line(f"EGD CONSUMED SIGNATURE != PRODUCER (consumer bound against another exchange layout): {len(r.get('egd_signature', []))}")
    o.rows(r.get("egd_signature", []), lambda x: [f"{x['consumer_ctrl']} <- {x['producer_ctrl']} exch {x['exchange_id']}  sig {_s(x['c_sig'])}/{_s(x['p_sig'])}  length {_s(x['c_len'])}/{_s(x['p_len'])}  vars off {x['n_off']}"]
           + [f"    {v['var_name']} voffs {v['voffs']} match {v['match_method']}" for v in x["vars"]])
    o.line(f"LOGIC WRITER + FIELD INPUT on same variable: {r['n_writer_and_io_input']}")
    o.rows(r["writer_and_io_input"], lambda w: f"{w['full_name']}  io {w['ctrl']} {w['point']} tag {_s(w['device_tag'])}  logic writers {w['n_writers']}")
    o.line(f"MULTIPLE FIELD INPUT POINTS on one variable: {r['n_multi_io_input']}")
    o.rows(r["multi_io_input"], lambda w: f"{w['full_name']}  points {w['n']}")
    o.line(f"PRODUCED IN >1 EGD EXCHANGE: {r['n_multi_egd_produced']}")
    o.rows(r["multi_egd_produced"], lambda w: f"{w['full_name']}  exchanges {w['exchanges']}")
    o.line(f"EGD CONSUMED/PRODUCED MISMATCH (match name-only or voffs-only): {r['n_egd_mismatch']}")
    o.rows(r["egd_mismatch"], lambda m: f"{m['consumer_ctrl']} {m['local_name']} exch {m['exchange_id']} voffs {m['voffs']} match {m['match_method']}")


def _diff_units(o, r):
    o.head(f"DIFF {r['a']} vs {r['b']}  ({r['what']})")
    if r["what"] in ("constants", "all"):
        o.line(f"CONTROL CONSTANTS differing: {r['n_constants']} of {r['n_constants_compared']} compared  (only in {r['a']}: {r['only_a']}, only in {r['b']}: {r['only_b']})")
        o.rows(r["constants"], lambda d: f"{d['name']:<40s} {r['a']}={_s(d['a']):<14s} {r['b']}={_s(d['b']):<14s} {_cut(d['description'], 60)}")
    if r["what"] in ("alarms", "all"):
        o.line(f"ALARM SET-POINTS / DELAYS / HYSTERESIS differing: {r['n_alarms']} of {r['n_alarms_compared']} compared")
        o.rows(r["alarms"], lambda d: f"{d['name']:<40s} {r['a']}: {d['a']}   {r['b']}: {d['b']}")


def _coverage(o, r):
    o.head("COVERAGE")
    o.line("CONTROLLERS (connected = conn_kind V/L/P/D; unknown = direction '?')")
    o.rows(r["controllers"], lambda c: f"{c['ctrl']:<8s} pins {c['n_pins']:>8d}  connected {c['n_connected']:>7d}  unknown {c['n_unknown']:>6d}  ({c['frac_unknown']*100:5.2f}%)"
           + ("  OVER 5% GATE" if c["frac_unknown"] > 0.05 else ""))
    o.line("PROGRAMS")
    o.rows(r["programs"], lambda p: f"{p['ctrl']:<8s} programs {p['n']:>4d}  encrypted {p['n_encrypted']:>3d}  opaque userblocks {p['n_opaque_userblocks']:>5d}")
    if r.get("opaque"):
        t = r["opaque"]
        o.line(f"OPAQUE MACROS: {t['n']} instances / {t['types']} types; interface recovered {t['recovered']}, "
               f"library catalogue only {t['catalogue_only']}, no visible pins {t['none']}")
        o.rows(r.get("opaque_types", [])[:20], lambda x: f"{x['block_type']:<28s} n {x['n']:>5d}  recovered {x['n_recovered']:>5d}  catalogue {x['n_catalogue']:>5d}")
    o.line("UNRESOLVED CONNECTIONS")
    o.rows(r["unresolved"], lambda u: f"{u['ctrl']:<8s} V-no-variable {u['v_unresolved']:>5d}  D-device-pin {u['d_device_pins']:>5d}  L/P-no-target {u['lp_unresolved']:>5d}")
    o.line("PINS BY dir_source: " + "  ".join(f"{k}={v}" for k, v in sorted(r["by_source"].items())))
    o.line("PINS BY direction:  " + "  ".join(f"{k}={v}" for k, v in sorted(r["by_direction"].items())))
    m = r["manual"]
    o.line(f"MANUAL pin_dir_table rows {m['pin_dir_table_rows']}; block types in use {m['block_types_in_use']}, covered by manual {m['block_types_in_manual']}")
    o.line(f"TOP UNKNOWN (block_type, pin) by connected instances ({len(r['top_unknown'])})")
    o.rows(r["top_unknown"], lambda t: f"{t['block_type']:<28s} {t['pin_name']:<36s} {t['n']:>6d}")


def _audit(o, r):
    o.line(f"AUDIT {r['block_type']}" + (f"  ctrl={r['ctrl']}" if r["ctrl"] else "") + f"  blocks {r['n_blocks']}  pin names {r['n_pin_names']}  manual-table pins {r['manual_pins']}")
    o.line(f"  unknown-direction pin names: {r['n_unknown_pin_names']}" + (": " + " ".join(r["unknown_pin_names"][:40]) + (" ..." if r["n_unknown_pin_names"] > 40 else "") if r["unknown_pin_names"] else ""))
    o.line("  pin                       n     I     O     S     ?  src      V     L     P  const     A  none  linked A-link other egd/hmi/io")
    for p in r["pins"]:
        o.line(f"  {p['pin'][:24]:24s} {p['n']:5d} {p['I']:5d} {p['O']:5d} {p['S']:5d} {p['unknown']:5d}  {p['sources']:6s} {p['V']:5d} {p['L']:5d} {p['P']:5d} {p['const']:6d} {p['A']:5d} {p['none']:5d}  {p['linked']:6d} {p['a_linked']:6d} {p['used_by_other_pin']:5d} {p['egd_hmi_io']:10d}")
    if r["pins_more"]:
        o.line(f"  ... +{r['pins_more']} more pin names")


def _print_check(o, r):
    c = r["counts"]
    o.head("PRINT vs INDEX  (read-only: the printed logic sheets change nothing)"
           + (f"  ctrl={r['ctrl']}" if r.get("ctrl") else ""))
    for p in r["pdfs"]:
        if p["gate"] != "pass":
            o.line(f"  REFUSED {p['name']:<14} {p['gate']:<20} {p['note'] or ''}")
    o.line(f"  corpus: {', '.join(r['scanned']) or '(nothing scanned)'}"
           f"   pin rows={c['rows']}  compared={c['compared']}  agree={c['agree']}"
           f" ({100.0 * c['agree'] / max(1, c['compared']):.2f}%)")
    if c.get("promoted"):
        o.line(f"  pins already promoted into the index from these drawings (origin print): {c['promoted']}")
    o.line(f"  blocks drawn={r['blocks_drawn']}   opaque blocks with no indexed pin={r['opaque_zero_pin']}"
           f" of which drawn={r['opaque_zero_pin_drawn']}")
    o.line()
    for key, title in (
            ("xref_tool_conflict", "HAND TABLE (tool-verified) CONTRADICTED - not empty means THIS PARSER is wrong"),
            ("phantom_writer", "PHANTOM WRITER - the index guessed an output, the drawing wires it as an input"),
            ("ghost_reader", "MISSING WRITER - the index guessed an input, the drawing wires it as an output"),
            ("wire_conflict", "WIRE CONFLICT - both name a variable and they differ (guessed pin)"),
            ("xref_soft_conflict", "HAND TABLE (mirrored/inferred) CONTRADICTED - the drawing wins, fix the CSV line"),
            ("xml_conflict", "CONFLICT ON AN XML-STATED PIN - suspect the parser first"),
            ("new_pin", "NEW PIN - the drawing names a pin the indexed block does not have (not promoted)"),
            ("placeholder", "PLACEHOLDER ONLY - the index has the macro's own interface variable, the drawing has the wire")):
        rows = r["sections"].get(key) or []
        o.line(f"{title}: {c.get(key, 0)}")
        for x in rows:
            src = f"[{x.get('origin') or '-'}/{x.get('dir_source') or '-'}]"
            o.line(f"  {x['ctrl']}.{x['block']}.{x['pin']}")
            o.line(f"      {src:<12} drawing {x['print_dir']} {x.get('print_wire') or '(no label)':<30}"
                   f"  index {x.get('index_dir') or '-'} {x.get('index_wire') or '-':<30}"
                   f"  {x['ctrl']}_P.pdf p{x['page']} {x.get('sheet') or ''} {x.get('cell') or ''}")
        if c.get(key, 0) > len(rows):
            o.line(f"  ... +{c[key] - len(rows)} more (--limit / --json)")
        o.line()
    e = r["extra"]
    o.line(f"ENCRYPTED-PROGRAM LOGIC (the drawing is the only source): "
           f"{e['n_encrypted_pins']} pins in {e['n_encrypted']} sheet groups")
    for x in e["encrypted"][:8]:
        o.line(f"  {x['owner']:<70} sheets={x['sheets']:<3} pins={x['pins']}")
    o.line(f"OPAQUE MACRO INTERNALS the index cannot represent: {e['n_internals_pins']} pins "
           f"in {e['n_internals']} groups")
    for x in e["internals"][:8]:
        o.line(f"  {x['owner']:<70} sheets={x['sheets']:<3} pins={x['pins']}")
    o.line(f"SHEETS WHOSE PATH COULD NOT BE RESOLVED: {e['n_unresolved_pins']} pins "
           f"in {e['n_unresolved']} groups")
    for x in e["unresolved"][:6]:
        o.line(f"  [{x['method']}] {x['owner']:<64} pins={x['pins']}")


def _ps_cite(x):
    sh = (x.get("sheet") or "?").strip()
    return f"{x['pdf']} p{x['page']} sheet {sh} {x.get('cell') or ''}".rstrip()


def _ps_where(x):
    if x.get("block_path"):
        return x["block_path"]
    base = (x.get("owner") or x.get("prefix") or x.get("sw_path") or "").replace(".", "/")
    return f"{base}/{x['label']}" if base else f"(sheet path not read)/{x['label']}"


def _ps_dir_word(x):
    return "from" if x["dir"] == "I" else "to"


def _ps_wire(x):
    if x.get("fed_by"):
        return f"(line {_ps_dir_word(x)} {', '.join(x['fed_by'])} per the index" + (
            f"; grey default {x['wire']} shown)" if x["wire_kind"] == "default" else ")")
    if x["wire_kind"] == "default":
        return f"(no variable or constant on this pin; default {x.get('wire')} shown)"
    if x["wire_kind"] == "none" or not x.get("wire"):
        return "(no label: unwired, or a line to another block)"
    return x["wire"]


def _ps_index(x):
    if "index_dir" not in x:
        return x.get("why", "")
    s = f"index {x['index_dir'] or '-'}/{x['index_src'] or '-'} {x.get('index_wire') or '-'}"
    if x.get("index_origin"):
        s += f" [{x['index_origin']}]"
    if x.get("why"):
        s += f"  ({x['why']})"
    return s


def _ps_notes(x):
    n = []
    if x.get("promoted"):
        n.append("promoted into the index from this drawing (origin print / source G)")
    if x.get("paired"):
        n.append("pin name taken from the index " + (x["paired"] if isinstance(x["paired"], str) else "by its wire"))
    if x.get("placed"):
        n.append("sheet not attached by the parser; placed " + x["placed"])
    return n


def _print_show(o, r):
    o.head(f"PRINTED SHEETS  {r['key']}   (drawing evidence; nothing here is written to the index)")
    if r.get("refused"):
        o.line(f"  {r['refused']}")
    else:
        if r["mode"] == "block":
            if r.get("block_path"):
                flags = [x for x, on in (("opaque", r.get("is_opaque")), ("encrypted program", r.get("encrypted"))) if on]
                o.line(f"  index {r.get('block_kind') or 'block'}: {r['block_path']} [{r.get('block_type') or '-'}]"
                       + (f"  ({', '.join(flags)})" if flags else ""))
            elif r.get("matched_by_label"):
                o.line("  not an index block: matched by its drawn label on the sheets of its container")
            elif r.get("container"):
                o.line("  not an index block: a container, listed from its sheets")
        else:
            o.line(f"  index: {r['index_pins']} pins wired to it")
        c = r["counts"]
        if r["total"]:
            o.line(f"  drawn pins: {r['total']}   " + "  ".join(f"{k}={c[k]}" for k in sorted(c)))
        o.line()
        if r["mode"] == "var":
            groups = (("O", "DRAWN WRITERS (pin on the output side)"), ("I", "DRAWN READERS (pin on the input side)"))
        else:
            groups = ((None, "DRAWN PINS"),) if r["total"] else ()
        w = max([len(x["pin"] or "(unnamed)") for x in r["rows"]] + [8])
        for d, title in groups:
            rows = [x for x in r["rows"] if d is None or x["dir"] == d]
            tot = r["total"] if d is None else r["by_dir"].get(d, 0)
            o.line(f"{title}: {tot}" + (f"  ({len(rows)} shown)" if len(rows) < tot else ""))
            for x in rows:
                pin = x["pin"] or "(unnamed)"
                if r["mode"] == "var":
                    head = f"  {_ps_where(x)}.{pin}"
                    if x.get("fed_by"):
                        head += f"   (line {_ps_dir_word(x)} {', '.join(x['fed_by'])} per the index)"
                    if x.get("wire") and x["wire"] != r["key"].split(".", 1)[1]:
                        head += f"   (drawn as {x['wire']})"
                    o.line(head)
                else:
                    o.line(f"  {x['dir']} {pin:<{w}} {_ps_wire(x)}")
                o.line(f"      {x['verdict']:<18} {_ps_index(x)}")
                for n in _ps_notes(x):
                    o.line(f"      {n}")
                o.line(f"      {_ps_cite(x)}")
            o.line()
        if r.get("index_only"):
            o.line(f"INDEX PINS WITH NO DRAWN ROW: {len(r['index_only'])}  (not shown on the block (hidden or "
                   "parameter pins), drawn but not nameable (listed above as '(unnamed)'), or on a sheet the parser "
                   "could not attach; not evidence against the index)")
            for x in r["index_only"][:30]:
                o.line(f"  {x['dir'] or '-'}/{x['src'] or '-'} {x['pin']:<{w}} {x['wire'] or '-'}"
                       + (f" [{x['origin']}]" if x.get("origin") else ""))
            o.line()
        if r.get("index_drawn"):
            o.line(f"INDEX PINS ON THIS VARIABLE, AS THE DRAWING SHOWS THEM: {len(r['index_drawn'])}"
                   "  (the drawn label is another name, or there is none)")
            for x in r["index_drawn"][:30]:
                o.line(f"  {x['dir']} {x['block_path']}.{x['pin']}   drawn: {_ps_wire(x)}")
                o.line(f"      {x['verdict']:<18} {_ps_index(x)}")
                o.line(f"      {_ps_cite(x)}")
            o.line()
        if r.get("index_unmatched"):
            u = r["index_unmatched"]
            o.line(f"INDEX PINS ON THIS VARIABLE WITH NO DRAWN MATCH: {len(u)}  (an unlabelled wire end, a sheet the "
                   "parser could not attach, or a program not printed; not evidence against the index)")
            for x in u[:30]:
                o.line(f"  {x['dir'] or '-'}/{x['src'] or '-'} {x['path']}.{x['pin']}"
                       + (f" [{x['origin']}]" if x.get("origin") else ""))
            if len(u) > 30:
                o.line(f"  ... +{len(u) - 30} more (--json)")
            o.line()
        if r.get("inside"):
            o.line(f"SHEETS DRAWING THE INSIDE OF THIS PATH: {len(r['inside'])}")
            for x in r["inside"][:30]:
                o.line(f"  {x['pdf']} p{x['page']} sheet {(x['sheet'] or '?').strip()}  [{x['method']}]  "
                       f"blocks={x['blocks']} pins={x['pins']}  {x['sw_path'] or ''}")
            if len(r["inside"]) > 30:
                o.line(f"  ... +{len(r['inside']) - 30} more (--json)")
            o.line()
        if r.get("container"):
            o.line(f"BLOCKS DRAWN ON THIS CONTAINER'S SHEETS: {r['container_total']}"
                   + (f"  ({len(r['container'])} shown)" if len(r["container"]) < r["container_total"] else ""))
            for x in r["container"]:
                ex = x["exec"] if x["exec"] is not None else "-"
                o.line(f"  {x['label']:<32} exec={ex:<5} pins={x['pins']:<4} {x['pdf']} p{x['page']} "
                       f"sheet {(x['sheet'] or '?').strip()}  {x['sw_path'] or ''}")
            o.line()
        if not r["total"] and not r.get("inside") and not r.get("container") and not r.get("index_drawn"):
            o.line("  nothing drawn for it on the ingested sheets")
    for e in r.get("egd") or []:
        o.line(f"EGD {e['role'].upper()} {e['var']}: {e['total']} drawn pins" +
               (f"  ({len(e['rows'])} shown)" if len(e["rows"]) < e["total"] else ""))
        for x in e["rows"]:
            o.line(f"  {x['dir']} {_ps_where(x)}.{x['pin'] or '(unnamed)'}   {x['verdict']}   {_ps_cite(x)}")


_DISPATCH = {
    "show": _show, "trace": _trace, "block": _block, "task": _task, "find": _find, "io": _io,
    "egd_ctrl": _egd_ctrl, "egd_var": _egd_var, "screen": _screen, "screen_var": _screen_var, "alarm": _alarm, "diff_units": _diff_units,
    "where": _where, "lint": _lint, "print_check": _print_check, "print_show": _print_show, "coverage": _coverage, "ambiguous": _ambiguous, "notfound": _notfound, "audit": _audit,
}


def render(res, args=None) -> str:
    o = _Out(args)
    kind = res.get("kind") if isinstance(res, dict) else None
    fn = _DISPATCH.get(kind)
    if fn is None:
        if kind == "error":
            return f"ERROR: {res.get('message')}"
        return f"ERROR: unknown result kind {kind!r}"
    fn(o, res)
    return o.text()
