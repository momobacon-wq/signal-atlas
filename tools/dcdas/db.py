# -*- coding: utf-8 -*-
"""SQLite schema + connection helpers + local configuration.

Nothing site-specific lives in the repo. Local settings come from (in priority order):
  env DCDAS_DB            index file            default %LOCALAPPDATA%\\dcdas\\index.sqlite
  env DCDAS_SRC           checkout root         else config.json "src_root"
  env DCDAS_WEB_KEY       site passphrase       else config.json "web_key_file" (first line of that file)
  %LOCALAPPDATA%\\dcdas\\config.json   {"src_root": "...", "web_key_file": "...", "manual_pdfs": ["...", ...]}
"""
import json
import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = "7"


def local_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "dcdas"


def config_path() -> Path:
    return Path(os.environ.get("DCDAS_CONFIG") or local_dir() / "config.json")


def config() -> dict:
    p = config_path()
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def db_path() -> Path:
    p = os.environ.get("DCDAS_DB") or config().get("db_path")
    if p:
        return Path(p)
    return local_dir() / "index.sqlite"


def src_root() -> Path:
    p = os.environ.get("DCDAS_SRC") or config().get("src_root")
    if not p:
        raise SystemExit(f"checkout root unknown: set DCDAS_SRC or \"src_root\" in {config_path()}")
    return Path(p)


def web_passphrase(key_file: str = None) -> str:
    """Passphrase used to encrypt the published data (never stored in the repo)."""
    v = os.environ.get("DCDAS_WEB_KEY")
    if v:
        return v.strip()
    kf = key_file or config().get("web_key_file") or str(local_dir() / "web.key")
    if not Path(kf).exists():
        raise SystemExit(f"site passphrase not found: set DCDAS_WEB_KEY or put it in {kf} (one line)")
    with open(kf, "r", encoding="utf-8") as f:
        v = f.readline().strip()
    if len(v) < 8:
        raise SystemExit("site passphrase too short (min 8 characters)")
    return v


