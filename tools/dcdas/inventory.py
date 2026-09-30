# -*- coding: utf-8 -*-
"""Controller / source-file inventory and staleness detection.

- controllers(): folders under the checkout whose Device.xml class is one of the four CONTROLLER classes
  (DEVICE_KINDS: controller / safety / exciter / drive), with the Device.xml root attributes
  (ProductVersion, Redundancy, MajorRev, MinorRev, LastModificationTime, Platform) read via lxml
  (the Description attribute is a multi-KB revision history, so no regex on raw text).
- nodes(): every other Device.xml folder (HMI workstations and thin clients, servers and VMs, network switches and
  time servers, external devices). Only the class token of the file's processing instruction is read: their
  Device.xml bodies carry addresses, host names, SNMP secrets and personnel fields, none of which may be indexed.
- source_files(): the files the extractor consumes, per controller and project-wide, with size/mtime
  (for a node: Device.xml and ProducedData.xml only).
- head_info(): the <?GeCssClass ... Version Coherency?> processing instruction of a the configuration tool XML.
"""
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from lxml import etree

from .db import src_root

LOGIC_GLOB = "_*.xml"
CTRL_FILES = ["Variables.xml", "DistributedIO.Xml", "ProducedData.xml", "ConsumedData.xml", "egd.xml",
              "Device.xml", "SymbolTable.xml", "LocalIOVariables.xml"]
ROOT_FILES = ["FormatSpecifications.xml", "AlarmClasses.xml", "AlarmDefinitions.xml",
              "PlantAreas.xml", "HmiScreenFiles.xml"]
HMI_FILES = [Path("HmiScreens") / "navigation" / "tp_actPt_navPointSearchDbStd.csv",
             Path("HmiScreens") / "navigation" / "CIMNavigationMenuItemsStd.csv"]

_PI_RE = re.compile(r'<\?GeCssClass\s+type="([^"]*)"(?:\s+Version="([^"]*)")?(?:\s+Coherency="([^"]*)")?')


@dataclass
class Controller:
    name: str
    folder: Path
    kind: str = ""              # controller | safety | exciter | drive  (DEVICE_KINDS)
    product_version: str = ""
    redundancy: str = ""
    platform: str = ""
    major_rev: str = ""
    minor_rev: str = ""
    last_mod: str = ""
    coherency: str = ""
    libraries: List[dict] = field(default_factory=list)   # BlockLibraries/Library entries
    programs: List[dict] = field(default_factory=list)    # ProgramList/ProgramGroup/Program entries


# Device.xml class token (between the last '.' and the first ',' of the PI type) -> kind. The controller classes are
# matched by suffix; everything else is a node. An unknown class is a node too, unless the folder holds logic or
# variables (then it is indexed as a controller, with a warning), so nothing is ever dropped or guessed silently.
CONTROLLER_KINDS = ("controller", "safety", "exciter", "drive")
NODE_KINDS = {"WorkstationDevice": "hmi", "ThinClientDevice": "hmi",
              "VirtualMachineDevice": "server", "VirtualizationServerDevice": "server",
              "VirtualizationServerSetDevice": "server", "VirtualFieldAgentDevice": "server",
              "NetworkSwitchDevice": "network", "NetworkTimeServerDevice": "network",
              "ExternalDevice": "external"}
NODE_FILES = {"Device.xml": "device", "ProducedData.xml": "egd_produced"}


def class_token(cls: str) -> str:
    return cls.split(",", 1)[0].rsplit(".", 1)[-1].strip()


def device_kind(tok: str) -> Optional[str]:
    """Controller kind of a Device.xml class token, or None for a node."""
    if tok.endswith("VIeSDevice"):
        return "safety"
    if tok.endswith("VIeDevice"):
        return "controller"
    if tok.startswith("EX2100"):
        return "exciter"
    if tok.startswith("LS2100"):
        return "drive"
    return None


@dataclass
class Node:
    """A Device.xml folder that is not a controller: registered by name and class only (no variables, no logic)."""
    name: str
    folder: Path
    kind: str                   # hmi | server | network | external | other
    device_class: str


def head_info(path: Path) -> dict:
    """Return {'class','version','coherency'} from the GeCssClass PI (first 2 KB)."""
    with open(path, "rb") as f:
        head = f.read(2048).decode("utf-8", "replace")
    m = _PI_RE.search(head)
    if not m:
        return {"class": "", "version": "", "coherency": ""}
    return {"class": m.group(1) or "", "version": m.group(2) or "", "coherency": m.group(3) or ""}


def _device_xml(ctrl: Controller):
    """Read Device.xml root attributes + ProgramList + BlockLibraries with iterparse (file is < 100 KB)."""
    p = ctrl.folder / "Device.xml"
    hi = head_info(p)
    ctrl.coherency = hi["coherency"]
    ctrl.kind = device_kind(class_token(hi["class"])) or "controller"
    group = None
    for ev, el in etree.iterparse(str(p), events=("start", "end"), huge_tree=True):
        tag = el.tag
        if ev == "start" and el.getparent() is None:
            a = el.attrib
            ctrl.product_version = a.get("ProductVersion", "")
            ctrl.redundancy = a.get("Redundancy", "")
            ctrl.platform = a.get("Platform", "")
            ctrl.major_rev = a.get("MajorRev", "")
            ctrl.minor_rev = a.get("MinorRev", "")
            ctrl.last_mod = a.get("LastModificationTime", "")
        elif ev == "start" and tag == "ProgramGroup":
            group = el.get("Name", "")
        elif ev == "end" and tag == "Program":
            ctrl.programs.append({"group": group or "", "name": el.get("Name", ""), "file": el.get("File", ""),
                                  "library_type": el.get("LibraryType", ""), "unlink": el.get("Unlink", "")})
        elif ev == "end" and tag == "Library":
            ctrl.libraries.append({"name": el.get("Name", ""), "type": el.get("Type", ""),
                                   "file": el.get("File", ""), "dir": el.get("Dir", "")})
        if ev == "end" and tag in ("Program", "Library", "LanModule"):
            el.clear()
    return ctrl


