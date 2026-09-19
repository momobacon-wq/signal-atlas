# -*- coding: utf-8 -*-
"""<CTRL>\\DistributedIO.Xml -> io_module / io_board / io_point / io_screw.

Tree (verified on the checkout):
  DistributedIO -> LanModules/LanModule(Name, ModuleId, GroupName=cabinet, BarCodeR, IoRedundancy, LibraryVersion,
      SharedIONet{R,S,T}PortAIPAddress) -> IoPacks/IoPack(PortAIPAddress ...), Parameters/Parameter,
      InternalPoints/Point, TerminalBoards/TerminalBoard(Name, HardwareForm, PositionInGroupR, TerminalBoardBarCode)
      -> TerminalBoardPoints/Point(Name, Connection, Address, DeviceTag) -> Screw*, Jumper*, Sense*, Parameter*.
  Extra shapes seen only in G11/G12:
    * LanModule -> Ports/Port -> LanModule  (CANopen devices hanging off a PCNO pack: "WoodwardDVP") — indexed as
      their own io_module named "<parent>/<Port Name>" (all share the parent's ModuleId).
    * LanModule -> Ports/Port -> ModbusMaster -> ModbusMasterStation -> Page -> Point(Direction=Read|Write ...)
      (PSCA serial Modbus) — indexed as io_point with signal_type='modbus'.
    * FF* elements (Foundation Fieldbus: FFSegment/FFH1FieldDevice/FFBlock/FFParameter) — skipped with their subtree.
  Screws that are not under a Point (TerminalBoard/ExtraCircuit/Screw, TerminalBoard/Screw, Port/Screws/Screw) are
  power/common screws without a signal and are not indexed.

Direction: from the Point name (spec prefixes) refined by the terminal-board family for boards whose point names
are not self-describing (TCSA, TRPG, TTUR, TSVC, TVBA, TCDM, SCSA). Everything else is '?'.
"""
import json
import re
import time
from pathlib import Path
from typing import Iterable

from lxml import etree

from .db import Batch
from .inventory import Controller, rel_to_root
from .parse_vars import var_index

MODULE_SQL = """INSERT INTO io_module(id,ctrl,name,module_id,cabinet,io_redundancy,ip_r,ip_s,ip_t,barcode_r,
  library_version,line_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"""