DDL = r"""
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS controller(
  name TEXT PRIMARY KEY, kind TEXT, product_version TEXT, major_rev TEXT, minor_rev TEXT,
  last_mod TEXT, coherency TEXT, redundancy TEXT, platform TEXT, egd_producer_id TEXT, indexed_at TEXT);
CREATE TABLE IF NOT EXISTS source_file(
  path TEXT PRIMARY KEY, ctrl TEXT, kind TEXT, size INTEGER, mtime REAL, sha1 TEXT, coherency TEXT,
  encrypted INTEGER, n_blocks INTEGER, n_zk INTEGER, parsed_at TEXT);

CREATE TABLE IF NOT EXISTS program(
  id INTEGER PRIMARY KEY, ctrl TEXT NOT NULL, name TEXT NOT NULL, library_type TEXT, file_path TEXT,
  encrypted INTEGER DEFAULT 0, block_count INTEGER DEFAULT 0, task_count INTEGER DEFAULT 0,
  help_file TEXT, UNIQUE(ctrl, name));
CREATE TABLE IF NOT EXISTS task(
  id INTEGER PRIMARY KEY, program_id INTEGER NOT NULL, name TEXT, block_type TEXT, logic_drg TEXT,
  is_task INTEGER, line_no INTEGER);
CREATE INDEX IF NOT EXISTS ix_task_prog ON task(program_id);
CREATE TABLE IF NOT EXISTS block(
  id INTEGER PRIMARY KEY, ctrl TEXT NOT NULL, program_id INTEGER NOT NULL, task_id INTEGER, parent_id INTEGER,
  path TEXT NOT NULL, name TEXT NOT NULL, block_type TEXT, kind TEXT CHECK(kind IN ('block','userblock','task')),
  version TEXT, is_opaque INTEGER DEFAULT 0, description TEXT, logic_drg TEXT, p_id TEXT, device TEXT,
  hmi_linked_object TEXT, line_no INTEGER, layout INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS ix_block_path ON block(ctrl, path);
CREATE INDEX IF NOT EXISTS ix_block_name ON block(ctrl, program_id, name);
CREATE INDEX IF NOT EXISTS ix_block_type ON block(block_type);
CREATE INDEX IF NOT EXISTS ix_block_task ON block(task_id);
CREATE INDEX IF NOT EXISTS ix_block_parent ON block(parent_id);
CREATE TABLE IF NOT EXISTS block_attr(block_id INTEGER, name TEXT, value TEXT, PRIMARY KEY(block_id, name));

CREATE TABLE IF NOT EXISTS pin(
  id INTEGER PRIMARY KEY, block_id INTEGER NOT NULL, name TEXT NOT NULL,
  direction TEXT CHECK(direction IN ('I','O','S','?')), dir_source TEXT CHECK(dir_source IN ('U','T','C','L','H','R','-')),
  conn_kind TEXT CHECK(conn_kind IN ('V','L','P','D','N','E','A','-')), connection TEXT,
  var_id INTEGER, tgt_block_id INTEGER, tgt_pin TEXT, address TEXT, value TEXT, alias TEXT,
  alias_override INTEGER, usage_declared TEXT, description TEXT, line_no INTEGER,
  origin TEXT CHECK(origin IN ('decl','link','pair','xref')), lib_name TEXT, UNIQUE(block_id, name));
CREATE INDEX IF NOT EXISTS ix_pin_origin ON pin(origin);
CREATE INDEX IF NOT EXISTS ix_pin_var ON pin(var_id, direction);
CREATE INDEX IF NOT EXISTS ix_pin_block ON pin(block_id);
CREATE INDEX IF NOT EXISTS ix_pin_tgt ON pin(tgt_block_id, tgt_pin);
CREATE INDEX IF NOT EXISTS ix_pin_alias ON pin(alias);

CREATE TABLE IF NOT EXISTS variable(
  id INTEGER PRIMARY KEY, ctrl TEXT NOT NULL, name TEXT NOT NULL, full_name TEXT NOT NULL,
  description TEXT, datatype TEXT, address TEXT, scope TEXT, value TEXT,
  decl_connection TEXT, decl_program TEXT, decl_task TEXT, global_prefix TEXT,
  egd_page TEXT, alias TEXT, format_spec TEXT, units TEXT, disp_low REAL, disp_high REAL,
  display_screen TEXT, control_constant INTEGER DEFAULT 0, device_name TEXT, producer_var_id INTEGER,
  referenced_in TEXT, alarm_id TEXT, alarm_class TEXT, alarm_definition TEXT, plant_area TEXT,
  potential_causes TEXT, operator_action TEXT, consequence TEXT, urgency TEXT,
  normal_severity INTEGER, active_severity INTEGER, is_program_local INTEGER DEFAULT 0,
  decl_file TEXT, decl_line INTEGER, UNIQUE(ctrl, name));
CREATE INDEX IF NOT EXISTS ix_var_name ON variable(name);
CREATE INDEX IF NOT EXISTS ix_var_alias ON variable(alias);
CREATE INDEX IF NOT EXISTS ix_var_device ON variable(device_name);
CREATE INDEX IF NOT EXISTS ix_var_addr ON variable(ctrl, address);
CREATE INDEX IF NOT EXISTS ix_var_full ON variable(full_name);

CREATE TABLE IF NOT EXISTS pin_dir(
  block_type TEXT, pin_name TEXT, direction TEXT, source TEXT,
  n_targeted INTEGER DEFAULT 0, n_carries INTEGER DEFAULT 0, n_const INTEGER DEFAULT 0, n_usage INTEGER DEFAULT 0,
  PRIMARY KEY(block_type, pin_name));
CREATE TABLE IF NOT EXISTS pin_dir_table(
  block_type TEXT, pin_name TEXT, direction TEXT, source_doc TEXT, page INTEGER, PRIMARY KEY(block_type, pin_name));
CREATE TABLE IF NOT EXISTS pin_dir_override(
  block_type TEXT, pin_name TEXT, direction TEXT, note TEXT, PRIMARY KEY(block_type, pin_name));

CREATE TABLE IF NOT EXISTS io_module(
  id INTEGER PRIMARY KEY, ctrl TEXT, name TEXT, module_id TEXT, cabinet TEXT, io_redundancy TEXT,
  ip_r TEXT, ip_s TEXT, ip_t TEXT, barcode_r TEXT, library_version TEXT, line_no INTEGER);
CREATE TABLE IF NOT EXISTS io_board(
  id INTEGER PRIMARY KEY, module_id INTEGER, name TEXT, hw_form TEXT, position_r TEXT, barcode TEXT, line_no INTEGER);
CREATE TABLE IF NOT EXISTS io_point(
  id INTEGER PRIMARY KEY, ctrl TEXT, module_id INTEGER, board_id INTEGER, name TEXT,
  direction TEXT CHECK(direction IN ('I','O','?')), signal_type TEXT, connection TEXT, var_id INTEGER,
  device_tag TEXT, address TEXT, input_type TEXT, low_value REAL, high_value REAL, params_json TEXT, line_no INTEGER);
CREATE INDEX IF NOT EXISTS ix_iop_var ON io_point(var_id);
CREATE INDEX IF NOT EXISTS ix_iop_tag ON io_point(device_tag);
CREATE INDEX IF NOT EXISTS ix_iop_mod ON io_point(ctrl, module_id);
CREATE INDEX IF NOT EXISTS ix_iop_conn ON io_point(ctrl, connection);
CREATE TABLE IF NOT EXISTS io_screw(
  point_id INTEGER, name TEXT, number INTEGER, cable_number TEXT, wire_number TEXT, interposing_tb TEXT,
  jumpers TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_screw_pt ON io_screw(point_id);

CREATE TABLE IF NOT EXISTS egd_exchange(
  id INTEGER PRIMARY KEY, producer_ctrl TEXT, exchange_id INTEGER, page TEXT, period_ns INTEGER,
  data_length INTEGER, sig_major INTEGER, UNIQUE(producer_ctrl, exchange_id));
CREATE TABLE IF NOT EXISTS egd_produced(
  exchange_pk INTEGER, var_name TEXT, dtype TEXT, address TEXT, voffs INTEGER, var_id INTEGER,
  PRIMARY KEY(exchange_pk, voffs, var_name));
CREATE INDEX IF NOT EXISTS ix_egdp_var ON egd_produced(var_id);
CREATE TABLE IF NOT EXISTS egd_consumed(
  id INTEGER PRIMARY KEY, consumer_ctrl TEXT, producer_ctrl TEXT, exchange_id INTEGER, page TEXT,
  var_name TEXT, voffs INTEGER, local_address TEXT, local_var_id INTEGER, producer_var_id INTEGER,
  match_method TEXT CHECK(match_method IN ('name','voffs','both','none')));
CREATE INDEX IF NOT EXISTS ix_egdc_prod ON egd_consumed(producer_ctrl, var_name);
CREATE INDEX IF NOT EXISTS ix_egdc_pvar ON egd_consumed(producer_var_id);
CREATE INDEX IF NOT EXISTS ix_egdc_lvar ON egd_consumed(local_var_id);

CREATE TABLE IF NOT EXISTS hmi_point(
  full_point TEXT, unit_prefix TEXT, screen TEXT, source TEXT CHECK(source IN ('navcsv','display_screen','block_attr')),
  var_id INTEGER, PRIMARY KEY(full_point, screen, source));
CREATE INDEX IF NOT EXISTS ix_hmi_var ON hmi_point(var_id);
CREATE INDEX IF NOT EXISTS ix_hmi_screen ON hmi_point(screen);
CREATE TABLE IF NOT EXISTS hmi_menu(block TEXT, menu TEXT, submenu TEXT, item TEXT, screen TEXT, unit TEXT);
CREATE TABLE IF NOT EXISTS format_spec(name TEXT PRIMARY KEY, units TEXT, low REAL, high REAL, decimals INTEGER, raw_json TEXT);
CREATE TABLE IF NOT EXISTS alarm_class(name TEXT PRIMARY KEY, description TEXT, priority INTEGER);
CREATE TABLE IF NOT EXISTS watch(ctrl TEXT, watch_file TEXT, var_name TEXT, datasource TEXT, var_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_watch_var ON watch(var_id);
CREATE TABLE IF NOT EXISTS library_help(block_type TEXT PRIMARY KEY, library TEXT, mht_path TEXT, def_file TEXT);
CREATE TABLE IF NOT EXISTS pin_mirror(
  var_id INTEGER PRIMARY KEY, pin_id INTEGER, kind TEXT);   -- variable = published value of this (already wired) pin
CREATE INDEX IF NOT EXISTS ix_mirror_pin ON pin_mirror(pin_id);
CREATE TABLE IF NOT EXISTS lib_pin_usage(
  library TEXT, def_name TEXT, pin_name TEXT, usage TEXT, PRIMARY KEY(library, def_name, pin_name));
"""

FTS_DDL = r"""
CREATE VIRTUAL TABLE IF NOT EXISTS variable_fts USING fts5(
  full_name, description, alias, device_tag, alarm_text, content='',
  tokenize="unicode61 tokenchars '_-./'");
"""


def open_build(path: Path = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-262144")   # 256 MB
    conn.execute("PRAGMA page_size=4096")
    conn.executescript(DDL)
    try:
        conn.executescript(FTS_DDL)
    except sqlite3.OperationalError as e:   # FTS5 missing
        print("WARN: FTS5 unavailable:", e)
    return conn


def open_ro(path: Path = None) -> sqlite3.Connection:
    path = path or db_path()
    if not path.exists():
        raise SystemExit(f"index not built: {path}  (run: py tools/dcdas.py build)")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA cache_size=-65536")
    conn.row_factory = sqlite3.Row
    return conn


class Batch:
    """Accumulate rows and executemany in chunks."""

    def __init__(self, conn, sql, size=5000):
        self.conn, self.sql, self.size, self.rows, self.n = conn, sql, size, [], 0

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) >= self.size:
            self.flush()

    def flush(self):
        if self.rows:
            self.conn.executemany(self.sql, self.rows)
            self.n += len(self.rows)
            self.rows = []


def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))


def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default