_WARNED = set()


def _split_devices(root: Path):
    """-> ([controller folders], [Node]) from one scan; the node pass reads the first 2 KB of Device.xml only."""
    ctrl_dirs, nodes_ = [], []
    for d in sorted(root.iterdir()):
        if not (d.is_dir() and (d / "Device.xml").exists()):
            continue
        tok = class_token(head_info(d / "Device.xml")["class"])
        if device_kind(tok):
            ctrl_dirs.append(d)
        elif tok in NODE_KINDS:
            nodes_.append(Node(d.name, d, NODE_KINDS[tok], tok))
        elif (d / "Variables.xml").exists() or any(d.glob(LOGIC_GLOB)):
            if d.name not in _WARNED:
                _WARNED.add(d.name)
                print(f"  WARN {d.name}: unknown device class {tok!r} with logic/variables: indexed as a controller", file=sys.stderr)
            ctrl_dirs.append(d)
        else:
            if d.name not in _WARNED:
                _WARNED.add(d.name)
                print(f"  WARN {d.name}: unknown device class {tok!r}: registered as a node", file=sys.stderr)
            nodes_.append(Node(d.name, d, "other", tok))
    return ctrl_dirs, nodes_


def controllers(root: Optional[Path] = None) -> List[Controller]:
    """The indexed controllers (four controller classes); nodes are never returned."""
    root = root or src_root()
    return [_device_xml(Controller(name=d.name, folder=d)) for d in _split_devices(root)[0]]


def nodes(root: Optional[Path] = None) -> List[Node]:
    """The non-controller Device.xml folders (registry only)."""
    return _split_devices(root or src_root())[1]


def library_folders(root: Optional[Path] = None) -> List[Path]:
    root = root or src_root()
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "Library.xml").exists())


@dataclass
class SourceFile:
    path: Path
    rel: str
    ctrl: str      # controller name, or '' for project-wide, or library folder name for libraries
    kind: str      # logic | variables | io | egd_produced | egd_consumed | egd | device | symbols | localio | watch | root | hmi | library
    size: int
    mtime: float


def _sf(root: Path, p: Path, ctrl: str, kind: str) -> SourceFile:
    st = p.stat()
    return SourceFile(p, p.relative_to(root).as_posix(), ctrl, kind, st.st_size, st.st_mtime)


def source_files(root: Optional[Path] = None, ctrls: Optional[List[str]] = None,
                 include_libraries: bool = True) -> Iterator[SourceFile]:
    root = root or src_root()
    for c in controllers(root):
        if ctrls and c.name not in ctrls:
            continue
        for p in sorted(c.folder.glob(LOGIC_GLOB)):
            yield _sf(root, p, c.name, "logic")
        kinds = {"Variables.xml": "variables", "DistributedIO.Xml": "io", "ProducedData.xml": "egd_produced",
                 "ConsumedData.xml": "egd_consumed", "egd.xml": "egd", "Device.xml": "device",
                 "SymbolTable.xml": "symbols", "LocalIOVariables.xml": "localio"}
        for fn, kind in kinds.items():
            p = c.folder / fn
            if p.exists():
                yield _sf(root, p, c.name, kind)
        wd = c.folder / "Watches"
        if wd.is_dir():
            for p in sorted(wd.glob("*.Watch")):
                yield _sf(root, p, c.name, "watch")
    if ctrls:
        return
    for n in nodes(root):               # nodes: only what the build reads (never ConsumedData / SymbolTable / egd.xml)
        for fn, kind in NODE_FILES.items():
            p = n.folder / fn
            if p.exists():
                yield _sf(root, p, n.name, kind)
    for p in sorted(root.glob("*.tcw")):
        yield _sf(root, p, "", "root")
    for fn in ROOT_FILES:
        p = root / fn
        if p.exists():
            yield _sf(root, p, "", "root")
    for rel in HMI_FILES:
        p = root / rel
        if p.exists():
            yield _sf(root, p, "", "hmi")
    if include_libraries:
        for d in library_folders(root):
            for p in sorted(d.glob("_*.xml")):
                yield _sf(root, p, d.name, "library")
            for p in sorted(d.glob("*.mht")):
                yield _sf(root, p, d.name, "mht")


def sha1_of(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def rel_to_root(path: Path, root: Optional[Path] = None) -> str:
    """Repo-safe relative path (forward slashes). Never emit absolute local paths into outputs."""
    root = root or src_root()
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return Path(path).name


def stale_files(conn, root: Optional[Path] = None, ctrls: Optional[List[str]] = None) -> Dict[str, List[str]]:
    """Compare on-disk files with the source_file ledger. Returns {'new':[], 'changed':[], 'missing':[]}."""
    root = root or src_root()
    ledger = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT path,size,mtime FROM source_file")}
    seen, new, changed = set(), [], []
    for sf in source_files(root, ctrls):
        seen.add(sf.rel)
        if sf.rel not in ledger:
            new.append(sf.rel)
        else:
            size, mtime = ledger[sf.rel]
            if size != sf.size or abs((mtime or 0) - sf.mtime) > 1.0:
                changed.append(sf.rel)
    missing = [p for p in ledger if p not in seen and (not ctrls or p.split("/")[0] in ctrls)]
    return {"new": new, "changed": changed, "missing": missing}
