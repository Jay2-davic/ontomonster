#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Regenerate the figures of the User Guide from the live UI.

The script does not need the backend. It serves the static index.html
file of the app and replaces the API with small sample data. It then
operates the real UI with Playwright and writes the PNG files to
docs/img/. Run the script after each UI change. This keeps docs/GUIDE.md
correct.

Usage:
    pip install playwright
    python -m playwright install chromium
    python dev/capture_docs.py
    python dev/capture_docs.py --only glossary menu

The figure groups are: chooser, overview, menu, reference, glossary,
scan and selectors. The script does not capture the map figures (bloom,
focus, star, controls and themes). Capture them by hand.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "src", "ontomonster", "static", "index.html")
OUT = os.path.join(ROOT, "docs", "img", "guide")
OUT_ROOT = os.path.join(ROOT, "docs", "img")

# Sample data that the stubs return.
SAMPLE_ONTOS = [  # Each entry is (prefix, domain).
    ("bfo", "upper"),
    ("cco", "upper"),
    ("iao", "upper"),
    ("chebi", "chemistry"),
    ("chmo", "chemistry"),
    ("cheminf", "chemistry"),
    ("go", "biology"),
    ("uberon", "biology"),
    ("pato", "biology"),
    ("mondo", "medical"),
    ("hp", "medical"),
    ("qudt", "units & quantities"),
    ("unit", "units & quantities"),
    ("iof", "materials & engineering"),
    ("pmd", "materials & engineering"),
    ("bibo", "information & metadata"),
    ("envo", "environment & agriculture"),
]
GLOSS_ROWS = [
    {
        "code": "ont00000594",
        "label": "hundred polished grain weight",
        "alt": "HPLSGRWT|TO_0000594",
        "iri": "http://purl.obolibrary.org/obo/TO_0000594",
        "def": "The weight of one hundred polished grains after milling.",
        "kind": "class",
    },
    {
        "code": "ont00000253",
        "label": "Information Bearing Entity",
        "alt": "IBE|information-bearing-entity-long-synonym",
        "iri": "https://www.commoncoreontologies.org/ont00000253",
        "def": "An entity that carries information content.",
        "kind": "class",
    },
    {
        "code": "BFO_0000176",
        "label": "continuant part of",
        "alt": "",
        "iri": "http://purl.obolibrary.org/obo/BFO_0000176",
        "def": "A core BFO parthood relation between continuants.",
        "kind": "prop",
    },
]


def _index_with_hook(dst):
    """Copy index.html with the hook window.__addOnto.

    The capture script uses the hook to add ontologies.
    """
    s = open(INDEX, encoding="utf-8").read()
    s = s.replace(
        "\n})();\n\n  (function(){function setT",
        "\n  window.__addOnto=addOnto;\n})();\n\n  (function(){function setT",
        1,
    )
    open(dst, "w", encoding="utf-8").write(s)


class _Server(socketserver.TCPServer):
    # Set before the bind, so a quick second run can reuse the port.
    allow_reuse_address = True


def _serve(dirpath):
    h = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=dirpath
    )
    srv = _Server(("127.0.0.1", 0), h)  # port 0: the OS picks a free port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


SAMPLE_TERMS = [  # (name, parent)
    ("Entity", None),
    ("Item", "Entity"),
    ("Item A", "Item"),
    ("Item B", "Item"),
    ("Category", "Entity"),
    ("Attribute", "Entity"),
]
# The chooser figure needs the app closed. Other figures need it open.
STATE = {"open": False}


def _sample_terms():
    terms = []
    for name, parent in SAMPLE_TERMS:
        kids = [{"pred": "rdf:type", "val": "owl:Class"}]
        if parent:
            kids.append(
                {"pred": "rdfs:subClassOf", "val": parent.replace(" ", "")}
            )
        kids.append({"pred": "rdfs:label", "val": '"%s"' % name})
        kids.append(
            {
                "pred": "skos:definition",
                "val": '"An example %s."' % name.lower(),
            }
        )
        terms.append({"name": ":" + name.replace(" ", ""), "children": kids})
    return terms


