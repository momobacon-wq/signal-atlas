# -*- coding: utf-8 -*-
"""Encrypt a standalone HTML report for docs/reports/ with the site passphrase.

  py tools/seal_report.py <report.html> <name> [--docs DIR]

Writes <docs>/reports/<name>.bin (12-byte IV || AES-256-GCM(gzip(html)), AAD "reports/<name>") and
<docs>/reports/<name>.json ({kdf}); <docs> defaults to the config's web_dir (docs/, or docs/<site>/ for a site set by
DCDAS_CONFIG). The key is derived exactly like export-web (same salt as <docs>/data/meta.json, so a key remembered by
that site also opens the report). The loader page <docs>/reports/<name>.html and docs/assets/report.js are checked in
by hand.
Before sealing, the page is made site-safe: Signal Atlas links become relative, Google Fonts and inline scripts
are dropped (the site CSP allows neither; report.js runs the claim filter).
"""
import argparse, gzip, json, os, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from dcdas import db as dbm                              # noqa: E402
from dcdas.db import web_passphrase                      # noqa: E402
from dcdas.export_web import derive_key, seal, KDF_ITER  # noqa: E402
import base64                                            # noqa: E402

ATLAS = dbm.DEFAULT_WEB_BASE                             # the default site; a site's own base is dbm.web_base()


def _rel(target: Path, out: Path) -> str:
    return os.path.relpath(target, out).replace(os.sep, "/").rstrip("/") + "/"


def site_safe(html: str, docs: Path = None) -> str:
    """Links into the published sites become relative to <docs>/reports/: the site's own base (web_base) -> <docs>/,
    the default site's base -> the folder above a sub-site's <docs> (or <docs> itself on the default site). The longer
    base is replaced first (a sub-site's base starts with the default site's)."""
    docs = Path(docs) if docs else REPO / "docs"
    out = docs / "reports"
    bases = {ATLAS: docs.parent if dbm.site() else docs}
    bases[dbm.web_base()] = docs
    for url in sorted(bases, key=len, reverse=True):
        html = html.replace(url, _rel(bases[url], out))
    html = re.sub(r'<link[^>]+fonts\.(googleapis|gstatic)\.com[^>]*>\s*', "", html)
    html = re.sub(r"<script\b.*?</script>\s*", "", html, flags=re.S)
    return "<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"></head><body>" + html + "</body></html>"


def main():
    ap = argparse.ArgumentParser(description="seal a standalone HTML report for <docs>/reports/")
    ap.add_argument("report"); ap.add_argument("name")
    ap.add_argument("--docs", help="site folder (default: the config's web_dir)")
    a = ap.parse_args()
    src, name = Path(a.report), a.name
    docs = Path(a.docs) if a.docs else dbm.web_dir(REPO)
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise SystemExit("name: lowercase letters, digits and dashes only")
    meta = json.loads((docs / "data" / "meta.json").read_text(encoding="utf-8"))
    kdf = meta["kdf"]
    assert int(kdf["iter"]) == KDF_ITER
    key = derive_key(web_passphrase(), base64.b64decode(kdf["salt"]))
    html = site_safe(src.read_text(encoding="utf-8"), docs)
    out = docs / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.bin").write_bytes(seal(key, gzip.compress(html.encode("utf-8"), mtime=0), f"reports/{name}"))
    (out / f"{name}.json").write_text(json.dumps({"enc": 1, "gzip": 1, "kdf": kdf}, ensure_ascii=False), encoding="utf-8")
    print(f"sealed {name}: {len(html):,} chars -> {(out / f'{name}.bin').stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
