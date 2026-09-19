# -*- coding: utf-8 -*-
"""<CTRL>\\ProducedData.xml + <CTRL>\\ConsumedData.xml -> egd_exchange / egd_produced / egd_consumed.

XML namespace http://geindustrial.com/EGD.
  ProducedData: Producer(Name, ProducerId) -> IPAddress*, Exchange(ExchangeId, SigMajor, PeriodSecs, PeriodNSecs,
                DataLength, Page) -> Destination*, Var(Name, DType, Address?, Writable?, VOffs)
  ConsumedData: Consumer(Name, ProducerId) -> RequiredProducer(Name, ProducerId) -> IPAddress*,
                ConsumedExchange(ExchangeId, SigMajor, PeriodSecs, PeriodNSecs, DataLength, Page) ->
                TransferAddress*, BoundVar(Name, DType, Address, Writable, VOffs)
  BoundVar@Address may be '01005C4E Health' (health-bit binding of the same consumer-side variable); the raw value
  is stored in egd_consumed.local_address, the hex part is used for the (address, device_name) fallback lookup.
  Consumer-side variable is named '<Producer>.<Name>' with DeviceName=<Producer> (parse_vars fills variable).

Per controller (idempotent): delete its egd_exchange (+ egd_produced via exchange_pk) rows and its egd_consumed rows,
re-insert, UPDATE controller.egd_producer_id. egd.xml is read ONLY when a ProducedData Exchange lacks Page (none of
the 15 controllers in the current checkout do).
producer_var_id and match_method are computed in a final pass over ALL egd_consumed rows at the end of run(), since a
consumer's ConsumedData is usually parsed before (or without) its producers' ProducedData.
"""
import time
from pathlib import Path
from typing import Iterable

from lxml import etree

from .db import Batch
from .inventory import Controller
from .parse_vars import var_index

NS = "{http://geindustrial.com/EGD}"
T_PRODUCER, T_EXCHANGE, T_VAR = NS + "Producer", NS + "Exchange", NS + "Var"
T_CONSUMER, T_REQPROD, T_CEXCH, T_BVAR = NS + "Consumer", NS + "RequiredProducer", NS + "ConsumedExchange", NS + "BoundVar"

INSERT_EXCHANGE = """INSERT OR REPLACE INTO egd_exchange(producer_ctrl,exchange_id,page,period_ns,data_length,sig_major)
VALUES(?,?,?,?,?,?)"""
INSERT_PRODUCED = """INSERT OR IGNORE INTO egd_produced(exchange_pk,var_name,dtype,address,voffs,var_id)
VALUES(?,?,?,?,?,?)"""
INSERT_CONSUMED = """INSERT INTO egd_consumed(consumer_ctrl,producer_ctrl,exchange_id,page,var_name,voffs,local_address,
  local_var_id,producer_var_id,match_method) VALUES(?,?,?,?,?,?,?,?,NULL,'none')"""


def _i(v):
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _period_ns(a) -> int:
    return (_i(a.get("PeriodSecs")) or 0) * 1_000_000_000 + (_i(a.get("PeriodNSecs")) or 0)


def _clear(el):
    el.clear()
    parent = el.getparent()
    if parent is not None:
        while el.getprevious() is not None:
            del parent[0]


# ------------------------------------------------------------------------------------------------ ProducedData
def parse_produced(conn, ctrl: str, path: Path, vidx: dict) -> dict:
    """Returns {'exchanges', 'vars', 'unresolved', 'producer_id', 'missing_page': [exchange_id,...]}."""
    st = {"exchanges": 0, "vars": 0, "unresolved": 0, "producer_id": None, "missing_page": []}
    b = Batch(conn, INSERT_PRODUCED, 5000)
    ex_pk = None
    for ev, el in etree.iterparse(str(path), events=("start", "end"), tag=(T_PRODUCER, T_EXCHANGE, T_VAR),
                                  huge_tree=True):
        tag = el.tag
        if ev == "start":
            if tag == T_EXCHANGE:
                a = el.attrib
                exchange_id = _i(a.get("ExchangeId"))
                page = a.get("Page")
                if page is None:
                    st["missing_page"].append(exchange_id)
                cur = conn.execute(INSERT_EXCHANGE, (ctrl, exchange_id, page, _period_ns(a), _i(a.get("DataLength")),
                                                     _i(a.get("SigMajor"))))
                ex_pk = cur.lastrowid
                st["exchanges"] += 1
            elif tag == T_PRODUCER:
                st["producer_id"] = el.get("ProducerId")
            continue
        # end events
        if tag == T_VAR:
            if ex_pk is not None:
                a = el.attrib
                name = a.get("Name", "")
                vid = vidx.get(name)
                if vid is None:
                    st["unresolved"] += 1
                b.add((ex_pk, name, a.get("DType"), a.get("Address"), _i(a.get("VOffs")), vid))
                st["vars"] += 1
            _clear(el)
        elif tag == T_EXCHANGE:
            b.flush()          # keep rows of one exchange together before the pk changes
            ex_pk = None
            _clear(el)
        elif tag == T_PRODUCER:
            _clear(el)
    b.flush()
    return st


