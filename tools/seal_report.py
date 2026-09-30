# -*- coding: utf-8 -*-
"""Encrypt a standalone HTML report for docs/reports/ with the site passphrase.

  py tools/seal_report.py <report.html> <name>

Writes docs/reports/<name>.bin (12-byte IV || AES-256-GCM(gzip(html)), AAD "reports/<name>") and
docs/reports/<name>.json ({kdf}). The key is derived exactly like export-web (same salt as docs/data/meta.json,
so a key remembered by the main site also opens the report). The loader page docs/reports/<name>.html and
docs/assets/report.js are checked in by hand.
Before sealing, the page is made site-safe: Signal Atlas links become relative, Google Fonts and inline scripts
are dropped (the site CSP allows neither; report.js runs the claim filter).
"""
import gzip, json, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from dcdas.db import web_passphrase                      # noqa: E402
from dcdas.export_web import derive_key, seal, KDF_ITER  # noqa: E402
import base64                                            # noqa: E402

ATLAS = "https://momobacon-wq.github.io/signal-atlas/"


def site_safe(html: str) -> str:
    html = html.replace(ATLAS, "../")
    html = re.sub(r'<link[^>]+fonts\.(googleapis|gstatic)\.com[^>]*>\s*', "", html)
    html = re.sub(r"<script\b.*?</script>\s*", "", html, flags=re.S)
    return "<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"></head><body>" + html + "</body></html>"


def main():
    src, name = Path(sys.argv[1]), sys.argv[2]
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise SystemExit("name: lowercase letters, digits and dashes only")
    meta = json.loads((REPO / "docs/data/meta.json").read_text(encoding="utf-8"))
    kdf = meta["kdf"]
    assert int(kdf["iter"]) == KDF_ITER
    key = derive_key(web_passphrase(), base64.b64decode(kdf["salt"]))
    html = site_safe(src.read_text(encoding="utf-8"))
    out = REPO / "docs/reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.bin").write_bytes(seal(key, gzip.compress(html.encode("utf-8"), mtime=0), f"reports/{name}"))
    (out / f"{name}.json").write_text(json.dumps({"enc": 1, "gzip": 1, "kdf": kdf}, ensure_ascii=False), encoding="utf-8")
    print(f"sealed {name}: {len(html):,} chars -> {(out / f'{name}.bin').stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