def _status():
    if not STATE["open"]:
        return {"open": False}
    return {
        "open": True,
        "project": {
            "name": "internal-ontology",
            "slug": "internal-ontology",
            "prefix": "",
            "namespace": "",
        },
        "references": [],
        "workspaces": [
            {
                "slug": "sample",
                "version": None,
                "groups": [],
                "versions": [],
                "source": "org",
                "terms": _sample_terms(),
            }
        ],
        "org": "myorg",
        "orgs": ["myorg"],
    }


def _route(r):
    u = r.request.url

    def ok(obj):
        r.fulfill(
            status=200, content_type="application/json", body=json.dumps(obj)
        )

    if "project/status" in u:
        ok(_status())
    elif "config/roots" in u:
        ok(
            {
                "reference_root": "/path/to/reference-ontologies",
                "internal_root": "/path/to/internal-ontology",
                "reference_exists": True,
                "internal_exists": True,
            }
        )
    elif "/api/domains" in u:
        ok({"domains": {p: d for p, d in SAMPLE_ONTOS}})
    elif "/api/glossary" in u:
        ok(
            {
                "rows": GLOSS_ROWS,
                "total": len(GLOSS_ROWS),
                "letters": ["B", "C", "H", "I"],
            }
        )
    elif "refcache/progress" in u:
        ok({"phase": "ready"})
    elif "/api/" in u:
        ok({})
    else:
        r.continue_()


INJECT = (
    "()=>{var s=%s;s.forEach(function(p){window.__addOnto(p,'http://o/'+p+'/',"
    "[{c:p.toUpperCase()+'_1',l:p+' example class'}],[]);});}"
    % json.dumps([p for p, _ in SAMPLE_ONTOS])
)