def _pages_from_egd_xml(path: Path) -> dict:
    """{exchange_id: page_name} from <CTRL>/egd.xml (Producer -> Produced -> Page(Name) -> Exchange(ExchId)).
    Only used when ProducedData.xml lacks Page on an Exchange."""
    out, page = {}, None
    for ev, el in etree.iterparse(str(path), events=("start", "end"), tag=("Page", "Exchange", "Produced"),
                                  huge_tree=True):
        if ev == "start":
            if el.tag == "Page":
                page = el.get("Name")
            elif el.tag == "Exchange" and page is not None:
                eid = _i(el.get("ExchId"))
                if eid is not None:
                    out[eid] = page
            continue
        if el.tag == "Page":
            page = None
        elif el.tag == "Produced":
            _clear(el)
            break            # Consumed section follows; not needed
        _clear(el)
    return out


# ------------------------------------------------------------------------------------------------ ConsumedData
def parse_consumed(conn, ctrl: str, path: Path, vidx: dict) -> dict:
    """Returns {'producers': {name: n_boundvars}, 'exchanges', 'vars', 'unresolved_local', 'consumer_id'}."""
    st = {"producers": {}, "exchanges": 0, "vars": 0, "unresolved_local": 0, "consumer_id": None}
    addr_idx = None          # lazy {(address, device_name): id} fallback
    b = Batch(conn, INSERT_CONSUMED, 5000)
    producer, exchange_id, page = None, None, None
    for ev, el in etree.iterparse(str(path), events=("start", "end"),
                                  tag=(T_CONSUMER, T_REQPROD, T_CEXCH, T_BVAR), huge_tree=True):
        tag = el.tag
        if ev == "start":
            if tag == T_REQPROD:
                producer = el.get("Name", "")
                st["producers"].setdefault(producer, 0)
            elif tag == T_CEXCH:
                exchange_id = _i(el.get("ExchangeId"))
                page = el.get("Page")
                st["exchanges"] += 1
            elif tag == T_CONSUMER:
                st["consumer_id"] = el.get("ProducerId")
            continue
        if tag == T_BVAR:
            if producer is not None and exchange_id is not None:
                a = el.attrib
                name = a.get("Name", "")
                address = a.get("Address")
                lvid = vidx.get(f"{producer}.{name}")
                if lvid is None and address:
                    if addr_idx is None:
                        addr_idx = {(r[0], r[1]): r[2] for r in conn.execute(
                            "SELECT address,device_name,id FROM variable WHERE ctrl=? AND device_name IS NOT NULL "
                            "AND device_name<>'' AND address IS NOT NULL", (ctrl,))}
                    lvid = addr_idx.get((address.split()[0], producer))
                if lvid is None:
                    st["unresolved_local"] += 1
                b.add((ctrl, producer, exchange_id, page, name, _i(a.get("VOffs")), address, lvid))
                st["vars"] += 1
                st["producers"][producer] += 1
            _clear(el)
        elif tag == T_CEXCH:
            exchange_id, page = None, None
            _clear(el)
        elif tag == T_REQPROD:
            producer = None
            _clear(el)
        elif tag == T_CONSUMER:
            _clear(el)
    b.flush()
    return st


