# -*- coding: utf-8 -*-
"""Cache-busting stamp for the static site (GitHub Pages serves every file with max-age=600 and no versioned URLs).

Usage:  py tools/stamp_assets.py [docs]

* data/manifest.json "build" = hash of every file under data/ (computed by export_web; recomputed here if missing).
* index.html: every assets/*.js|css reference gets ?v=<sha256 of that file>[:10]; <meta name="atlas-build"> gets the
  data build + app hash; the manifest preload gets ?v=<build>.
* version.json = {"build", "app"}: an open tab re-fetches it (no-cache) and offers a reload when either changed.
Idempotent and deterministic (no timestamps). export-web runs it at the end; run by hand after editing docs/assets.
"""
import glob
import hashlib
import json
import os
import re
import sys


def sha(data, n=10):
    return hashlib.sha256(data).hexdigest()[:n]


def data_build(data_dir):
    h = hashlib.sha256()
    files = []
    for root, _d, fs in os.walk(data_dir):
        for fn in fs:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, data_dir).replace(os.sep, "/")
            if rel != "manifest.json":
                files.append((rel, p))
    for rel, p in sorted(files):
        h.update(rel.encode("utf-8") + b"\0")
        with open(p, "rb") as f:
            h.update(f.read())
    with open(os.path.join(data_dir, "manifest.json"), "rb") as f:
        man = json.loads(f.read().decode("utf-8"))
    man.pop("build", None)
    h.update(json.dumps(man, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()[:10]


def stamp(docs):
    docs = os.path.abspath(docs)
    data_dir = os.path.join(docs, "data")
    man_path = os.path.join(data_dir, "manifest.json")
    meta_path = os.path.join(data_dir, "meta.json")
    build = ""
    if os.path.exists(meta_path):                      # encrypted export: build lives in the plaintext meta.json
        with open(meta_path, "rb") as f:
            build = json.loads(f.read().decode("utf-8")).get("build") or ""
    elif os.path.exists(man_path):
        with open(man_path, "rb") as f:
            man = json.loads(f.read().decode("utf-8"))
        build = man.get("build") or ""
        if not build:
            build = data_build(data_dir)
            man["build"] = build
            with open(man_path, "wb") as f:
                f.write(json.dumps(man, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    assets = os.path.join(docs, "assets")
    fh = {}
    for p in sorted(glob.glob(os.path.join(assets, "*.js")) + glob.glob(os.path.join(assets, "*.css"))):
        with open(p, "rb") as f:
            fh[os.path.basename(p)] = sha(f.read())
    app = sha("".join("%s=%s;" % kv for kv in sorted(fh.items())).encode("utf-8"))

    ix_path = os.path.join(docs, "index.html")
    if os.path.exists(ix_path):
        with open(ix_path, "r", encoding="utf-8") as f:
            html = f.read()
        orig = html

        def ref(m):
            name = m.group(2)
            return m.group(1) + "assets/" + name + ("?v=" + fh[name] if name in fh else "") + m.group(4)
        html = re.sub(r'((?:src|href)=")assets/([\w.-]+\.(?:js|css))(\?v=[0-9a-zA-Z]*)?(")', ref, html)
        html = re.sub(r'<meta name="atlas-build"[^>]*>',
                      '<meta name="atlas-build" content="%s" data-app="%s">' % (build, app), html)
        html = re.sub(r'(<link rel="preload" href=")data/(meta|manifest)\.json(\?v=[0-9a-zA-Z]*)?(")',
                      r'\g<1>data/\g<2>.json?v=%s\g<4>' % build, html)
        if html != orig:
            with open(ix_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(html)
    ver = {"build": build, "app": app}
    with open(os.path.join(docs, "version.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ver, separators=(",", ":")))
    return ver


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(here), "docs")
    print(json.dumps(stamp(d)))