BOARD_SQL = "INSERT INTO io_board(id,module_id,name,hw_form,position_r,barcode,line_no) VALUES(?,?,?,?,?,?,?)"
POINT_SQL = """INSERT INTO io_point(id,ctrl,module_id,board_id,name,direction,signal_type,connection,var_id,
  device_tag,address,input_type,low_value,high_value,params_json,line_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
SCREW_SQL = """INSERT INTO io_screw(point_id,name,number,cable_number,wire_number,interposing_tb,jumpers,note)
  VALUES(?,?,?,?,?,?,?,?)"""

# ---------------------------------------------------------------------------------------------- direction rules
# Generic rules from the spec (Point-name prefix). Checked after the family rules below.
_GENERIC = [
    (re.compile(r"^NotUsed"), "?"),
    (re.compile(r"^(Relay|AnalogOutput|Servo|Output|Solenoid)"), "O"),
    (re.compile(r"^(AnalogInput|Contact|Input|RTD|TC|ThermoCouple|Thermocouple|Pulse|Speed|Vib|VIB|Prox)"), "I"),
]
# Terminal-board family rules for names that are ambiguous or board-specific (checked first, only for TB points).
_FAMILY = {
    "TCSA": [(re.compile(r"^TCSA_Relay"), "O"), (re.compile(r"^K\d+$"), "O"),
             (re.compile(r"^(K\d+_Fdbk|TCSA_Contact|FD\d|FlameInd|FlameAnalogInput|ESTOP|BusPT|GenPT|PulseRate)"),
              "I")],
    "TRPG": [(re.compile(r"^(Kq\d+_Status|FlameInd)"), "I"), (re.compile(r"^Kq\d+$"), "O")],
    "TTUR": [(re.compile(r"^(BusPT|GenPT|Ckt_Bkr|ShCurrMon|ShVoltMon|PulseRate)"), "I")],
    "TSVC": [(re.compile(r"^ServoOutput"), "O"), (re.compile(r"^FlowRate"), "I")],
    "TVBA": [(re.compile(r"^(GAP\d|VIB\d)"), "I")],
    "TCDM": [(re.compile(r"^SIG\d"), "I")],
    "SCSA": [(re.compile(r"^SCSA_Relay\d+Fdbk"), "I"), (re.compile(r"^SCSA_Relay"), "O"),
             (re.compile(r"^(SCSA_Contact|ColdJunction|Thermocouple|AnalogInput)"), "I")],
    "STTC": [(re.compile(r"^(ColdJunction|Thermocouple)"), "I")],
}
# Last resort for terminal-board points: the channel's own configuration parameters say what it is.
_PARAM_O = ("RelayOutput", "PTR_Output", "Output_State")
_PARAM_I = ("ContactInput",)
_PREFIX_RE = re.compile(r"^[A-Za-z]+")


def direction_of(name: str, board: str = None, params: dict = None) -> str:
    if board and board in _FAMILY:
        for rx, d in _FAMILY[board]:
            if rx.match(name):
                return d
    for rx, d in _GENERIC:
        if rx.match(name):
            return d
    if params and board:
        if any(k in params for k in _PARAM_O):
            return "O"
        if any(k in params for k in _PARAM_I):
            return "I"
    return "?"


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


def _nz(v):
    """Empty string -> None."""
    return v if v not in (None, "") else None


def _ip(v):
    return v if v and v != "0.0.0.0" else None


class _Ids:
    """Explicit primary keys so batched io_point rows can be referenced by io_screw without lastrowid."""

    def __init__(self, conn):
        self.module = conn.execute("SELECT coalesce(max(id),0) FROM io_module").fetchone()[0]
        self.board = conn.execute("SELECT coalesce(max(id),0) FROM io_board").fetchone()[0]
        self.point = conn.execute("SELECT coalesce(max(id),0) FROM io_point").fetchone()[0]


def _module_ips(el):
    return (_ip(el.get("SharedIONetRPortAIPAddress")), _ip(el.get("SharedIONetSPortAIPAddress")),
            _ip(el.get("SharedIONetTPortAIPAddress")))


def _point_row(ctrl, el, kind, mod_id, board_id, board_name, var_idx, pid):
    """Build the io_point row + its screws from a fully-parsed <Point> element."""
    name = el.get("Name", "")
    connection = _nz(el.get("Connection"))
    params, screws = {}, []
    input_type = low = high = None
    low_in = high_in = None
    for c in el:
        t = c.tag
        if t == "Parameter":
            n, v = c.get("Name", ""), c.get("Value")
            if n == "InputType":
                input_type = v
            elif n == "Low_Value":
                low = _f(v)
            elif n == "High_Value":
                high = _f(v)
            elif n == "Low_Input":
                low_in = _f(v)
            elif n == "High_Input":
                high_in = _f(v)
            else:
                params[n] = v
        elif t == "Screw":
            screws.append((pid, c.get("Name"), _i(c.get("Number")), _nz(c.get("CableNumber")),
                           _nz(c.get("WireNumber")), _nz(c.get("InterposingTB")), None, _nz(c.get("Note"))))
        elif t in ("Jumper", "Sense"):
            params[c.get("Name", t)] = c.get("SelectedValue")
    if low is None:
        low = low_in
    if high is None:
        high = high_in
    if kind == "tb":
        m = _PREFIX_RE.match(name)
        signal_type = m.group(0) if m else name
        direction = direction_of(name, board_name, params)
    elif kind == "modbus":
        signal_type = "modbus"
        d = el.get("Direction", "")
        direction = "I" if d == "Read" else "O" if d == "Write" else "?"
        for k in ("Direction", "PointAddress", "RemDataType", "MasterPointDataType", "UpdateRate", "EngMin",
                  "EngMax", "RawMin", "RawMax", "BitNumber"):
            if el.get(k) not in (None, ""):
                params[k] = el.get(k)
        low, high = _f(el.get("EngMin")), _f(el.get("EngMax"))
    else:
        signal_type = "internal"
        direction = direction_of(name)
    for k in ("Description", "C2CConnectedVariableDescription"):
        if el.get(k):
            params[k] = el.get(k)
    row = (pid, ctrl, mod_id, board_id, name, direction, signal_type, connection,
           var_idx.get(connection) if connection else None, _nz(el.get("DeviceTag")), _nz(el.get("Address")),
           input_type, low, high, json.dumps(params, ensure_ascii=False, separators=(",", ":")) if params else None,
           el.sourceline)
    return row, screws


def parse_io_file(conn, ctrl: str, path: Path, root: Path, var_idx: dict, ids: _Ids, log=print) -> dict:
    """Stream one DistributedIO.Xml. Returns counts {'modules','boards','points','screws','ff_skipped'}."""
    n = {"modules": 0, "boards": 0, "points": 0, "screws": 0, "ff_skipped": 0, "unresolved": 0}
    pts = Batch(conn, POINT_SQL, 5000)
    scr = Batch(conn, SCREW_SQL, 5000)
    mod_stack = []            # [(module_pk, module_name, ips_known)]
    port_stack = []           # Port names (nested LanModule naming)
    board_id = board_name = None
    kind = None               # 'tb' | 'internal' | 'modbus' while inside a point list
    ff = 0                    # depth inside a skipped FF* subtree
    pack_ips = []             # IoPack PortAIPAddress in document order for the current module
    dir_hist = {}
    for ev, el in etree.iterparse(str(path), events=("start", "end"), huge_tree=True):
        tag = el.tag
        if ev == "start":
            if ff:
                ff += 1
                continue
            if tag.startswith("FF"):
                ff = 1
                n["ff_skipped"] += 1
                continue
            if tag == "LanModule":
                ids.module += 1
                name = el.get("Name", "")
                if mod_stack and port_stack:       # nested device under a Port
                    name = f"{mod_stack[-1][1]}/{port_stack[-1]}"
                ip_r, ip_s, ip_t = _module_ips(el)
                conn.execute(MODULE_SQL, (ids.module, ctrl, name, _nz(el.get("ModuleId")), _nz(el.get("GroupName")),
                                          _nz(el.get("IoRedundancy")), ip_r, ip_s, ip_t, _nz(el.get("BarCodeR")),
                                          _nz(el.get("LibraryVersion")), el.sourceline))
                mod_stack.append((ids.module, name, bool(ip_r or ip_s or ip_t)))
                pack_ips = []
                n["modules"] += 1
            elif tag == "IoPack":
                pack_ips.append(_ip(el.get("PortAIPAddress")))
            elif tag == "Port":
                port_stack.append(el.get("Name", ""))
            elif tag == "TerminalBoard":
                ids.board += 1
                board_id, board_name = ids.board, el.get("Name", "")
                conn.execute(BOARD_SQL, (board_id, mod_stack[-1][0] if mod_stack else None, board_name,
                                         _nz(el.get("HardwareForm")), _nz(el.get("PositionInGroupR")),
                                         _nz(el.get("TerminalBoardBarCode")), el.sourceline))
                n["boards"] += 1
            elif tag == "TerminalBoardPoints":
                kind = "tb"
            elif tag == "InternalPoints":
                kind = "internal"
            elif tag == "Page":
                kind = "modbus"
            continue
        # ---- end events
        if ff:
            ff -= 1
            if ff == 0:
                el.clear()
                while el.getprevious() is not None:
                    del el.getparent()[0]
            continue
        if tag == "Point":
            if kind and mod_stack:
                ids.point += 1
                row, screws = _point_row(ctrl, el, kind, mod_stack[-1][0], board_id if kind == "tb" else None,
                                         board_name if kind == "tb" else None, var_idx, ids.point)
                pts.add(row)
                for s in screws:
                    scr.add(s)
                n["points"] += 1
                n["screws"] += len(screws)
                if row[7] and row[8] is None:
                    n["unresolved"] += 1
                if kind == "tb":
                    k = (board_name, row[6], row[5])
                    dir_hist[k] = dir_hist.get(k, 0) + 1
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]
        elif tag in ("TerminalBoardPoints", "InternalPoints", "Page"):
            kind = None
        elif tag == "TerminalBoard":
            board_id = board_name = None
            el.clear()
        elif tag == "IoPacks":
            if mod_stack and not mod_stack[-1][2] and any(pack_ips):
                ips = (pack_ips + [None, None, None])[:3]
                conn.execute("UPDATE io_module SET ip_r=?,ip_s=?,ip_t=? WHERE id=?", (*ips, mod_stack[-1][0]))
                mod_stack[-1] = (mod_stack[-1][0], mod_stack[-1][1], True)
        elif tag == "Port":
            if port_stack:
                port_stack.pop()
        elif tag == "LanModule":
            if mod_stack:
                mod_stack.pop()
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]
        elif tag in ("IoNetExchangeClass2", "Parameters", "ExtraCircuit", "CaptureBuffers", "Screws", "Jumpers"):
            el.clear()          # bulky non-signal subtrees: free them before the enclosing LanModule ends
    pts.flush()
    scr.flush()
    n["dir_hist"] = dir_hist
    return n


def run(conn, root: Path, ctrls: Iterable[Controller], log=print) -> dict:
    """Parse DistributedIO.Xml per controller. Deletes that controller's io_* rows first (idempotent)."""
    stats = {}
    ids = _Ids(conn)
    for c in ctrls:
        t0 = time.time()
        conn.execute("DELETE FROM io_screw WHERE point_id IN (SELECT id FROM io_point WHERE ctrl=?)", (c.name,))
        conn.execute("DELETE FROM io_board WHERE module_id IN (SELECT id FROM io_module WHERE ctrl=?)", (c.name,))
        conn.execute("DELETE FROM io_point WHERE ctrl=?", (c.name,))
        conn.execute("DELETE FROM io_module WHERE ctrl=?", (c.name,))
        p = c.folder / "DistributedIO.Xml"
        if not p.exists():
            conn.commit()
            stats[c.name] = None
            log(f"  io   {c.name:7s} (no DistributedIO.Xml)")
            continue
        var_idx = var_index(conn, c.name)
        n = parse_io_file(conn, c.name, p, root, var_idx, ids, log)
        conn.commit()
        stats[c.name] = n
        log(f"  io   {c.name:7s} mod {n['modules']:4d} tb {n['boards']:4d} pt {n['points']:6d} screw {n['screws']:6d}"
            f"  unresolved-conn {n['unresolved']:5d}  ff-skipped {n['ff_skipped']:5d}  {time.time()-t0:5.1f}s")
    return stats