# ------------------------------------------------------------------------------------------------- final pass
def finalize(conn, log=print) -> dict:
    """producer_var_id + match_method for ALL egd_consumed rows (independent of parse order)."""
    t0 = time.time()
    conn.execute("UPDATE egd_consumed SET producer_var_id=(SELECT v.id FROM variable v "
                 "WHERE v.ctrl=egd_consumed.producer_ctrl AND v.name=egd_consumed.var_name)")
    by_key, names = {}, set()
    for prod, eid, voffs, name in conn.execute(
            "SELECT e.producer_ctrl,e.exchange_id,p.voffs,p.var_name FROM egd_produced p "
            "JOIN egd_exchange e ON e.id=p.exchange_pk"):
        by_key[(prod, eid, voffs)] = name
        names.add((prod, name))
    rows, counts = [], {"both": 0, "name": 0, "voffs": 0, "none": 0}
    for cid, prod, eid, voffs, name in conn.execute(
            "SELECT id,producer_ctrl,exchange_id,voffs,var_name FROM egd_consumed"):
        vname = by_key.get((prod, eid, voffs))
        has_name = (prod, name) in names
        if vname is not None and vname == name:
            m = "both"
        elif has_name:
            m = "name"
        elif vname is not None:
            m = "voffs"
        else:
            m = "none"
        counts[m] += 1
        rows.append((m, cid))
    conn.executemany("UPDATE egd_consumed SET match_method=? WHERE id=?", rows)
    conn.commit()
    known = {r[0] for r in conn.execute("SELECT name FROM controller")}
    ext = [r[0] for r in conn.execute("SELECT DISTINCT producer_ctrl FROM egd_consumed ORDER BY 1") if r[0] not in known]
    n_pv = conn.execute("SELECT count(*) FROM egd_consumed WHERE producer_var_id IS NOT NULL").fetchone()[0]
    log(f"  match   {len(rows):7d} rows  both={counts['both']} name={counts['name']} voffs={counts['voffs']} "
        f"none={counts['none']}  producer_var_id={n_pv}  {time.time()-t0:5.1f}s")
    if ext:
        log(f"  producers without a controller folder: {' '.join(ext)}")
    counts["external_producers"] = ext
    return counts


# -------------------------------------------------------------------------------------------------------- run
def run(conn, root: Path, ctrls: Iterable[Controller], log=print) -> dict:
    stats = {}
    for c in ctrls:
        t0 = time.time()
        conn.execute("DELETE FROM egd_produced WHERE exchange_pk IN (SELECT id FROM egd_exchange WHERE producer_ctrl=?)",
                     (c.name,))
        conn.execute("DELETE FROM egd_exchange WHERE producer_ctrl=?", (c.name,))
        conn.execute("DELETE FROM egd_consumed WHERE consumer_ctrl=?", (c.name,))
        vidx = var_index(conn, c.name)
        ps = {"exchanges": 0, "vars": 0, "unresolved": 0, "producer_id": None, "missing_page": []}
        cs = {"producers": {}, "exchanges": 0, "vars": 0, "unresolved_local": 0, "consumer_id": None}
        p = c.folder / "ProducedData.xml"
        if p.exists():
            ps = parse_produced(conn, c.name, p, vidx)
            if ps["missing_page"]:
                egd = c.folder / "egd.xml"
                pages = _pages_from_egd_xml(egd) if egd.exists() else {}
                fixed = 0
                for eid in ps["missing_page"]:
                    if eid in pages:
                        conn.execute("UPDATE egd_exchange SET page=? WHERE producer_ctrl=? AND exchange_id=? AND page IS NULL",
                                     (pages[eid], c.name, eid))
                        fixed += 1
                log(f"  egd.xml {c.name:7s} {len(ps['missing_page'])} exchange(s) lacked Page; {fixed} filled from egd.xml")
        p = c.folder / "ConsumedData.xml"
        if p.exists():
            cs = parse_consumed(conn, c.name, p, vidx)
        pid = ps["producer_id"] or cs["consumer_id"]
        if pid:
            conn.execute("UPDATE controller SET egd_producer_id=? WHERE name=?", (pid, c.name))
        conn.commit()
        stats[c.name] = {"exchanges": ps["exchanges"], "produced": ps["vars"], "produced_unresolved": ps["unresolved"],
                         "consumed_exchanges": cs["exchanges"], "consumed": cs["vars"],
                         "consumed_unresolved_local": cs["unresolved_local"], "producers": cs["producers"],
                         "producer_id": pid}
        log(f"  egd  {c.name:7s} ex {ps['exchanges']:3d} prod {ps['vars']:6d} (novar {ps['unresolved']:5d})  "
            f"cons {cs['vars']:5d} from {len(cs['producers']):2d} producers (nolocal {cs['unresolved_local']:3d})  "
            f"{time.time()-t0:5.1f}s")
    stats["_match"] = finalize(conn, log)
    # RequiredProducer names seen in this run (incl. those with zero BoundVar) that have no controller folder
    seen = set()
    for s in stats.values():
        seen.update(s.get("producers", {}))
    known = {r[0] for r in conn.execute("SELECT name FROM controller")}
    ext = sorted(seen - known)
    if ext:
        log(f"  RequiredProducer names without a controller folder (this run): {' '.join(ext)}")
    stats["_match"]["required_producers_external"] = ext
    return stats
