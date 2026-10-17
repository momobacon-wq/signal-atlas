# -*- coding: utf-8 -*-
"""Cache-busting stamp for the static site (GitHub Pages serves every file with max-age=600 and no versioned URLs).

Usage:  py tools/stamp_assets.py [docs]

* data/manifest.json "build" = hash of every file under data/ (computed by export_web; recomputed here if missing).
* index.html: every assets/*.js|css reference (also ../assets/ from a sub-site) gets ?v=<sha256 of that file>[:10];
  <meta name="atlas-build"> gets the data build + app hash; the manifest preload gets ?v=<build>.
* version.json = {"build", "app"}: a record of what the folder was stamped with (no page fetches it today).
* Sub-sites: a folder docs/<site>/ whose index.html carries <meta name="atlas-site" content="<site>"> shares
  docs/assets/ through ../assets/ and has its own data/ and version.json. Stamping the root folder also stamps every
  such sub-site (each with its own data build); stamping a sub-site folder stamps only that folder.
Idempotent and deterministic (no timestamps). export-web runs it at the end; run by hand after editing docs/assets.
Dependency-free on purpose: export_web loads this file by path.
"""
import glob
import hashlib
import json
import os
import re
import sys

ASSET_REF_RE = re.compile(r'((?:src|href)=")((?:\.\./)*)assets/([\w.-]+\.(?:js|css))(\?v=[0-9a-zA-Z]*)?(")')
SITE_META_RE = re.compile(r'<meta\s+name="atlas-site"\s+content="([^"]*)"')
RESERVED_SITES = {"data", "assets", "reports"}   # = dcdas.db.RESERVED_SITES (this file stays dependency-free)


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


def page_site(html):
    """The atlas-site token of a page ("" = the default site's page)."""
    m = SITE_META_RE.search(html)
    return m.group(1) if m else ""


def assets_dir_for(docs):
    """<docs>/assets when the folder has its own, else the shared <docs>/../assets of a sub-site."""
    own = os.path.join(docs, "assets")
    return own if os.path.isdir(own) else os.path.join(os.path.dirname(docs), "assets")


def _build(docs):
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
    return build                                       # "" when the folder has no data yet (a new sub-site)


def _asset_hashes(assets):
    fh = {}
    for p in sorted(glob.glob(os.path.join(assets, "*.js")) + glob.glob(os.path.join(assets, "*.css"))):
        with open(p, "rb") as f:
            fh[os.path.basename(p)] = sha(f.read())
    app = sha("".join("%s=%s;" % kv for kv in sorted(fh.items())).encode("utf-8"))
    return fh, app


def _stamp_one(docs, assets):
    build = _build(docs)
    fh, app = _asset_hashes(assets)
    ix_path = os.path.join(docs, "index.html")
    if os.path.exists(ix_path):
        with open(ix_path, "r", encoding="utf-8") as f:
            html = f.read()
        orig = html

        def ref(m):
            name = m.group(3)
            return m.group(1) + m.group(2) + "assets/" + name + ("?v=" + fh[name] if name in fh else "") + m.group(5)
        html = ASSET_REF_RE.sub(ref, html)
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


def subsites(docs):
    """[(site, folder)] of the sub-sites under a root docs folder: docs/*/index.html carrying a non-empty atlas-site."""
    out = []
    for ix in sorted(glob.glob(os.path.join(docs, "*", "index.html"))):
        with open(ix, "r", encoding="utf-8") as f:
            s = page_site(f.read())
        if s:
            out.append((s, os.path.dirname(ix)))
    return out


def stamp(docs, sub_out=None):
    """Stamp one site folder. For the root folder (it holds assets/) every sub-site is stamped too; their versions are
    appended to sub_out as (site, ver) when a list is given. Returns the folder's own {"build", "app"}."""
    docs = os.path.abspath(docs)
    root = os.path.isdir(os.path.join(docs, "assets"))
    assets = assets_dir_for(docs)
    ver = _stamp_one(docs, assets)
    if root:
        for s, d in subsites(docs):
            v = _stamp_one(d, assets_dir_for(d))     # its own assets/ if it has one, else the shared ../assets
            if sub_out is not None:
                sub_out.append((s, v))
    return ver


# ---- sub-site page: derived from the root page (one source of truth; verify_web checks a sub-site against it)
def subsite_html(root_html, site):
    """docs/<site>/index.html from docs/index.html: assets and the auth config are read from the parent folder, the
    login gate names the site (data-site "Signal Atlas <SITE>", upper case; it is the 'site' field of the login log), and the page carries <meta name="atlas-site" content="<site>">. data/ stays relative
    (each site has its own). Raises ValueError when an anchor is missing, so a changed root page is noticed."""
    if not re.fullmatch(r"[a-z0-9_-]+", site or ""):
        raise ValueError(f"site {site!r}: lowercase letters, digits, '_' and '-' only")
    if site in RESERVED_SITES:
        raise ValueError(f"site {site!r}: {', '.join(sorted(RESERVED_SITES))} are folders of the root site")
    if page_site(root_html):
        raise ValueError("the root page already carries atlas-site")
    html, n = re.subn(r'((?:src|href)=")assets/', r"\1../assets/", root_html)
    if not n:
        raise ValueError("no assets/ reference in the root page")
    html, n = re.subn(r'(<meta name="ams-auth-config" content=")auth-config\.json(")', r"\1../auth-config.json\2", html)
    if n != 1:
        raise ValueError("auth config meta not found exactly once")
    html, n = re.subn(r'(<meta name="ams-auth-config"[^>]*\sdata-site=")Signal Atlas(")', r"\1Signal Atlas %s\2" % site.upper(), html)
    if n != 1:
        raise ValueError('data-site="Signal Atlas" not found exactly once')
    html, n = re.subn(r'(<meta name="atlas-build"[^>]*>)', r'\1\n<meta name="atlas-site" content="%s">' % site, html)
    if n != 1:
        raise ValueError("atlas-build meta not found exactly once")
    return html


def unstamp(html):
    """A page with every stamp value blanked (?v=, atlas-build content / data-app), for comparing pages modulo stamps."""
    html = ASSET_REF_RE.sub(lambda m: m.group(1) + m.group(2) + "assets/" + m.group(3) + m.group(5), html)
    html = re.sub(r'<meta name="atlas-build"[^>]*>', '<meta name="atlas-build">', html)
    return re.sub(r'(<link rel="preload" href="data/(?:meta|manifest)\.json)(\?v=[0-9a-zA-Z]*)?(")', r"\1\3", html)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(here), "docs")
    subs = []
    print(json.dumps(stamp(d, subs)))
    for s, v in subs:
        print(json.dumps({"site": s, **v}))
