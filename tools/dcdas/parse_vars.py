# -*- coding: utf-8 -*-
"""<CTRL>\\Variables.xml (and EX2100e LocalIOVariables.xml) -> variable table.

Variables.xml root <GlobalVariables> holds one <Variable .../> per controller-global signal (G11: 53,561).
`Connection` is the DECLARATION site (Program.Variable | Program.Task.Pin | Program.Task.Block.Pin), never the
writer. `DeviceName` non-empty marks an EGD consumed copy (name is '<producer>.<name>').
"""
import time
from pathlib import Path
from typing import Iterable, Optional

from lxml import etree

from .db import Batch
from .inventory import Controller, rel_to_root

INSERT_SQL = """INSERT INTO variable(
  ctrl,name,full_name,description,datatype,address,scope,value,decl_connection,decl_program,decl_task,global_prefix,
  egd_page,alias,format_spec,units,disp_low,disp_high,display_screen,control_constant,device_name,referenced_in,
  alarm_id,alarm_class,alarm_definition,plant_area,potential_causes,operator_action,consequence,urgency,
  normal_severity,active_severity,is_program_local,decl_file,decl_line)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(ctrl,name) DO UPDATE SET
  full_name=excluded.full_name, description=excluded.description, datatype=excluded.datatype, address=excluded.address,
  scope=excluded.scope, value=excluded.value, decl_connection=excluded.decl_connection, decl_program=excluded.decl_program,
  decl_task=excluded.decl_task, global_prefix=excluded.global_prefix, egd_page=excluded.egd_page, alias=excluded.alias,
  format_spec=excluded.format_spec, units=excluded.units, disp_low=excluded.disp_low, disp_high=excluded.disp_high,
  display_screen=excluded.display_screen, control_constant=excluded.control_constant, device_name=excluded.device_name,
  referenced_in=excluded.referenced_in, alarm_id=excluded.alarm_id, alarm_class=excluded.alarm_class,
  alarm_definition=excluded.alarm_definition, plant_area=excluded.plant_area, potential_causes=excluded.potential_causes,
  operator_action=excluded.operator_action, consequence=excluded.consequence, urgency=excluded.urgency,
  normal_severity=excluded.normal_severity, active_severity=excluded.active_severity, is_program_local=0,
  decl_file=excluded.decl_file, decl_line=excluded.decl_line"""


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _i(v):
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def split_decl(conn_str: str):
    """'Program.Task.Block.Pin' -> (program, task). 2 parts = Program.Var; 3+ = Program.Task.(...)."""
    if not conn_str:
        return "", ""
    parts = conn_str.split(".")
    if len(parts) >= 3:
        return parts[0], parts[1]
    if len(parts) == 2:
        return parts[0], ""
    return "", ""


def row_from_attrib(ctrl: str, a, decl_file: str, line: int, is_program_local: int = 0):
    name = a.get("Name", "")
    conn_str = a.get("Connection", "")
    prog, task = split_decl(conn_str)
    alarm_id = a.get("Alarm") or a.get("Event") or None
    alarm_def = a.get("AlarmDefinition") or ("Event" if a.get("Event") and not a.get("Alarm") else None)
    return (
        ctrl, name, f"{ctrl}.{name}", a.get("Description"), a.get("DataType"), a.get("Address"),
        a.get("Scope"), a.get("Value"), conn_str or None, prog or None, task or None, a.get("GlobalNamePrefix"),
        a.get("EgdPage"), a.get("Alias"), a.get("FormatSpecification"), a.get("Units"),
        _f(a.get("DisplayLow")), _f(a.get("DisplayHigh")), a.get("DisplayScreen"),
        1 if (a.get("ControlConstant", "").lower() == "true") else 0, a.get("DeviceName"), a.get("ReferencedIn"),
        alarm_id, a.get("AlarmClass"), alarm_def, a.get("PlantArea"), a.get("PotentialCauses"),
        a.get("OperatorAction"), a.get("ConsequenceOfInaction"), a.get("OperatorUrgency"),
        _i(a.get("NormalSeverity")), _i(a.get("ActiveSeverity")), is_program_local, decl_file, line,
    )


def parse_variables_file(conn, ctrl: str, path: Path, root: Path, tag: str = "Variable", log=print) -> int:
    rel = rel_to_root(path, root)
    b = Batch(conn, INSERT_SQL, 5000)
    n = 0
    for _ev, el in etree.iterparse(str(path), events=("end",), tag=tag, huge_tree=True):
        b.add(row_from_attrib(ctrl, el.attrib, rel, el.sourceline))
        n += 1
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    b.flush()
    return n


def run(conn, root: Path, ctrls: Iterable[Controller], log=print) -> dict:
    """Parse Variables.xml (+LocalIOVariables.xml) for each controller. Deletes that controller's rows first."""
    stats = {}
    for c in ctrls:
        t0 = time.time()
        # upsert keeps variable ids stable (pin/io/egd/hmi rows point at them); rows that vanished are deleted below
        before = {r[0] for r in conn.execute("SELECT name FROM variable WHERE ctrl=? AND is_program_local=0", (c.name,))}
        n = 0
        p = c.folder / "Variables.xml"
        if p.exists():
            n += parse_variables_file(conn, c.name, p, root, "Variable", log)
        p2 = c.folder / "LocalIOVariables.xml"
        if p2.exists():
            n += parse_variables_file(conn, c.name, p2, root, "LocalIOVariable", log)
        seen = {r[0] for r in conn.execute("SELECT name FROM variable WHERE ctrl=? AND is_program_local=0", (c.name,))}
        gone = 0
        if before:
            # names present before but not re-inserted now: the parse above touched every current name (upsert),
            # so anything with decl_file older than this run's files is stale -> compare by name set
            current = set()
            for path, tag in ((c.folder / "Variables.xml", "Variable"), (c.folder / "LocalIOVariables.xml", "LocalIOVariable")):
                if path.exists():
                    for _ev, el in etree.iterparse(str(path), events=("end",), tag=tag, huge_tree=True):
                        current.add(el.get("Name", ""))
                        el.clear()
            stale = before - current
            if stale:
                conn.executemany("DELETE FROM variable WHERE ctrl=? AND name=? AND is_program_local=0",
                                 [(c.name, nm) for nm in stale])
                gone = len(stale)
        conn.commit()
        stats[c.name] = n
        log(f"  vars {c.name:7s} {n:7d}  removed {gone:4d}  {time.time()-t0:5.1f}s")
    return stats


def var_index(conn, ctrl: str) -> dict:
    """{name: id} for one controller (used by other parsers to resolve Connection strings)."""
    return {r[0]: r[1] for r in conn.execute("SELECT name,id FROM variable WHERE ctrl=?", (ctrl,))}
