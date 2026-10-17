# -*- coding: utf-8 -*-
"""Create or refresh a sub-site page docs/<site>/index.html from the root page docs/index.html.

Usage:  py tools/make_subsite.py [site] [--docs DOCS] [--check]

* site defaults to the configured site (DCDAS_SITE / DCDAS_CONFIG "site"); the default site has no sub-site page.
* The page is stamp_assets.subsite_html(root page): assets/ and auth-config.json are read from the parent folder
  (../assets/, ../auth-config.json: one login gate, one copy of the front end), data/ stays relative (each site has its
  own data/ and version.json), and the page carries <meta name="atlas-site" content="<site>">, which namespaces the
  browser storage (atlas.<site>.key ...) in core.js / report.js. verify_web compares the page with this derivation.
* No auth-config.json, 404.html or robots.txt is written into the sub-site folder: the root copies serve it.
  docs/404.html must list the site in its SUBS array (checked here) so a mistyped /<site>/... path returns to the
  sub-site's home page instead of the root site's.
* After writing, the root folder is stamped (stamp_assets.stamp), which stamps every sub-site too.
--check writes nothing and exits 1 when the page is missing or differs (modulo stamps).
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stamp_assets  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="derive docs/<site>/index.html from docs/index.html")
    ap.add_argument("site", nargs="?", help="site token (default: the configured site)")
    ap.add_argument("--docs", default=os.path.join(os.path.dirname(HERE), "docs"), help="root docs folder")
    ap.add_argument("--check", action="store_true", help="only compare, write nothing")
    a = ap.parse_args(argv)
    site = a.site
    if not site:
        from dcdas import db as dbm
        site = dbm.site()
    if not site:
        print("ERROR: no site given and none configured (the default site uses docs/index.html itself)")
        return 2
    docs = os.path.abspath(a.docs)
    root_ix = os.path.join(docs, "index.html")
    with open(root_ix, "r", encoding="utf-8") as f:
        root_html = f.read()
    try:
        want = stamp_assets.subsite_html(root_html, site)
    except ValueError as e:
        print(f"ERROR: cannot derive the {site} page from {os.path.relpath(root_ix, os.path.dirname(docs))}: {e}")
        return 2
    sub = os.path.join(docs, site)
    ix = os.path.join(sub, "index.html")
    got = None
    if os.path.exists(ix):
        with open(ix, "r", encoding="utf-8") as f:
            got = f.read()
    same = got is not None and stamp_assets.unstamp(got) == stamp_assets.unstamp(want)

    p404 = os.path.join(docs, "404.html")
    if os.path.exists(p404):
        with open(p404, "r", encoding="utf-8") as f:
            m = re.search(r"var SUBS = \[([^\]]*)\]", f.read())
        if not m or ("'%s'" % site) not in m.group(1):
            print(f"WARN: 404.html does not list '{site}' in SUBS: mistyped /{site}/... paths land on the root site")

    if a.check:
        print(f"{site}/index.html " + ("matches the root page (modulo stamps)" if same else
                                      ("missing" if got is None else "differs from the root page: run make_subsite")))
        return 0 if same else 1
    if same:
        print(f"{site}/index.html already matches the root page (modulo stamps)")
    else:
        os.makedirs(sub, exist_ok=True)
        with open(ix, "w", encoding="utf-8", newline="\n") as f:
            f.write(want)
        print(f"{'wrote' if got is None else 'updated'} {site}/index.html")
    subs = []
    ver = stamp_assets.stamp(docs, subs)
    print("stamp root", ver)
    for s, v in subs:
        print("stamp", s, v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
