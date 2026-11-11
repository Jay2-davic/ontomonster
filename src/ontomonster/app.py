# SPDX-License-Identifier: BSD-3-Clause
"""Local web app of OntoMonster.

The app works in a folder: the Internal Ontology Location. This folder
holds the saves of the user and the exported ontologies. The app loads
reference ontologies from the Reference Ontology Location. The user
edits terms. The app saves working drafts to Parquet files and writes
validated exports in five formats to the org folders.

To run the app, use ./run.sh or python -m ontomonster. Open the URL
that the command prints.

Everything is local. The app uses no cloud service. The references come
from disk.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Configure logging based on ONTO_MONSTER_LOG environment variable.
log_level = os.environ.get("ONTO_MONSTER_LOG", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(message)s"
)
logger = logging.getLogger("onto_monster")
# Hide refcache/progress from uvicorn access log.
logging.getLogger("uvicorn.access").addFilter(
    lambda record: "/api/refcache/progress" not in record.getMessage()
)

from .ontology_loader import load_ontologies
from . import refcache
from .project_io import import_project_from_text, project_to_turtle
from .project import Project, ProjectError, slugify, default_namespace
from . import parquet_store as pqs
from .assemble import assemble

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
ONTO_DIR = (
    BASE / "data" / "ontologies"
)  # The folder of the bundled, built-in reference ontologies.
ONTO_DIR.mkdir(exist_ok=True)

# Store the state outside the repo. A write to it then never starts the
# --reload watcher.
STATE_FILE = Path.home() / ".ontomonster-state.json"

TERM_EXTS = (".ttl", ".owl", ".rdf", ".n3", ".nt")

app = FastAPI(title="OntoMonster")

STATE: dict = {
    "project": None,
    "org": None,
}  # The open project and the selected org level (local, one user).

# The roots of the two sibling repos that the app works with:
# - reference_root: the input repo with the reference ontologies (CCO,
#   BFO and others).
# - internal_root: the output repo where the built ontologies are.
ROOTS: dict = {"reference_root": "", "internal_root": ""}


def _roots_from_disk() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {
            "reference_root": d.get("reference_root", "") or "",
            "internal_root": d.get("internal_root", "") or "",
        }
    except Exception:  # noqa: BLE001
        return {"reference_root": "", "internal_root": ""}


def _persist_state(pr: "Project | None" = None) -> None:
    """Save the open project and the configured roots."""
    try:
        data = _roots_from_disk()
        data.update(ROOTS)
        if pr:
            data["root"] = str(pr.root)
        STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.debug("state: could not persist state: %s", e)


# Load the roots when the module is imported.
ROOTS.update(_roots_from_disk())


def _remember(pr: "Project | None") -> None:
    _persist_state(pr)


def _recall() -> "Project | None":
    # Start with a blank session at each launch. The app does not open
    # the last project again. The user opens or creates a project in
    # each session. The saved work stays in the project folder,
    # unchanged.
    return None


def _current() -> "Project | None":
    if STATE["project"] is None:
        pr = _recall()
        if pr:
            STATE["project"] = pr
    return STATE["project"]


def _ttl_workspaces(pr: "Project", taken: set) -> tuple[list, list]:
    """Read each ttl/ input as a workspace.

    Skip each slug that is already saved. The Parquet data wins, so the
    code never overwrites edits. Return (workspaces, filenames).
    """
    out, names = [], []
    for f in sorted(pr.ttl_dir.glob("*")):
        if f.suffix.lower() not in TERM_EXTS:
            continue
        names.append(f.name)
        slug = slugify(f.stem)
        if slug in taken:
            continue
        taken.add(slug)
        try:
            hint = "xml" if f.suffix.lower() in (".owl", ".rdf") else None
            proj = import_project_from_text(
                f.read_text(encoding="utf-8", errors="replace"), hint
            )
            out.append(
                {
                    "slug": slug,
                    "version": None,
                    "terms": proj.get("terms", []),
                    "cited": [],
                    "groups": proj.get("groups", []),
                    "versions": [],
                    "source": "ttl",
                    "file": f.name,
                }
            )
            logger.debug("ttl: loaded %s -> %d terms", f.name, len(proj.get('terms', [])))
        except Exception as e:  # noqa: BLE001
            logger.debug("ttl: could not parse %s: %s", f.name, e)
    return out, names


_RESERVED_DIRS = {
    "save",
    "refcache",
    "ontologies",
    "ttl",
    "_file_template",
    ".git",
}


def _is_org_folder(d) -> bool:
    """Return True if the folder is an org.

    An org is a folder that the user made with the convention. It is an
    org if it holds a real workspace, or if it has the org structure
    folders (<org>_consolidate or <org>_workspace). An org that the user
    made on purpose then appears, also if it is empty. A random folder
    with none of these is not an org.
    """
    try:
        subs = [c.name for c in d.iterdir() if c.is_dir()]
    except Exception:  # noqa: BLE001
        return False
    # The folder has the org structure that the README describes. It is
    # an org, also if it is empty.
    if any(
        n.endswith("_consolidate") or n.endswith("_workspace") for n in subs
    ):
        return True
    # Otherwise it is an org only if it holds real workspace content (a
    # term file or a save draft). Ignore the consolidate and template
    # artefacts.
    try:
        for p in d.rglob("*"):
            if any(
                part.endswith("_consolidate") or "template" in part.lower()
                for part in p.parts
            ):
                continue
            if not p.is_file():
                continue
            if p.suffix.lower() in TERM_EXTS and not _is_shacl_file(p.name):
                return True
            if (
                p.name.lower() in ("working.parquet", "history.parquet")
                and p.stat().st_size > 0
            ):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _list_orgs(pr: "Project") -> list:
    """Return the org folders of the Internal Ontology Location.

    Use _is_org_folder to decide. Exclude reserved folders and template
    folders.
    """
    out = []
    try:
        for d in sorted(pr.root.iterdir()):
            if (
                d.is_dir()
                and not _is_template_name(d.name)
                and _is_org_folder(d)
            ):
                out.append(d.name)
    except Exception:  # noqa: BLE001
        pass
    return out


def _ws_save_dir(
    pr: "Project", org: str, slug: str = None, folder: str = None
):
    """Return the save folder of an org: <internal>/<org>/save.

    This folder holds all workspaces of the org. The workspace slug is
    the key. If no org is selected, return the pooled folder
    pr.save_dir.
    """
    if org:
        return pr.root / org / "save"
    return pr.save_dir


def _is_shacl_file(name: str) -> bool:
    n = name.lower()
    return n.endswith("_shacl.ttl") or n.endswith(".shacl.ttl")


def _is_template_name(name: str) -> bool:
    n = name.lower()
    return (
        "template" in n
        or n in _RESERVED_DIRS
        or n.startswith(".")
        or n.endswith("_consolidate")
    )


def _emit_ws(out, pr, org, slug, folder, cand, wdir=None):
    """Add one workspace to the list.

    A save draft wins over the committed .ttl file. The draft comes from
    the save folder of the org (<org>/save/) or from the earlier pooled
    save. The argument folder is a display name.
    """
    draft = None
    try:
        # One save for each org.
        osd = (pr.root / org / "save") if org else None
        if osd and osd.is_dir():
            draft = pqs.load_working(osd, slug)
    except Exception:  # noqa: BLE001
        draft = None
    if draft is None:
        try:
            draft = pqs.load_working(
                pr.save_dir, slug
            )  # Fallback to the pooled save (before the migration).
        except Exception:  # noqa: BLE001
            draft = None
    if draft is None and cand is None:
        return False
    if draft is not None:
        out.append(
            {
                "slug": slug,
                "version": None,
                "draft": True,
                "terms": draft["terms"],
                "cited": draft.get("cited", []),
                "groups": draft.get("groups", []),
                "versions": [],
                "source": "working",
                "org": org,
                "folder": folder,
            }
        )
        logger.debug("org:%s: restored draft %s -> %d terms", org, folder or slug, len(draft['terms']))
        return True
    try:
        hint = "xml" if cand.suffix.lower() in (".owl", ".rdf") else None
        proj = import_project_from_text(
            cand.read_text(encoding="utf-8", errors="replace"), hint
        )
        out.append(
            {
                "slug": slug,
                "version": None,
                "terms": proj.get("terms", []),
                "cited": [],
                "groups": proj.get("groups", []),
                "versions": [],
                "source": "org",
                "org": org,
                "folder": folder,
                "file": cand.name,
            }
        )
        logger.debug("org:%s: loaded %s -> %d terms", org, cand, len(proj.get('terms', [])))
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("org:%s: could not parse %s: %s", org, cand, e)
        return False


def _org_workspaces(pr: "Project", org: str, taken: set) -> list:
    """Find the workspaces of one org in <internal>/<org>/.

    The function finds workspaces in these places: the save folder of
    the org, the subfolders of the org, the <org>_workspace container,
    and the term files in the org folder. The five export formats of one
    workspace (.ttl, .rdf, .jsonld, .yaml and .shacl) count as one
    workspace. The .ttl file or term file is the source. A save draft
    wins. The function skips template, _consolidate and reserved
    folders.
    """
    out = []
    base = pr.root / org
    if not base.is_dir():
        logger.debug("org:%s: folder not found: %s", org, base)
        return out
    logger.debug("org:%s: scanning %s -> %s", org, base, [p.name for p in sorted(base.iterdir())])

    # 1) Collect drafts from EVERY save location under the org: the save
    # folder of the org (<org>/save), each nested save folder of a
    # workspace (<org>/**/save) and the earlier pooled save. Work that
    # an earlier model saved then still loads. From now on, the save
    # folder of the org is the canonical target.
    def _harvest(sd, label):
        try:
            slugs = set(pqs.list_working(sd)) | set(pqs.list_workspaces(sd))
        except Exception:  # noqa: BLE001
            return
        for slug in sorted(slugs):
            if slug in taken:
                continue
            w = None
            try:
                w = pqs.load_working(sd, slug) or pqs.load_version(sd, slug)
            except Exception:  # noqa: BLE001
                w = None
            if w and w.get("terms") is not None:
                out.append(
                    {
                        "slug": slug,
                        "version": None,
                        "draft": True,
                        "terms": w["terms"],
                        "cited": w.get("cited", []),
                        "groups": w.get("groups", []),
                        "versions": [],
                        "source": "working",
                        "org": org,
                        "folder": slug,
                    }
                )
                taken.add(slug)
                logger.debug("org:%s: restored '%s' from %s (%d terms)", org, slug, label, len(w['terms']))

    _harvest(base / "save", org + "/save")
    for sub in sorted(
        base.rglob("save")
    ):  # Each nested save folder of a workspace (earlier model).
        if sub.is_dir() and sub != base / "save":
            _harvest(sub, str(sub.relative_to(base)))
    _harvest(pr.save_dir, "pooled save")  # The earlier pooled save.
    # 2) Collect the committed .ttl workspaces that a draft did not
    # restore.
    _collect_ws(pr, org, base, taken, out, depth=0, is_org_root=True)
    if not out:
        logger.debug("org:%s: no workspaces found under %s", org, base)
    return out


def _collect_ws(pr, org, d, taken, out, depth=0, is_org_root=False):
    """Find the workspaces under d, with recursion.

    A folder with one or more term files is a workspace. Its five
    formats count as one. If the folder has many different stems, each
    stem is a workspace. A folder with no term files is a container. The
    function goes into it. This handles <org>/<ws>/,
    <org>/<container>/<ws>/ and files directly in <org>/.
    """
    if depth > 5:
        return
    try:
        children = sorted(d.iterdir())
    except Exception:  # noqa: BLE001
        return
    tfiles = [
        f
        for f in children
        if f.is_file()
        and f.suffix.lower() in TERM_EXTS
        and not _is_shacl_file(f.name)
    ]
    stems = {}
    for f in tfiles:
        stems.setdefault(f.stem, []).append(f)

    # A folder that is not the root is a WORKSPACE if it has term files
    # or a save draft. A folder with only a draft is work in progress
    # that the user saved but did not export. It must still load.
    if not is_org_root:
        slug0 = slugify(d.name)
        has_draft = False
        try:
            sd = d / "save"
            if sd.is_dir() and pqs.load_working(sd, slug0) is not None:
                has_draft = True
            elif pqs.load_working(pr.save_dir, slug0) is not None:
                has_draft = True
        except Exception:  # noqa: BLE001
            has_draft = False
        if (not stems) and has_draft and slug0 not in taken:
            cand = d / f"{d.name}.ttl"
            if _emit_ws(
                out,
                pr,
                org,
                slug0,
                d.name,
                cand if cand.is_file() else None,
                wdir=d,
            ):
                taken.add(slug0)
                logger.debug("org:%s: workspace '%s' (from save draft)", org, d.name)
            return

    # The flat <org>_workspace container has the five files directly in
    # it. Each file stem is a workspace. The name of the workspace is
    # the stem, not the name of the container.
    is_flat_container = d.name.endswith("_workspace")

    if stems and not is_org_root and not is_flat_container:
        # A regular subfolder is a workspace, named by the folder. If it
        # has many stems, split it.
        if len(stems) == 1:
            slug = slugify(d.name)
            if slug not in taken:
                cand = d / f"{d.name}.ttl"
                if not cand.is_file():
                    cand = tfiles[0]
                if _emit_ws(out, pr, org, slug, d.name, cand, wdir=d):
                    taken.add(slug)
                    print(
                        f"[org:{org}] workspace '{d.name}' ({len(stems)} "
                        f"ontology)"
                    )
        else:
            for stem, files in sorted(stems.items()):
                slug = slugify(stem)
                if slug in taken:
                    continue
                ttl = next(
                    (x for x in files if x.suffix.lower() == ".ttl"), files[0]
                )
                if _emit_ws(out, pr, org, slug, d.name, ttl, wdir=d):
                    taken.add(slug)
            print(
                f"[org:{org}] '{d.name}' held {len(stems)} ontologies -> "
                f"separate workspaces"
            )
        return  # A workspace folder is a leaf. Do not go further into it.

    if stems and (is_org_root or is_flat_container):
        # Files directly in the org root or in the <org>_workspace
        # container: each stem is a workspace.
        for stem, files in sorted(stems.items()):
            slug = slugify(stem)
            if slug in taken:
                continue
            ttl = next(
                (x for x in files if x.suffix.lower() == ".ttl"), files[0]
            )
            if _emit_ws(
                out, pr, org, slug, stem, ttl, wdir=d
            ):  # The folder hint is the stem.
                taken.add(slug)
        if is_flat_container:
            print(
                f"[org:{org}] '{d.name}' (flat) -> {len(stems)} workspace(s)"
            )
            # The flat container is the store of workspaces. Do not go
            # further into it.
            return

    # Org root: each term file placed directly here becomes a workspace.
    if stems and is_org_root:
        for stem, files in sorted(stems.items()):
            slug = slugify(stem)
            if slug in taken:
                continue
            ttl = next(
                (x for x in files if x.suffix.lower() == ".ttl"), files[0]
            )
            if _emit_ws(out, pr, org, slug, "", ttl, wdir=d):
                taken.add(slug)

    # A container has no term files, or it is the org root. Go into each
    # subfolder that is not reserved.
    for sub in children:
        if (
            sub.is_dir()
            and not _is_template_name(sub.name)
            and sub.name not in ("save", "refcache")
        ):
            _collect_ws(pr, org, sub, taken, out, depth + 1, is_org_root=False)


def _ref_sources(pr) -> list:
    """Return the reference files to cache.

    They are the bundled built-in files, the files of the
    reference-ontologies repo (ROOTS["reference_root"]/reference_ontos)
    and any earlier ontologies/ folder of a project.
    """
    src = []
    for f in sorted(ONTO_DIR.glob("*")):
        if f.suffix.lower() in TERM_EXTS:
            src.append(
                {"path": str(f), "source": "built-in", "role": "mid-level"}
            )
    rr = ROOTS.get("reference_root", "")
    if rr:
        rdir = Path(rr) / "reference_ontos"
        base = rdir if rdir.is_dir() else Path(rr)
        for f in sorted(base.glob("*")):
            if f.suffix.lower() in TERM_EXTS:
                src.append(
                    {
                        "path": str(f),
                        "source": "reference-repo",
                        "role": "mid-level",
                    }
                )
    try:
        for f in sorted(pr.ontologies_dir.glob("*")):
            if f.suffix.lower() in TERM_EXTS:
                src.append(
                    {"path": str(f), "source": "project", "role": "mid-level"}
                )
    except Exception:  # noqa: BLE001
        pass
    return src


def _loaded_namespaces(pr) -> dict:
    """Return the merged map of prefix to IRI from the loaded reference
    ontologies.

    The map has the real @prefix and xmlns declarations, from the
    refcache. This is the source of namespaces (ground truth) for export
    and resolution. The code has no hard-coded guesses.
    """
    out = {}
    try:
        for rec in refcache.build(
            pr.refcache_dir, _ref_sources(pr), parse=False
        ):
            for p, n in (rec.get("namespaces") or {}).items():
                if p and n:
                    out.setdefault(p, n)
    except Exception as e:  # noqa: BLE001
        logger.debug("ns: could not read loaded namespaces: %s", e)
    return out


def _consolidate(pr):
    """Write the consolidated bundle of the selected org
    (<org>/<org>_consolidate/).

    The bundle is the union of the workspaces of the org. If no org is
    selected, use the earlier top-level universal/ folder. Return the
    result of the writer, or None.
    """
    org = STATE.get("org")
    prefixes = _loaded_namespaces(pr)
    if not org:
        from .assemble import write_universal

        return write_universal(pr, prefixes=prefixes)
    from .assemble import write_consolidate

    wss = _org_workspaces(pr, org, set())
    seen, terms, maxv = set(), [], "0.1"
    for w in wss:
        v = w.get("version") or "0.1"
        for t in w.get("terms") or []:
            if t.get("name") and t["name"] not in seen:
                seen.add(t["name"])
                terms.append(t)
    return write_consolidate(pr, org, terms, version=maxv, prefixes=prefixes)


def _status(include_refs: bool = True, scan_org: bool = True) -> dict:
    pr = _current()
    if not pr:
        return {"open": False}
    refs = []
    if include_refs:
        refs = refcache.build(pr.refcache_dir, _ref_sources(pr), parse=False)
    # Light mode: return just refs, skip workspace scan.
    if not scan_org:
        return {
            "open": True,
            "project": pr.to_dict(),
            "references": refs,
            "light": True,
        }
    workspaces, taken = [], set()
    working = pqs.list_working(pr.save_dir)
    hist = pqs.list_workspaces(pr.save_dir)
    order = list(hist) + [w for w in working if w not in hist]
    for slug in order:
        taken.add(slug)
        w = pqs.load_working(pr.save_dir, slug)
        if w is not None:
            lv = pqs.latest_version(pr.save_dir, slug)
            workspaces.append(
                {
                    "slug": slug,
                    "version": pqs.next_version(lv),
                    "draft": True,
                    "terms": w["terms"],
                    "cited": w["cited"],
                    "groups": w.get("groups", []),
                    "versions": pqs.list_versions(pr.save_dir, slug),
                    "source": "working",
                }
            )
        else:
            d = pqs.load_version(pr.save_dir, slug)
            workspaces.append(
                {
                    "slug": slug,
                    "version": d["version"],
                    "terms": d["terms"],
                    "cited": d["cited"],
                    "groups": d.get("groups", []),
                    "versions": pqs.list_versions(pr.save_dir, slug),
                    "source": "saved",
                }
            )
    org = STATE.get("org")
    if org:
        workspaces.extend(_org_workspaces(pr, org, taken))
        ttl_inputs = []
    else:
        ttl_ws, ttl_inputs = _ttl_workspaces(pr, taken)
        workspaces.extend(ttl_ws)
    return {
        "open": True,
        "project": pr.to_dict(),
        "references": refs,
        "workspaces": workspaces,
        "ttl_inputs": ttl_inputs,
        "org": org,
        "orgs": _list_orgs(pr),
    }


@app.post("/api/shutdown")
async def shutdown():
    """Stop the local server in a controlled way, so that the Exit
    button can quit the app.
    """
    import os
    import signal
    import threading
    import time

    def _stop():
        time.sleep(0.3)  # Let the HTTP response finish first.
        os.kill(
            os.getpid(), signal.SIGINT
        )  # uvicorn treats SIGINT as a clean shutdown.

    threading.Thread(target=_stop, daemon=True).start()
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.post("/api/project/new")
async def project_new(req: Request):
    b = await req.json()
    try:
        pr = Project.create(
            b.get("parent", ""),
            b.get("name", ""),
            b.get("prefix") or None,
            b.get("namespace") or None,
        )
        STATE["project"] = pr
        _remember(pr)
        return JSONResponse(_status())
    except ProjectError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/project/open")
async def project_open(req: Request):
    b = await req.json()
    try:
        STATE["project"] = Project.open(b.get("path", ""))
        _remember(STATE["project"])
        return JSONResponse(_status())
    except ProjectError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/project/suggest")
async def project_suggest(req: Request):
    b = await req.json()
    name = b.get("name", "")
    docs = Path.home() / "Documents"
    parent = docs if docs.is_dir() else Path.home()
    return JSONResponse(
        {
            "slug": slugify(name),
            "prefix": slugify(name),
            "namespace": default_namespace(name),
            "parent": str(parent),
        }
    )


@app.get("/api/domains")
def domains_map():
    """Return the coarse subject-domain groups from the
    reference-ontologies repo.

    The groups come from the field "domain" of each ontology in
    sources.yaml. The glossary uses them. The keys are the ontology id
    and its declared prefix. The result is empty if the repo or the
    field is not there.
    """
    out = {}
    rr = ROOTS.get("reference_root", "")
    if rr:
        p = Path(rr) / "sources.yaml"
        if p.is_file():
            try:
                import yaml as _yaml

                data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                for o in data.get("ontologies") or []:
                    d = o.get("domain")
                    if not d:
                        continue
                    if o.get("id"):
                        out[o["id"]] = d
                    if o.get("prefix"):
                        out[o["prefix"]] = d
            except Exception as e:  # noqa: BLE001
                logger.debug("domains: could not read sources.yaml: %s", e)
    return JSONResponse({"domains": out})


@app.get("/api/config/roots")
def config_roots_get():
    """Return the configured roots and whether each path exists now."""
    ref = ROOTS.get("reference_root", "")
    intl = ROOTS.get("internal_root", "")
    return JSONResponse(
        {
            "reference_root": ref,
            "internal_root": intl,
            "reference_exists": bool(ref) and Path(ref).is_dir(),
            "internal_exists": bool(intl) and Path(intl).is_dir(),
        }
    )


@app.post("/api/config/roots")
async def config_roots_set(req: Request):
    """Set one or both roots and save them.

    Check that each given path exists. Pass only the key that you want
    to change. An empty string clears a root.
    """
    b = await req.json()
    errs = []
    for key in ("reference_root", "internal_root"):
        if key in b:
            val = (b.get(key) or "").strip()
            if val and not Path(val).is_dir():
                errs.append(f"{key}: not a folder — {val}")
            else:
                ROOTS[key] = val
    _persist_state(STATE.get("project"))
    resp = {
        "reference_root": ROOTS.get("reference_root", ""),
        "internal_root": ROOTS.get("internal_root", ""),
        "reference_exists": bool(ROOTS.get("reference_root"))
        and Path(ROOTS["reference_root"]).is_dir(),
        "internal_exists": bool(ROOTS.get("internal_root"))
        and Path(ROOTS["internal_root"]).is_dir(),
    }
    if errs:
        resp["error"] = "; ".join(errs)
        return JSONResponse(resp, status_code=400)
    return JSONResponse(resp)


@app.post("/api/project/import-save")
async def project_import_save(req: Request):
    """Import previous work from a save/ folder that the user selects.

    The function reads the Parquet working drafts (and the latest
    versions). It returns the terms and groups of each workspace. The
    client can then bring them into the current view. The path can be
    the save/ folder or a folder that contains it.
    """
    b = await req.json()
    raw = (b.get("save_dir") or "").strip()
    if not raw:
        return JSONResponse({"error": "No folder given."}, status_code=400)
    sd = Path(raw).expanduser()
    if not sd.is_dir():
        return JSONResponse({"error": f"Not a folder: {sd}"}, status_code=400)
    # Accept a folder that contains save/, or the save/ folder itself.
    if (
        not (sd / "history.parquet").exists()
        and not (sd / "working.parquet").exists()
    ):
        if (sd / "save").is_dir():
            sd = sd / "save"
    out = []
    try:
        slugs = set(pqs.list_workspaces(sd)) | set(pqs.list_working(sd))
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"error": f"Could not read save data: {e}"}, status_code=400
        )
    for slug in sorted(slugs):
        w = None
        try:
            w = pqs.load_working(sd, slug) or pqs.load_version(sd, slug)
        except Exception:  # noqa: BLE001
            w = None
        if w and w.get("terms"):
            out.append(
                {
                    "slug": slug,
                    "terms": w["terms"],
                    "groups": w.get("groups", []),
                    "cited": w.get("cited", []),
                    "version": w.get("version"),
                }
            )
    if not out:
        return JSONResponse(
            {
                "error": f"No saved workspaces found in {sd}",
                "save_dir": str(sd),
                "workspaces": [],
            }
        )
    print(
        f"[import-save] {sd} -> {len(out)} workspace(s): "
        f"{[w['slug'] for w in out]}"
    )
    return JSONResponse(
        {"workspaces": out, "count": len(out), "save_dir": str(sd)}
    )


@app.post("/api/project/load")
async def project_load(req: Request):
    """Mode 1, load existing.

    The user selects a Reference Ontology Location and an Internal
    Ontology Location. The app opens the internal location directly as
    the working project (no project.toml). The references load from the
    reference location. The app REBUILDS the refcache. It does not
    restore it. The app restores the save/ data.
    """
    b = await req.json()
    ref = (
        b.get("reference_location") or ROOTS.get("reference_root") or ""
    ).strip()
    intl = (
        b.get("internal_location") or ROOTS.get("internal_root") or ""
    ).strip()
    if not intl or not Path(intl).is_dir():
        return JSONResponse(
            {"error": f"Internal Ontology Location is not a folder: {intl}"},
            status_code=400,
        )
    if ref and not Path(ref).is_dir():
        return JSONResponse(
            {"error": f"Reference Ontology Location is not a folder: {ref}"},
            status_code=400,
        )
    ROOTS["reference_root"], ROOTS["internal_root"] = ref, intl
    pr = Project(Path(intl), {})  # The internal location IS the working root.
    (pr.save_dir).mkdir(parents=True, exist_ok=True)
    (pr.refcache_dir).mkdir(parents=True, exist_ok=True)
    STATE["project"] = pr
    STATE["org"] = None
    _persist_state(pr)
    # Do NOT wait for the slow refcache parse here. Return at once with
    # a quick status (parse=False). The client then calls
    # /api/refcache/build and shows the progress overlay while the app
    # parses the references again from the reference location.
    return JSONResponse(_status())


@app.post("/api/project/create-structure")
async def project_create_structure(req: Request):
    """Mode 2, start a new project.

    Create the empty folder structure at both locations. The reference
    location gets reference_ontos/ and archive_ontos/. The internal
    location gets save/ and refcache/. Then open the internal location
    as the working project.
    """
    b = await req.json()
    ref = (b.get("reference_location") or "").strip()
    intl = (b.get("internal_location") or "").strip()
    if not ref or not intl:
        return JSONResponse(
            {
                "error": (
                    "Both a Reference and an Internal Ontology Location "
                    "are required."
                )
            },
            status_code=400,
        )
    made = []
    for base, subs in (
        (ref, ("reference_ontos", "archive_ontos")),
        (intl, ("save", "refcache")),
    ):
        root = Path(base).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        for s in subs:
            (root / s).mkdir(parents=True, exist_ok=True)
            made.append(str(root / s))
    ROOTS["reference_root"], ROOTS["internal_root"] = str(
        Path(ref).expanduser()
    ), str(Path(intl).expanduser())
    pr = Project(Path(intl).expanduser(), {})
    STATE["project"] = pr
    _persist_state(pr)
    return JSONResponse({"created": made, **_status()})


@app.get("/api/orgs")
def orgs_list():
    pr = _current()
    if not pr:
        return JSONResponse({"open": False, "orgs": [], "org": None})
    return JSONResponse(
        {"open": True, "orgs": _list_orgs(pr), "org": STATE.get("org")}
    )


@app.post("/api/org/select")
async def org_select(req: Request):
    """Switch the org level.

    The function clears the current set and loads ONLY the workspaces of
    this org. Two orgs never mix. Pass an empty org to clear the
    selection.
    """
    pr = _current()
    if not pr:
        return JSONResponse(
            {"error": "No internal location loaded."}, status_code=400
        )
    b = await req.json()
    org = (b.get("org") or "").strip()
    if org and not (pr.root / org).is_dir():
        return JSONResponse(
            {"error": f"Org level not found: {org}"}, status_code=400
        )
    STATE["org"] = org or None
    return JSONResponse(_status())


@app.get("/api/project/status")
async def project_status(req: Request):
    """Get project status. Pass light=true to skip org scan."""
    light = req.query_params.get("light") == "true"
    return JSONResponse(_status(include_refs=True, scan_org=not light))


@app.post("/api/project/namespace")
async def project_namespace(req: Request):
    """Set the prefix and namespace of the open project and save them.

    This is the one IRI base for all exported terms. It applies from the
    next export.
    """
    b = await req.json()
    pr = STATE.get("project")
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    try:
        pr.set_identity(b.get("prefix"), b.get("namespace"))
        return JSONResponse(_status())
    except ProjectError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/project/browse")
async def project_browse(req: Request):
    """Open a native OS folder picker on the machine that runs the app.

    Return the absolute path that the user chooses. The function runs
    tkinter in a short-lived subprocess. It never touches the event loop
    of the server. An empty path means that the user cancelled.
    """
    try:
        b = await req.json()
    except Exception:  # noqa: BLE001
        b = {}
    import subprocess
    import sys

    title = (b.get("title") or "Select folder").replace('"', "'")
    initial = b.get("initial") or str(Path.cwd())
    code = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r=tk.Tk();r.withdraw()\n"
        "try:\n r.attributes('-topmost',True)\nexcept Exception:\n pass\n"
        "r.update()\n"
        "p=filedialog.askdirectory(title=%r, initialdir=%r, mustexist=False)\n"
        "r.destroy()\n"
        "print(p or '')\n" % (title, initial)
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"error": f"Folder picker unavailable: {e}"}, status_code=500
        )
    if out.returncode != 0:
        msg = (out.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        return JSONResponse(
            {"error": f"Folder picker failed: {msg[0]}"}, status_code=500
        )
    return JSONResponse({"path": out.stdout.strip()})


@app.post("/api/project/refresh")
def project_refresh():
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    logger.debug("project: refresh: re-scanning ontologies/ and ttl/")
    return JSONResponse(_status())


@app.post("/api/project/save")
async def project_save(req: Request):
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    b = await req.json()
    ws = slugify(b.get("workspace", "workspace"))
    sd = _ws_save_dir(
        pr, STATE.get("org"), ws, folder=b.get("folder")
    )  # The save folder of the org. No pooling across orgs.
    try:
        sd.mkdir(parents=True, exist_ok=True)
        info = pqs.save_working(
            sd, ws, b.get("terms", []), b.get("groups", []), b.get("cited", [])
        )
        info["status"] = _status(include_refs=False)
        return JSONResponse(info)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/project/versions/{workspace}")
def project_versions(workspace: str):
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    return JSONResponse(pqs.list_versions(pr.save_dir, slugify(workspace)))


@app.post("/api/project/load-version")
async def project_load_version(req: Request):
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    b = await req.json()
    return JSONResponse(
        pqs.load_version(
            pr.save_dir, slugify(b.get("workspace", "")), b.get("version")
        )
    )


@app.post("/api/project/import-ttl")
async def project_import_ttl(req: Request):
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    b = await req.json()
    fn = b.get("filename", "")
    p = pr.ttl_dir / fn
    if not p.is_file():
        return JSONResponse(
            {"error": f"No such file in ttl/: {fn}"}, status_code=404
        )
    try:
        proj = import_project_from_text(
            p.read_text(encoding="utf-8", errors="replace")
        )
        proj["slug"] = slugify(Path(fn).stem)
        return JSONResponse(proj)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/project/export")
def project_export():
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    try:
        for ws in pqs.list_working(
            pr.save_dir
        ):  # Make a version from each draft.
            w = pqs.load_working(pr.save_dir, ws)
            if w and w["terms"]:
                pqs.save_version(
                    pr.save_dir,
                    ws,
                    w["terms"],
                    "minor",
                    w["cited"],
                    w.get("groups", []),
                )
        return JSONResponse(assemble(pr))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/project/rebuild")
async def project_rebuild(req: Request):
    """Rebuild everything from a clean state.

    The function writes again every current workspace folder and the
    universal output, from the current data only. It removes the stale
    output folders of workspaces that no longer exist. This gives the
    cleanest and newest ontology, with no old artefacts.
    """
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    b = await req.json()
    incoming = b.get("workspaces") or []
    from .assemble import _write_unit, write_universal

    current = []
    for (
        w
        # Save the current data of the app as a new working copy and
        # version.
    ) in incoming:
        slug = slugify(w.get("name", "workspace"))
        terms = w.get("terms") or []
        if not terms:
            continue
        pqs.save_working(
            pr.save_dir, slug, terms, w.get("groups", []), w.get("cited", [])
        )
        pqs.save_version(
            pr.save_dir,
            slug,
            terms,
            "minor",
            w.get("cited", []),
            w.get("groups", []),
        )
        current.append(slug)
    if not current:
        current = list(pqs.list_working(pr.save_dir))
    try:
        modules = []
        for slug in current:
            data = pqs.load_working(pr.save_dir, slug) or pqs.load_version(
                pr.save_dir, slug
            )
            if not data or not data.get("terms"):
                continue
            pd = {
                "prefix": pr.prefix,
                "iri": pr.namespace,
                "terms": data["terms"],
            }
            modules.append(
                _write_unit(
                    pr.root,
                    slug,
                    pd,
                    pr.prefix,
                    pr.namespace,
                    f"{pr.base_iri}/{slug}",
                    data.get("version") or "0.1",
                    pr.ontologies_dir,
                    imports=[pr.base_iri],
                )
            )
        uni = _consolidate(pr)
        # Remove the stale GENERATED workspace folders. They are not
        # reserved and not current, and they hold artefacts.
        reserved = {
            "save",
            "ttl",
            "ontologies",
            "refcache",
            "universal",
            "exports",
        }
        removed = []
        for d in pr.root.iterdir():
            if not d.is_dir() or d.name in reserved or d.name in current:
                continue
            if (d / f"{d.name}.ttl").exists() or (
                d / f"{d.name}.yaml"
            ).exists():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        return JSONResponse(
            {
                "written": [m["slug"] for m in modules],
                "modules": modules,
                "universal": uni,
                "removed": removed,
                "valid": all(m.get("valid", True) for m in modules)
                and uni.get("valid", True),
            }
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/project/export-workspace")
async def project_export_workspace(req: Request):
    """Export ONLY the named workspace.

    The function makes a version from the working draft of the workspace
    and writes only the files of this workspace. The other workspaces
    stay unchanged. A problem in another workspace then cannot block or
    damage this export.
    """
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    b = await req.json()
    slug = slugify(b.get("name") or b.get("workspace") or "workspace")
    sd = _ws_save_dir(
        pr, STATE.get("org"), slug, folder=b.get("folder")
    )  # The save folder of the org.
    try:
        w = pqs.load_working(sd, slug)
        if w and w["terms"]:
            pqs.save_version(
                sd, slug, w["terms"], "minor", w["cited"], w.get("groups", [])
            )
        data = pqs.load_version(sd, slug)  # Load the latest version.
        if not data or not data.get("terms"):
            return JSONResponse(
                {"error": f"Nothing to export for '{slug}'."}, status_code=400
            )
        from .assemble import _write_unit, write_universal

        ref_ns = _loaded_namespaces(
            pr
            # Prefix to IRI map from the loaded reference ontologies
            # (ground truth).
        )
        pd = {"prefix": pr.prefix, "iri": pr.namespace, "terms": data["terms"]}
        org = STATE.get("org")
        # Write the five files FLAT into the <org>_workspace/ container
        # of the org, named <slug>.*. There is no subfolder for each
        # workspace. If the org has no container, use the org root.
        if org:
            container = pr.root / org / f"{org}_workspace"
            out_root = container if container.is_dir() else (pr.root / org)
        else:
            out_root = pr.root
        out_slug = b.get("folder") or slug
        mod = _write_unit(
            out_root,
            out_slug,
            pd,
            pr.prefix,
            pr.namespace,
            f"{pr.base_iri}/{out_slug}",
            data.get("version") or "0.1",
            pr.ontologies_dir,
            imports=[pr.base_iri],
            prefixes=ref_ns,
            flat=True,
        )
        # Refresh the consolidated bundle (the union of all workspaces)
        # as a SECONDARY effect. If another workspace has invalid
        # identifiers, this export must not fail. Report a warning
        # instead.
        uni, uni_err = None, None
        try:
            uni = _consolidate(pr)
            if uni and uni.get("excluded"):
                ex = uni["excluded"]
                uni_err = (
                    (
                        "universal/ refreshed, but %d term(s) from other "
                        "workspaces were excluded "
                        "because they aren't valid IRIs: " % len(ex)
                    )
                    + "; ".join(ex[:8])
                    + (" \u2026" if len(ex) > 8 else "")
                )
        except Exception as ue:
            uni_err = str(ue)
        return JSONResponse(
            {
                "slug": slug,
                "module": mod,
                "universal": uni,
                "universal_error": uni_err,
                "valid": mod.get("valid", True)
                and (uni.get("valid", True) if uni else True),
                "modules": [mod],
            }
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


_CONTAINERISH = ("_workspace", "_consolidate")


def _find_ws_dir(pr, org, slug, folder=None):
    """Find the real folder of a workspace under <org>.

    Prefer an exact folder name (the hint). Otherwise match by slug.
    Never return a container (*_workspace or *_consolidate) or a
    reserved or save folder. Return the match that is nearest to the org
    root.
    """
    base = pr.root / org
    if not base.is_dir():
        return None
    matches = []
    for d in base.rglob("*"):
        if (
            not d.is_dir()
            or d.name in ("save", "refcache")
            or _is_template_name(d.name)
        ):
            continue
        if d.name.endswith(_CONTAINERISH):
            continue
        if (folder and d.name == folder) or slugify(d.name) == slug:
            matches.append(d)
    if not matches:
        return None
    matches.sort(
        key=lambda p: len(p.relative_to(base).parts)
    )  # Nearest to the root first.
    return matches[0]


@app.post("/api/project/move-workspace")
async def project_move_workspace(req: Request):
    """Move a workspace from one org to another org.

    Move its folder (with its save/) to <to_org>/. For a workspace that
    has files only, move its term files. For a workspace that is only a
    save draft, move the draft. Update the consolidated bundle of both
    orgs.
    """
    import shutil

    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    b = await req.json()
    slug = slugify(b.get("workspace") or b.get("folder") or "")
    from_org = (b.get("from_org") or STATE.get("org") or "").strip()
    to_org = (b.get("to_org") or "").strip()
    if not slug or not from_org or not to_org:
        return JSONResponse(
            {"error": "Need workspace, from_org and to_org."}, status_code=400
        )
    if from_org == to_org:
        return JSONResponse({"ok": True, "unchanged": True})
    tgt_parent = pr.root / to_org
    tgt_parent.mkdir(parents=True, exist_ok=True)
    moved = None
    src = _find_ws_dir(pr, from_org, slug, folder=(b.get("folder") or None))
    try:
        if src and src.is_dir():  # A workspace with a folder.
            dest = tgt_parent / src.name
            if dest.exists():
                return JSONResponse(
                    {"error": f"'{src.name}' already exists in {to_org}."},
                    status_code=409,
                )
            shutil.move(str(src), str(dest))
            moved = str(dest)
        # No folder: use the files if there are any. Otherwise it is a
        # draft only.
        else:
            files = [
                f
                for f in (pr.root / from_org).rglob("*")
                if f.is_file()
                and f.suffix.lower() in TERM_EXTS
                and slugify(f.stem) == slug
            ]
            if files:
                for f in files:
                    dest = tgt_parent / f.name
                    if dest.exists():
                        return JSONResponse(
                            {
                                "error": (
                                    f"'{f.name}' already exists "
                                    f"in {to_org}."
                                )
                            },
                            status_code=409,
                        )
                    shutil.move(str(f), str(dest))
                moved = str(tgt_parent)
            else:
                # A draft only: find the save entry anywhere under
                # from_org and move it to <to_org>/save.
                draft, src_save = None, None
                for sd in (
                    [pr.root / from_org / "save"]
                    + list((pr.root / from_org).rglob("save"))
                    + [pr.save_dir]
                ):
                    try:
                        if sd.is_dir():
                            d = pqs.load_working(sd, slug)
                            if d and d.get("terms") is not None:
                                draft, src_save = d, sd
                                break
                    except Exception:  # noqa: BLE001
                        pass
                if not draft:
                    return JSONResponse(
                        {
                            "error": (
                                f"Workspace '{slug}' not found in "
                                f"{from_org} (no folder, file, or "
                                "save draft)."
                            )
                        },
                        status_code=404,
                    )
                (tgt_parent / "save").mkdir(parents=True, exist_ok=True)
                pqs.save_working(
                    tgt_parent / "save",
                    slug,
                    draft["terms"],
                    draft.get("groups", []),
                    draft.get("cited", []),
                )
                try:
                    pqs.delete_workspace(src_save, slug)
                except Exception:  # noqa: BLE001
                    pass
                moved = str(tgt_parent / "save") + " (draft)"
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Move failed: {e}"}, status_code=500)
    logger.debug("move: %s: %s -> %s  (%s)", slug, from_org, to_org, moved)
    # Update the consolidated bundle of both orgs (best effort).
    for o in (from_org, to_org):
        try:
            STATE["org"] = o
            _consolidate(pr)
        except Exception as ce:  # noqa: BLE001
            logger.debug("move: consolidate %s warning: %s", o, ce)
    STATE["org"] = (
        # Stay in the source org. Its remaining workspaces stay visible.
        from_org
    )
    return JSONResponse(
        {
            "ok": True,
            "moved": slug,
            "from_org": from_org,
            "to_org": to_org,
            **_status(),
        }
    )


@app.post("/api/project/rename-workspace")
async def project_rename_workspace(req: Request):
    """Save a workspace rename in every place that uses the slug as key.

    The places are: the Parquet data (history versions and working
    copy), the source ttl input file, the exported module folder
    <project>/<slug>/ and the universal/ folder (rebuilt). The server
    makes a slug of the old name and of the new name.
    """
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    b = await req.json()
    old = slugify(b.get("old", ""))
    new = slugify(b.get("new", ""))
    if not old or not new:
        return JSONResponse(
            {"error": "Need both old and new names."}, status_code=400
        )
    if old == new:
        return JSONResponse({"ok": True, "slug": new, "unchanged": True})
    known = set(pqs.list_workspaces(pr.save_dir)) | set(
        pqs.list_working(pr.save_dir)
    )
    for f in pr.ttl_dir.glob("*"):
        if f.suffix.lower() in TERM_EXTS:
            known.add(slugify(f.stem))
    if old not in known:
        return JSONResponse(
            {"error": f"No workspace '{old}'."}, status_code=404
        )
    if new in known:
        return JSONResponse(
            {"error": f"A workspace named '{new}' already exists."},
            status_code=409,
        )
    result = {"from": old, "to": new}
    # 1) Parquet: history versions and working copy.
    result["parquet"] = pqs.rename_workspace(pr.save_dir, old, new)
    # 2) The source ttl input. Without this step, the old name returns
    # at the next load.
    for f in list(pr.ttl_dir.glob("*")):
        if f.suffix.lower() in TERM_EXTS and slugify(f.stem) == old:
            try:
                f.rename(f.with_name(new + f.suffix))
                result["ttl_renamed"] = True
            except Exception as fe:  # noqa: BLE001
                result["ttl_error"] = str(fe)
    # 3) The exported module folder <project>/<slug>/.
    oldf, newf = pr.root / old, pr.root / new
    try:
        if oldf.is_dir() and pr.root in oldf.parents:
            if newf.exists():
                result["folder_error"] = f"'{new}' folder already exists"
            else:
                oldf.rename(newf)
                result["folder_renamed"] = True
    except Exception as fe:  # noqa: BLE001
        result["folder_error"] = str(fe)
    # 4) Rebuild universal/, so that the paths and terms follow the new
    # slug (best effort).
    try:
        from .assemble import write_universal

        result["universal"] = _consolidate(pr)
    except Exception as ue:  # noqa: BLE001
        result["universal_error"] = str(ue)
    return JSONResponse({"ok": True, "slug": new, **result})


@app.post("/api/project/delete-workspace")
async def project_delete_workspace(req: Request):
    """Delete a workspace for good.

    The function deletes the Parquet versions and the working copy, the
    exported module folder (<project>/<slug>/) and the contribution to
    universal/ (it rebuilds the folder without the workspace). This is
    destructive and cannot be reversed. The client must have asked for
    confirmation two times.
    """
    import shutil

    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    b = await req.json()
    slug = slugify(b.get("name", ""))
    if not slug:
        return JSONResponse(
            {"error": "No workspace name given."}, status_code=400
        )
    known = set(pqs.list_workspaces(pr.save_dir)) | set(
        pqs.list_working(pr.save_dir)
    )
    if slug not in known:
        return JSONResponse(
            {"error": f"No workspace '{slug}' to delete."}, status_code=404
        )
    removed = {
        "parquet": None,
        "folder": False,
        "universal": None,
        "universal_error": None,
    }
    # 1) Parquet: history versions and working copy.
    removed["parquet"] = pqs.delete_workspace(pr.save_dir, slug)
    # 2) The exported module folder <project>/<slug>/.
    folder = pr.root / slug
    try:
        if folder.is_dir() and pr.root in folder.parents:
            shutil.rmtree(folder)
            removed["folder"] = True
    except Exception as fe:  # noqa: BLE001
        removed["folder_error"] = str(fe)
    # 3) Refresh universal/, so that the terms of the deleted workspace
    # go out (best effort).
    try:
        from .assemble import write_universal

        uni = _consolidate(pr)
        removed["universal"] = uni
        if uni and uni.get("excluded"):
            removed["universal_error"] = (
                "universal/ rebuilt; %d term(s) from other workspaces "
                "still excluded as invalid IRIs." % len(uni["excluded"])
            )
    except Exception as ue:  # noqa: BLE001
        removed["universal_error"] = str(ue)
    return JSONResponse({"deleted": slug, **removed, "status": _status()})


def project_file(path: str):
    pr = _current()
    if not pr:
        return JSONResponse({"error": "No project open."}, status_code=400)
    p = (pr.root / path).resolve()
    if pr.root not in p.parents or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)


@app.post("/api/refcache/build")
def refcache_build():
    pr = _current()
    if not pr:
        return {
            "started": False,
            "total": 0,
        }  # No project is open yet. Do nothing. This is not an error.
    sources = _ref_sources(pr)
    refcache.build_async(pr.refcache_dir, sources)
    return {"started": True, "total": len(sources)}


@app.post("/api/refcache/clear")
def refcache_clear():
    pr = _current()
    if pr is None:
        return JSONResponse({"error": "no project open"}, status_code=400)
    from . import ontology_loader

    refcache.clear(pr.refcache_dir)
    ontology_loader.clear_cache()
    return {"ok": True}


@app.post("/api/project/add-references")
async def project_add_references(req: Request):
    """Save uploaded reference files in the ontologies/ folder of the
    project.

    They then load automatically at each open, with no new upload. The
    caller then starts a refcache build to parse and index them.
    """
    pr = _current()
    if pr is None:
        return JSONResponse({"error": "no project open"}, status_code=400)
    b = await req.json()
    odir = pr.ontologies_dir
    odir.mkdir(parents=True, exist_ok=True)
    written, failed = [], []
    for it in b.get("files", []):
        fn = Path(str(it.get("filename", ""))).name  # Remove any path.
        if not fn or Path(fn).suffix.lower() not in TERM_EXTS:
            failed.append(fn or "(unnamed)")
            continue
        try:
            _txt = it.get("text", "")
            if _txt and _txt[:1] == "\ufeff":
                _txt = _txt[1:]
            (odir / fn).write_text(_txt, encoding="utf-8")
            written.append(fn)
        except Exception as e:  # noqa: BLE001
            failed.append(fn + ": " + str(e))
    return {"ok": True, "written": written, "failed": failed}


@app.post("/api/grid-preview")
async def grid_preview(req: Request):
    """Return the raw 2D cell grid of an uploaded CSV, TSV or XLSX file.

    The inspector shows it for hand labels. The function makes no
    guesses about semantics.
    """
    import base64
    from . import grid_reader

    b = await req.json()
    fn = str(b.get("filename", "asset"))
    try:
        if b.get("b64"):
            raw = base64.b64decode(b["b64"])
            grid = grid_reader.read_grid(fn, raw_bytes=raw)
        else:
            grid = grid_reader.read_grid(fn, raw_text=b.get("text", "") or "")
        return JSONResponse(grid)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"error": f"Could not read {fn}: {e}"}, status_code=500
        )


@app.post("/api/analyze-asset")
async def analyze_asset(req: Request):
    """Analyze a data asset and return a normalized ontology draft.

    The draft fills a workspace in advance. The asset can be CSV, a
    HuggingFace yaml and md card, XML or Parquet. The body has
    {filename, text} for text formats or {filename, b64} for binary
    formats (Parquet). The function never guesses semantics. It leaves
    predicates and subclass blank.
    """
    import base64
    from . import asset_analyzer

    b = await req.json()
    fn = str(b.get("filename", "asset"))
    try:
        if b.get("b64"):
            raw = base64.b64decode(b["b64"])
        else:
            txt = b.get("text", "") or ""
            if txt[:1] == "\ufeff":
                txt = txt[1:]
            raw = txt.encode("utf-8")
        draft = asset_analyzer.analyze(fn, raw)
        return JSONResponse(draft)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"error": f"Could not analyze {fn}: {e}"}, status_code=500
        )


@app.get("/api/refcache/progress")
def refcache_progress():
    return refcache.progress()


@app.get("/api/refsearch")
def refsearch(
    prefix: str = "", q: str = "", kind: str = "class", limit: int = 50
):
    pr = _current()
    if not pr:
        return JSONResponse({"results": []})
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    results = refcache.search(pr.refcache_dir, prefix, q, kind, limit)
    return {"prefix": prefix, "q": q, "results": results}


@app.get("/api/glossary")
def glossary(
    prefix: str = "",
    q: str = "",
    kind: str = "all",
    offset: int = 0,
    limit: int = 200,
    letter: str = "",
):
    pr = _current()
    if not pr or not prefix:
        return JSONResponse(
            {"prefix": prefix, "total": 0, "rows": [], "letters": []}
        )
    try:
        limit = max(1, min(int(limit), 50000))
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        limit, offset = 200, 0
    return JSONResponse(
        refcache.glossary(
            pr.refcache_dir, prefix, q, kind, offset, limit, letter
        )
    )


@app.get("/api/ontologies")
def ontologies():
    return JSONResponse(load_ontologies(ONTO_DIR))


@app.post("/api/serialize")
async def serialize_ep(req: Request):
    b = await req.json()
    project = b.get("project", b)
    try:
        return JSONResponse(
            {"turtle": project_to_turtle(project, b.get("prefixes"))}
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)})


@app.post("/api/import-ref")
async def import_ref(req: Request):
    """Parse an uploaded ontology into ONE cleaned reference record.

    The logic is the same as in the mid-level reference loader: only the
    primary and canonical namespaces, a correct title and description,
    and robust TTL and RDF/XML parsing.
    """
    import os
    import tempfile
    from .ontology_loader import _parse_file

    b = await req.json()
    text = b.get("text", "")
    if text and text[:1] == "\ufeff":
        text = text[1:]
    fn = b.get("filename", "import.ttl")
    suf = os.path.splitext(fn)[1] or ".ttl"
    fd, tmp = tempfile.mkstemp(suffix=suf)
    os.close(fd)
    tmp = Path(tmp)
    try:
        tmp.write_text(text, encoding="utf-8")
        recs = _parse_file(tmp, name=fn)
        rec = (
            recs[0]
            if recs
            else {"file": fn, "error": "no declared terms found"}
        )
        rec.setdefault("file", fn)
        rec["source"] = "import"
        rec["cls"] = len(rec.get("classes", []))
        rec["rel"] = len(rec.get("props", []))
        return JSONResponse(rec)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"file": fn, "error": str(e)})
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


@app.post("/api/import")
async def import_ep(req: Request):
    b = await req.json()
    fn = (b.get("filename") or "").lower()
    text = b.get("text", "")
    try:
        if fn.endswith((".yaml", ".yml")):
            from linkml_io import schema_text_to_project

            return JSONResponse(schema_text_to_project(text))
        if fn.endswith((".owl", ".rdf")):
            return JSONResponse(import_project_from_text(text, "xml"))
        return JSONResponse(import_project_from_text(text))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