def main(argv):
    ap = argparse.ArgumentParser(description="Regenerate User Guide figures.")
    ap.add_argument(
        "--only",
        nargs="*",
        default=None,
        help=(
            "limit to groups: chooser overview menu reference "
            "glossary scan selectors"
        ),
    )
    args = ap.parse_args(argv)
    want = set(args.only) if args.only else None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "Install Playwright:  pip install playwright && python -m "
            "playwright install chromium"
        )

    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp()
    # The page loads its logo from /static/. Serve it too.
    os.makedirs(os.path.join(tmp, "static"))
    shutil.copy(
        os.path.join(os.path.dirname(INDEX), "logo.png"),
        os.path.join(tmp, "static", "logo.png"),
    )
    _index_with_hook(os.path.join(tmp, "index.html"))
    srv = _serve(tmp)
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"

    def do(group):
        return want is None or group in want

    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])

            def clip(pg, sel, pad=6):
                bb = pg.eval_on_selector(
                    sel,
                    "e=>{const "
                    "r=e.getBoundingClientRect();return{x:r.x,y:r.y,"
                    "width:r.width,height:r.height};}",
                )
                return {
                    "x": max(0, bb["x"] - pad),
                    "y": max(0, bb["y"] - pad),
                    "width": bb["width"] + 2 * pad,
                    "height": bb["height"] + 2 * pad,
                }

            def shot(name, pg, sel=None, h=None, pad=6, folder=OUT):
                c = clip(pg, sel, pad) if sel else None
                if c and h:
                    c["height"] = min(c["height"], h)
                pg.screenshot(path=os.path.join(folder, name + ".png"), clip=c)
                print("  shot", name)

            # Capture the chooser. It uses its own page with the overlay
            # open.
            if do("chooser"):
                pg = b.new_page(
                    viewport={"width": 760, "height": 560},
                    device_scale_factor=2,
                )
                pg.route("**/api/**", _route)
                pg.goto(url, wait_until="domcontentloaded")
                time.sleep(1.4)
                if pg.query_selector("#pOverlay"):
                    shot(
                        "start_loading",
                        pg,
                        (
                            "#pOverlay .pbox, #pOverlay"
                            if pg.query_selector("#pOverlay .pbox")
                            else "#pOverlay"
                        ),
                        pad=0,
                    )
                pg.close()

            # Load the app with sample ontologies for all other figures.
            STATE["open"] = True
            pg = b.new_page(
                viewport={"width": 1460, "height": 920}, device_scale_factor=2
            )
            pg.route("**/api/**", _route)
            pg.goto(url, wait_until="domcontentloaded")
            time.sleep(1.4)
            pg.evaluate(
                "()=>{var "
                "o=document.getElementById('pOverlay');if(o)o.remove();}"
            )
            pg.evaluate(
                "()=>{var "
                "o=document.getElementById('refov');"
                "if(o)o.classList.remove('show');}"
            )
            pg.evaluate(INJECT)
            time.sleep(0.3)

            if do("selectors"):
                # Frame the Org and the Workspace selectors together.
                box = pg.evaluate(
                    "()=>{const a=document.getElementById('orgSelect')"
                    ".getBoundingClientRect(),b=document.getElementById("
                    "'wsSelect').getBoundingClientRect();return {x:Math.min("
                    "a.x,b.x),y:Math.min(a.y,b.y),r:Math.max(a.right,b.right),"
                    "b:Math.max(a.bottom,b.bottom)};}"
                )
                pg.screenshot(
                    path=os.path.join(OUT, "ws_select.png"),
                    clip={
                        "x": max(0, box["x"] - 55),
                        "y": max(0, box["y"] - 24),
                        "width": box["r"] - box["x"] + 107,
                        "height": box["b"] - box["y"] + 48,
                    },
                )
                print("  shot ws_select")
            if do("overview"):
                pg.evaluate(
                    "()=>{var t=[...document.querySelectorAll("
                    "'#termlist .term')].find(e=>/Item A/.test("
                    "e.textContent));if(t)t.click();}"
                )
                time.sleep(0.8)
                shot("01_overview", pg, folder=OUT_ROOT)
            if do("menu"):
                pg.click("#menuBtn")
                time.sleep(0.4)
                shot("II_menu_dropdown", pg, "#menuMenu", pad=8)
                pg.click("#menuBtn")
                time.sleep(0.2)
            if do("reference"):
                pg.click("#aboutBtn")
                time.sleep(0.4)
                shot("II_about_dropdown", pg, "#aboutMenu", pad=8)
                pg.click("#aboutBtn")
                time.sleep(0.2)
            if do("scan"):
                pg.click("#scanBtn")
                time.sleep(0.8)
                if pg.query_selector("#scanov") and pg.is_visible("#scanov"):
                    shot("II_scan_window", pg, "#scanov .scanbox")
                    pg.keyboard.press("Escape")
                    time.sleep(0.2)
            if do("glossary"):
                pg.evaluate(
                    "()=>{var "
                    "b=document.getElementById('viewOntosBtn');if(b)b.click();"
                    "}"
                )
                time.sleep(1.0)
                if pg.query_selector("#glossov"):
                    pg.eval_on_selector_all(
                        "#glosstabs .ggrp",
                        "e=>{e.filter(x=>"
                        "/chemistry|materials/i.test(x.textContent))"
                        ".forEach(x=>x.click());}",
                    )
                    time.sleep(0.3)
                    shot("about_glossary", pg, "#glossov .glossbox", pad=4)
                    pg.fill("#glosssearch", "grain")
                    time.sleep(0.7)
                    shot(
                        "about_glossary_search",
                        pg,
                        "#glossov .glossbox",
                        pad=4,
                    )
            pg.close()
            b.close()
    finally:
        srv.shutdown()
    print(
        "Done. Figures written to docs/img/guide/ . Review `git diff`, then "
        "commit."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
