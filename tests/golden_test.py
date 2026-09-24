# -*- coding: utf-8 -*-
"""Golden assertions against the built SQLite index (run: py tests/golden_test.py).

Two verified chains from the plan + project-wide counts. Exit 1 on any failure; prints one line per check.
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from dcdas import db as dbm  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(label)


def one(conn, sql, *args):
    r = conn.execute(sql, args).fetchone()
    return r[0] if r else None


def main():
    conn = sqlite3.connect(f"file:{dbm.db_path().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # ---- counts
    check("variable total 319081", one(conn, "SELECT count(*) FROM variable WHERE is_program_local=0") == 319081,
          str(one(conn, "SELECT count(*) FROM variable WHERE is_program_local=0")))
    g11_blocks = one(conn, "SELECT count(*) FROM block WHERE ctrl='G11' AND kind='block'")
    check("G11 blocks 31417", g11_blocks == 31417, str(g11_blocks))
    g11_tasks = one(conn, "SELECT count(*) FROM task t JOIN program p ON p.id=t.program_id WHERE p.ctrl='G11'")
    check("G11 tasks 1018 (950 TopUserBlock + 68 FFTask)", g11_tasks == 1018, str(g11_tasks))
    g11_enc = one(conn, "SELECT count(*) FROM program WHERE ctrl='G11' AND encrypted=1")
    check("G11 encrypted programs 20", g11_enc == 20, str(g11_enc))
    s1 = one(conn, "SELECT count(*) FROM program WHERE ctrl='S1'"), one(conn, "SELECT count(*) FROM program WHERE ctrl='S1' AND encrypted=1")
    check("S1 programs 92 / encrypted 88", s1 == (92, 88), str(s1))
    wsc = one(conn, "SELECT count(*) FROM program WHERE ctrl='WSC1' AND encrypted=1")
    check("WSC1 encrypted 10", wsc == 10, str(wsc))
    nav = one(conn, "SELECT count(*) FROM hmi_point WHERE source='navcsv'")
    check("hmi_point navcsv 17323 (17738 csv rows - 415 duplicates)", nav == 17323, str(nav))
    fsn = one(conn, "SELECT count(*) FROM format_spec")
    check("format_spec ~3060", fsn is not None and 3000 <= fsn <= 3100, str(fsn))
    wf = one(conn, "SELECT count(DISTINCT watch_file) FROM watch")
    check("watch files >= 100 (171 incl. empty stubs)", (wf or 0) >= 100, str(wf))

    nb = one(conn, "SELECT count(*) FROM block WHERE kind='block'")
    nl = one(conn, "SELECT count(*) FROM block WHERE kind='block' AND layout IS NULL")
    check("block.layout (BlockLayoutData) present for >99% of blocks", nb and nl / nb < 0.01, f"{nl}/{nb} null")

    # ---- chain A: G11 L27QE1_A -> EGD -> BOPE1
    v = conn.execute("SELECT * FROM variable WHERE ctrl='G11' AND name='L27QE1_A'").fetchone()
    check("A1 G11.L27QE1_A exists BOOL 01005DE1 LVL_1", v is not None and v["datatype"] == "BOOL" and v["address"] == "01005DE1"
          and v["alarm_class"] == "LVL_1" and v["decl_connection"] == "LubeOil.L27QE1_A",
          str(dict(v)) [:120] if v else "missing")
    w = conn.execute("""SELECT b.path,b.block_type,p.name,p.direction,p.dir_source,b.line_no,prg.name AS prog
                        FROM pin p JOIN block b ON b.id=p.block_id JOIN program prg ON prg.id=b.program_id
                        WHERE p.var_id=? AND p.direction='O'""", (v["id"] if v else -1,)).fetchall()
    check("A2 writer = G11/LubeOil/.../MOVE_21.DEST (O)", any(r["prog"] == "LubeOil" and r["path"].endswith("MOVE_21") and r["name"] == "DEST" for r in w),
          "; ".join(f"{r['prog']}/{r['path']}.{r['name']} {r['direction']}/{r['dir_source']}" for r in w))
    check("A2b exactly one writer", len(w) == 1, str(len(w)))
    src = conn.execute("""SELECT p.connection FROM pin p JOIN block b ON b.id=p.block_id JOIN program prg ON prg.id=b.program_id
                          WHERE prg.ctrl='G11' AND prg.name='LubeOil' AND b.path LIKE '%MOVE_21' AND p.name='SRC'""").fetchone()
    check("A2c MOVE_21.SRC <- L27QE1", src is not None and src[0] == "L27QE1", str(src[0]) if src else "missing")
    ep = conn.execute("""SELECT x.page,x.exchange_id,p.voffs FROM egd_produced p JOIN egd_exchange x ON x.id=p.exchange_pk
                         WHERE x.producer_ctrl='G11' AND p.var_name='L27QE1_A'""").fetchone()
    check("A3 produced Page1 ex2 voffs866", ep is not None and ep[0] == "Page1" and ep[1] == 2 and ep[2] == 866, str(tuple(ep)) if ep else "missing")
    ec = conn.execute("""SELECT producer_ctrl,exchange_id,voffs,local_address,match_method,local_var_id,producer_var_id
                         FROM egd_consumed WHERE consumer_ctrl='BOPE1' AND var_name='L27QE1_A' ORDER BY producer_ctrl""").fetchall()
    g11c = [r for r in ec if r["producer_ctrl"] == "G11"]
    check("A4 BOPE1 consumes G11 L27QE1_A ex2 voffs866 addr 0100628D match both",
          len(g11c) == 1 and g11c[0]["exchange_id"] == 2 and g11c[0]["voffs"] == 866 and g11c[0]["local_address"] == "0100628D"
          and g11c[0]["match_method"] == "both", "; ".join(str(tuple(r)) for r in ec))
    check("A4b second L27QE1_A (voffs 10642) attributed to G12", any(r["producer_ctrl"] == "G12" and r["voffs"] == 10642 for r in ec))
    bv = conn.execute("SELECT id,device_name,address,producer_var_id FROM variable WHERE ctrl='BOPE1' AND name='G11.L27QE1_A'").fetchone()
    check("A5 BOPE1.G11.L27QE1_A device_name G11 addr 0100628D producer_var_id -> G11 row",
          bv is not None and bv["device_name"] == "G11" and bv["address"] == "0100628D" and v is not None and bv["producer_var_id"] == v["id"],
          str(tuple(bv)) if bv else "missing")
    check("A4c local_var_id -> BOPE1.G11.L27QE1_A", bool(g11c) and bv is not None and g11c[0]["local_var_id"] == bv["id"])
    rd = conn.execute("""SELECT prg.name,b.path,p.name,p.direction,p.dir_source FROM pin p JOIN block b ON b.id=p.block_id
                         JOIN program prg ON prg.id=b.program_id WHERE p.var_id=? AND b.ctrl='BOPE1'""", (bv["id"] if bv else -1,)).fetchall()
    check("A6 BOPE1 reader ALARM_EE_1 task G11_L27QE1_A MOVE_1.SRC", any(r[0] == "ALARM_EE_1" and "G11_L27QE1_A" in r[1] and r[2] == "SRC" for r in rd),
          "; ".join(f"{r[0]}/{r[1]}.{r[2]} {r[3]}/{r[4]}" for r in rd)[:200])
    hm = one(conn, "SELECT count(*) FROM hmi_point WHERE var_id=? AND screen='GTHA_GEN2_Lube_Seal_Lift_Oil_UX.cim'", v["id"] if v else -1)
    check("A7 hmi screen GTHA_GEN2_Lube_Seal_Lift_Oil_UX.cim", (hm or 0) >= 1, str(hm))

    # ---- chain B: WSC1 1-TI-CW011-2AAXQ01 terminal -> logic
    vb = conn.execute("SELECT * FROM variable WHERE ctrl='WSC1' AND name='1-TI-CW011-2AAXQ01'").fetchone()
    check("B2 WSC1.1-TI-CW011-2AAXQ01 REAL degC 0-200 decl CA016", vb is not None and vb["datatype"] == "REAL" and vb["units"] == "°C"
          and vb["disp_low"] == 0 and vb["disp_high"] == 200 and vb["decl_connection"] == "CA016.1-TI-CW011-2AAXQ01",
          str(dict(vb))[:160] if vb else "missing")
    iop = conn.execute("""SELECT p.id,m.name,m.cabinet,b.name,b.hw_form,b.position_r,p.name,p.device_tag,p.address,p.input_type,p.low_value,p.high_value,p.direction
                          FROM io_point p JOIN io_module m ON m.id=p.module_id JOIN io_board b ON b.id=p.board_id
                          WHERE p.var_id=?""", (vb["id"] if vb else -1,)).fetchone()
    check("B1 io_point PHRA-32/CA016/SHRA/AnalogInput02 tag 1-TI-CW011-2AA 4-20ma 0-200 dir I",
          iop is not None and iop[1] == "PHRA-32" and iop[2] == "CA016" and iop[3].startswith("SHRA") and iop[6] == "AnalogInput02"
          and iop[7] == "1-TI-CW011-2AA" and iop[8] == "04000AC9" and iop[9] == "4-20ma" and iop[10] == 0 and iop[11] == 200 and iop[12] == "I",
          str(tuple(iop)) if iop else "missing")
    sc = {r[0]: r[1] for r in conn.execute("SELECT name,number FROM io_screw WHERE point_id=?", (iop[0] if iop else -1,))}
    check("B1b screws P24V2=5 20mA2=6 VDC2=7 Ret2=8", sc.get("P24V2") == 5 and sc.get("20mA2") == 6 and sc.get("VDC2") == 7 and sc.get("Ret2") == 8, str(sc))
    wb = one(conn, "SELECT count(*) FROM pin WHERE var_id=? AND direction='O'", vb["id"] if vb else -1)
    check("B3 no logic writer (field I/O)", wb == 0, str(wb))
    rb = conn.execute("""SELECT prg.name,b.path,b.block_type,p.name,p.direction,p.dir_source FROM pin p JOIN block b ON b.id=p.block_id
                         JOIN program prg ON prg.id=b.program_id WHERE p.var_id=? AND b.ctrl='WSC1'""", (vb["id"] if vb else -1,)).fetchall()
    check("B4 reader AI block .IN in CWS_COMB_SYSTEM_INPUTS_1", any(r[0] == "CWS_COMB_SYSTEM_INPUTS_1" and r[2] == "AI" and r[3] == "IN" for r in rb),
          "; ".join(f"{r[0]}/{r[1]}[{r[2]}].{r[3]} {r[4]}/{r[5]}" for r in rb)[:200])
    ai_out = conn.execute("""SELECT p.name,p.conn_kind,p.direction,p.dir_source,p.connection FROM pin p JOIN block b ON b.id=p.block_id
                             JOIN program prg ON prg.id=b.program_id WHERE prg.ctrl='WSC1' AND prg.name='CWS_COMB_SYSTEM_INPUTS_1'
                             AND b.block_type='AI' AND p.conn_kind='P' LIMIT 1""").fetchone()
    check("B4b AI output pin with conn_kind P is direction O", ai_out is not None and ai_out[2] == "O", str(tuple(ai_out)) if ai_out else "no P pin")
    drgs = conn.execute("""SELECT DISTINCT b.logic_drg,b.p_id FROM pin p JOIN block b ON b.id=p.block_id WHERE p.var_id=?""", (vb["id"] if vb else -1,)).fetchall()
    check("B6 LogicDrg 111604-10-GA-YDY-SNL-001 reachable via reader block (or its userblock)",
          any("111604-10-GA-YDY-SNL-001" in (r[0] or "") for r in drgs) or one(conn,
          """SELECT count(*) FROM block b WHERE b.ctrl='WSC1' AND b.logic_drg='111604-10-GA-YDY-SNL-001'""") > 0, str(drgs)[:120])

    # ---- declared-at-pin links (PID CVO etc.)
    na = one(conn, "SELECT count(*) FROM pin WHERE conn_kind='A' AND var_id IS NOT NULL")
    check("A-kind pins linked to declared variables >= 160000", (na or 0) >= 160000, str(na))
    cvo = conn.execute("""SELECT b.path,p.direction,p.conn_kind FROM pin p JOIN block b ON b.id=p.block_id JOIN variable v ON v.id=p.var_id
                          WHERE v.ctrl='H11' AND v.name='HpBypToCrhPressCv.CVO' AND p.name='CVO'""").fetchall()
    check("H11 HpBypToCrhPressCv.CVO written by the PID block's CVO pin (O, declared at pin)",
          len(cvo) == 1 and cvo[0][1] == "O" and cvo[0][2] == "A", str(cvo))

    # ---- template (LibName) pins: the AI block's output is named after its Device attribute
    ai = conn.execute("""SELECT b.path,p.direction,p.dir_source,p.conn_kind,p.lib_name FROM pin p JOIN block b ON b.id=p.block_id
                         JOIN variable v ON v.id=p.var_id WHERE v.ctrl='H11' AND v.name='HpOTHeatExOutNearSideTemp6_AI' AND p.direction='O'""").fetchall()
    check("H11.HpOTHeatExOutNearSideTemp6_AI written by AI_153's {Device} pin (O/T, declared at pin)",
          len(ai) == 1 and ai[0][0].endswith("/AI_153") and ai[0][2] == "T" and ai[0][3] == "A" and ai[0][4] == "{Device}", str(ai))
    nq = one(conn, "SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE b.block_type='AI' AND p.lib_name='{Device}' AND p.direction='?'")
    check("no AI {Device} pin left with direction '?'", nq == 0, str(nq))
    pd = conn.execute("SELECT direction, source FROM pin_dir WHERE block_type='AI' AND pin_name='{Device}'").fetchone()
    check("pin_dir has (AI,{Device}) = O/T", pd is not None and tuple(pd) == ("O", "T"), str(pd))

    # ---- paired opaque AI_INT: IN reads the sibling AI_k device output (naming-pair inference)
    pr = conn.execute("""SELECT p.direction,p.dir_source,p.conn_kind,p.origin,v.full_name FROM pin p JOIN block b ON b.id=p.block_id
                         LEFT JOIN variable v ON v.id=p.var_id WHERE b.ctrl='H11' AND b.path='HardwireInputs_1/HW_ISC_HEATEX/AI_INT_1' AND p.name='IN'""").fetchone()
    check("H11 AI_INT_1.IN paired to H11.HpOTHeatExInFarSideTemp1_AI (I/R, origin pair)",
          pr is not None and tuple(pr) == ("I", "R", "V", "pair", "H11.HpOTHeatExInFarSideTemp1_AI"), str(tuple(pr)) if pr else "missing")
    po = conn.execute("""SELECT p.name,p.direction,p.dir_source,p.conn_kind,v.full_name FROM pin p JOIN block b ON b.id=p.block_id
                         LEFT JOIN variable v ON v.id=p.var_id WHERE b.ctrl='H11' AND b.path='HardwireInputs_1/HW_ISC_HEATEX/AI_INT_1'
                         AND p.origin='pair' AND p.name IN ('OUT','DEVICE_STATUS') ORDER BY p.name""").fetchall()
    check("H11 AI_INT_1 OUT -> ai_HpOTHeatExInFarSideTemp1, DEVICE_STATUS -> HpOTHeatExInFarSideTemp1_DS (O/R, pair)",
          [tuple(r) for r in po] == [("DEVICE_STATUS", "O", "R", "V", "H11.HpOTHeatExInFarSideTemp1_DS"), ("OUT", "O", "R", "V", "H11.ai_HpOTHeatExInFarSideTemp1")],
          str([tuple(r) for r in po]))
    # ---- AI_INT_153: the same pair inference, but IN and OUT were confirmed in the tool -> xref rows replaced the pair rows
    px = conn.execute("""SELECT p.name,p.direction,p.dir_source,p.origin,v.full_name FROM pin p JOIN block b ON b.id=p.block_id
                         LEFT JOIN variable v ON v.id=p.var_id WHERE b.ctrl='H11' AND b.path='HardwireInputs_1/HW_ISC_HEATEX/AI_INT_153'
                         ORDER BY p.name""").fetchall()
    check("H11 AI_INT_153: IN/OUT/DEVICE_STATUS all verified (xref, T); one row per pin",
          [tuple(r) for r in px] == [("DEVICE_STATUS", "O", "T", "xref", "H11.HpOTHeatExOutNearSideTemp6_DS"),
                                     ("IN", "I", "T", "xref", "H11.HpOTHeatExOutNearSideTemp6_AI"),
                                     ("OUT", "O", "T", "xref", "H11.ai_HpOTHeatExOutNearSideTemp6")], str([tuple(r) for r in px]))
    npair = one(conn, "SELECT count(*) FROM pin WHERE origin='pair'")
    check("pair rows >= 1900 (~2069 recovered, 120 replaced by xref rows)", (npair or 0) >= 1900, str(npair))
    nbad = one(conn, "SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE p.origin='pair' AND NOT (b.is_opaque=1 AND b.block_type='AI_INT' AND p.name IN ('IN','OUT','DEVICE_STATUS'))")
    check("pair rows only on opaque AI_INT IN/OUT/DEVICE_STATUS", nbad == 0, str(nbad))

    # ---- hand-verified cross-reference rows (tools/xref_manual.csv): encrypted 4oo20 voters, 20 inputs + OUT each
    xr = conn.execute("""SELECT b.name,p.direction,p.dir_source,p.conn_kind,p.origin,v.full_name FROM pin p JOIN block b ON b.id=p.block_id
                         LEFT JOIN variable v ON v.id=p.var_id WHERE b.ctrl='H11' AND b.path LIKE 'HRSG_Protection_1/FNCTN_HpOTHeatExOutTemp/NooM_Basic_%'
                         AND p.name='INL' ORDER BY b.name""").fetchall()
    check("H11 NooM_Basic_1/2.INL read H11.HpOTHeatExOutNearSideTemp6 (I/T/V, origin xref)",
          [tuple(r) for r in xr] == [("NooM_Basic_1", "I", "T", "V", "xref", "H11.HpOTHeatExOutNearSideTemp6"),
                                     ("NooM_Basic_2", "I", "T", "V", "xref", "H11.HpOTHeatExOutNearSideTemp6")], str([tuple(r) for r in xr]))
    vp = {r[0]: (r[1], r[2]) for r in conn.execute("""SELECT p.name, p.direction, v.name FROM pin p JOIN block b ON b.id=p.block_id
                         LEFT JOIN variable v ON v.id=p.var_id WHERE b.ctrl='H11' AND b.path='HRSG_Protection_1/FNCTN_HpOTHeatExOutTemp/NooM_Basic_1' AND p.origin='xref'""")}
    check("H11 NooM_Basic_1: 21 xref pins, INA=FarSideTemp1, INL=NearSideTemp6, INT=NearSideTemp10, OUT -> PRO_HpOTHeatExOutTemp2Hi (O)",
          len(vp) == 21 and vp.get("INA") == ("I", "HpOTHeatExOutFarSideTemp1") and vp.get("INL") == ("I", "HpOTHeatExOutNearSideTemp6")
          and vp.get("INT") == ("I", "HpOTHeatExOutNearSideTemp10") and vp.get("OUT") == ("O", "PRO_HpOTHeatExOutTemp2Hi"), str(sorted(vp.items()))[:300])
    nw = one(conn, "SELECT count(*) FROM pin p JOIN variable v ON v.id=p.var_id WHERE v.full_name='H11.PRO_HpOTHeatExOutTemp2Hi' AND p.direction='O'")
    check("H11.PRO_HpOTHeatExOutTemp2Hi has exactly 1 writer (the voter OUT)", nw == 1, str(nw))
    nx = one(conn, "SELECT count(*) FROM pin WHERE origin='xref'")
    check("xref rows = 204 (H11 + H12: 2 voters x 21 + 20 outlet-temp AI_INT x 3 pins)", nx == 204, str(nx))
    n40 = one(conn, """SELECT count(*) FROM (SELECT b.id FROM pin p JOIN block b ON b.id=p.block_id JOIN variable v ON v.id=p.var_id
                        WHERE b.path LIKE 'HardwireInputs_1/HW_ISC_HEATEX/AI_INT_%' AND v.name LIKE '%HpOTHeatExOut%SideTemp%'
                        GROUP BY b.id HAVING sum(p.origin='xref')=3 AND count(*)=3)""")
    check("40 outlet-temp AI_INT instances (H11 + H12) have exactly 3 pins, all xref", n40 == 40, str(n40))
    nbad = one(conn, "SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE p.origin='xref' AND (b.is_opaque=0 OR p.dir_source<>'T' OR p.var_id IS NULL)")
    check("xref rows only on opaque blocks, T, with a variable", nbad == 0, str(nbad))
    nr = one(conn, """SELECT count(*) FROM pin p JOIN variable v ON v.id=p.var_id WHERE v.full_name='H11.HpOTHeatExOutNearSideTemp6' AND p.direction<>'O'""")
    check("H11.HpOTHeatExOutNearSideTemp6 has 9 reader pins (7 plaintext + 2 xref)", nr == 9, str(nr))

    # ---- pin mirrors (published value of an already-wired pin)
    nm = one(conn, "SELECT count(*) FROM pin_mirror")
    check("pin_mirror rows >= 25000", (nm or 0) >= 25000, str(nm))
    mir = conn.execute("""SELECT b.path, p.name, p.conn_kind, w.full_name FROM pin_mirror m JOIN variable v ON v.id=m.var_id
                          JOIN pin p ON p.id=m.pin_id JOIN block b ON b.id=p.block_id LEFT JOIN variable w ON w.id=p.var_id
                          WHERE v.ctrl='H11' AND v.name='HpDistCV2.RSP'""").fetchone()
    check("H11.HpDistCV2.RSP mirrors pin HpDistCV2.RSP wired to H11.HpDistCv2PID11_SP",
          mir is not None and mir[1] == "RSP" and mir[2] == "V" and mir[3] == "H11.HpDistCv2PID11_SP", str(tuple(mir)) if mir else "missing")

    # ---- opaque macro interface recovery (origin = decl | link)
    dff = one(conn, "SELECT id FROM block WHERE ctrl='H11' AND path='HrsgHP_1/ACTUATOR_HpOTFdwtrFlwCV/DFFWD_1'")
    n_dff = one(conn, "SELECT count(*) FROM pin WHERE block_id=?", dff)
    check("H11 DFFWD_1 (opaque) has >= 60 recovered pins", (n_dff or 0) >= 60, str(n_dff))
    rows = {r[0]: r[1:] for r in conn.execute("""SELECT p.name, p.direction, p.conn_kind, p.origin, v.name FROM pin p LEFT JOIN variable v ON v.id=p.var_id
                                                 WHERE p.block_id=? AND p.name IN ('F_SP_FW','M_WTR_IN_FFWD_C','KP_SCHED_C','A_FILT','GT_FLAME_OFF_C')""", (dff,))}
    check("DFFWD_1.F_SP_FW recovered as input wired to F_SP_FW (written by DIV_4.OUT)", rows.get("F_SP_FW") == ("I", "V", "decl", "F_SP_FW"), str(rows.get("F_SP_FW")))
    check("DFFWD_1.M_WTR_IN_FFWD_C recovered as output", rows.get("M_WTR_IN_FFWD_C") == ("O", "A", "decl", "M_WTR_IN_FFWD_C"), str(rows.get("M_WTR_IN_FFWD_C")))
    check("DFFWD_1.KP_SCHED_C recovered from L: link as output", rows.get("KP_SCHED_C") == ("O", "-", "link", None), str(rows.get("KP_SCHED_C")))
    check("DFFWD_1.A_FILT recovered as constant input", rows.get("A_FILT") == ("I", "A", "decl", "A_FILT"), str(rows.get("A_FILT")))
    check("DFFWD_1.GT_FLAME_OFF_C wired to same-address HHP_HrsgNotInSrvc", rows.get("GT_FLAME_OFF_C") == ("I", "V", "decl", "HHP_HrsgNotInSrvc"), str(rows.get("GT_FLAME_OFF_C")))
    mir = conn.execute("""SELECT m.kind, p.name FROM pin_mirror m JOIN pin p ON p.id=m.pin_id
                          WHERE m.var_id=(SELECT id FROM variable WHERE ctrl='H11' AND name='GT_FLAME_OFF_C')""").fetchone()
    mir = tuple(mir) if mir else None
    check("H11.GT_FLAME_OFF_C is a mirror of DFFWD_1.GT_FLAME_OFF_C (kind I)", mir == ("I", "GT_FLAME_OFF_C"), str(mir))
    nw = one(conn, "SELECT count(*) FROM pin WHERE direction='O' AND var_id=(SELECT id FROM variable WHERE ctrl='H11' AND name='BlwdnTkLvlLowRedun')")
    check("H11.BlwdnTkLvlLowRedun (alarm) has a recovered writer (REDUNDANCY_STATUS_V2)", nw == 1, str(nw))
    n_decl = one(conn, "SELECT count(*) FROM pin WHERE origin='decl'")
    n_link = one(conn, "SELECT count(*) FROM pin WHERE origin='link'")
    check("recovered pins: decl >= 1000, link >= 1400", (n_decl or 0) >= 1000 and (n_link or 0) >= 1400, f"decl={n_decl} link={n_link}")
    bad = one(conn, "SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE p.origin IS NOT NULL AND b.is_opaque=0")
    check("recovered pins only on opaque blocks", bad == 0, str(bad))
    bad = one(conn, "SELECT count(*) FROM pin WHERE origin IS NOT NULL AND conn_kind IN ('V','L','P','D') AND direction='?'")
    check("recovered pins never '?' inside the connected set", bad == 0, str(bad))
    # same rule as query.lint: a recovered pin of an opaque macro counts as a block writer (interface pin + block = one path)
    n_multi = one(conn, """SELECT count(*) FROM (SELECT p.var_id, count(*) AS n, sum(b.kind='block' OR p.origin IS NOT NULL) AS nb FROM pin p JOIN block b ON b.id=p.block_id
                           WHERE p.direction='O' AND p.var_id IS NOT NULL GROUP BY p.var_id HAVING (nb>1 OR (nb=0 AND n>1)))""")
    check("multi-writer variables <= 2510 (2485 before recovery; 2503 with pairing)", (n_multi or 0) <= 2510, str(n_multi))

    # ---- quality gates
    for c in ("G11", "H11", "WSC1"):
        tot = one(conn, "SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE b.ctrl=? AND p.conn_kind IN ('V','L','P','D')", c)
        unk = one(conn, "SELECT count(*) FROM pin p JOIN block b ON b.id=p.block_id WHERE b.ctrl=? AND p.conn_kind IN ('V','L','P','D') AND (p.direction='?' OR p.direction IS NULL)", c)
        frac = (unk / tot) if tot else 1
        check(f"coverage {c}: unknown direction < 5% of connected pins", frac < 0.05, f"{unk}/{tot} = {frac:.1%}")
    print()
    print(f"{len(FAILS)} failure(s)" if FAILS else "ALL OK")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
