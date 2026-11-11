# SPDX-License-Identifier: BSD-3-Clause
"""Cache the reference ontologies in a Parquet catalog.

The catalog has one row for each ontology file. The key of a row is the
content hash of the file (sha256 of the raw bytes). The catalog file is
refcache/catalog.parquet in the project root. A row records the
location, the assigned prefix, title and description, the term counts, a
version for the file and a status.

- The content hash detects changes. The code parses a file again only
  when its bytes change. A copy or a touch does not start work.
- A change to the bytes increases the version of that file (1, 2, 3 and
  so on). Each file has its own version.
- Two files with the same hash are duplicates. The later file gets the
  status "duplicate". The code does not load it twice.

The cache stores the match rows (kind, code, label, definition, prefix,
alt, iri) in Parquet term sidecar files. The name of a sidecar is
terms_<hash>.parquet. Files with identical content share one sidecar. A
live PROGRESS dictionary drives the progress bar of the UI.

A large reference has more than BIG_TERMS terms, for example ChEBI. The
code returns only the metadata of a large reference. The function search
matches its terms.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .ontology_loader import _parse_file

logger = logging.getLogger("onto_monster")

# Above this number of terms, return only metadata. The server searches
# the terms.
BIG_TERMS = 12000
# Number of parallel workers for parsing.
MAX_WORKERS = min(max(1, os.cpu_count() - 1), 6) if os.cpu_count() else 4
CATALOG = "catalog.parquet"
# The schema of the sidecar columns. Change it to force a new parse.
SCHEMA = "v20"

_LOCK = threading.Lock()
PROGRESS = {"phase": "idle", "file": "", "done": 0, "total": 0, "note": ""}
# Maps a sidecar slug to (mtime, {"class": [...], "prop": [...]}).
_TABLES: dict = {}
_THREAD = None
_RESULT: dict = {"records": None}

_CAT_STR = [
    "path",
    "file",
    "sha256",
    "prefix",
    "title",
    "desc",
    "iri",
    "namespaces",
    "status",
    "error",
    "dup_of",
    "updated_at",
]
_CAT_INT = ["n_classes", "n_props", "version", "mtime", "size"]


def _set(**kw):
    with _LOCK:
        PROGRESS.update(kw)


def progress() -> dict:
    with _LOCK:
        return dict(PROGRESS)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _slug(sha: str) -> str:
    """Return the file name stem of a term sidecar. It depends on the
    content hash and on the column schema.
    """
    return (
        "terms_"
        + hashlib.sha1((SCHEMA + "|" + sha).encode("utf-8")).hexdigest()[:16]
    )


# Catalog (Parquet).
def _load_catalog(refdir: Path) -> dict:
    f = refdir / CATALOG
    if not f.exists():
        return {}
    try:
        d = pq.read_table(f.as_posix()).to_pydict()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for i in range(len(d.get("path", []))):
        row = {c: (d[c][i] if c in d else None) for c in (_CAT_STR + _CAT_INT)}
        out[row["path"]] = row
    return out


def _save_catalog(refdir: Path, cat: dict) -> None:
    rows = list(cat.values())
    cols = {}
    for c in _CAT_STR:
        cols[c] = [str(r.get(c) or "") for r in rows]
    for c in _CAT_INT:
        cols[c] = [int(r.get(c) or 0) for r in rows]
    pq.write_table(pa.table(cols), (refdir / CATALOG).as_posix())


def clear(refdir: Path) -> None:
    """Delete the on-disk cache of a project and reset the state in
    memory. The next build then parses every file again and shows new
    errors.
    """
    global _TABLES, _RESULT
    try:
        for p in Path(refdir).glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    _TABLES = {}
    _RESULT = {"records": None}
    _set(phase="idle", file="", done=0, total=0, note="")


# Term sidecars.
def _rows(rec: dict):
    kinds, codes, labels, defs, pfxs, alts, iris = [], [], [], [], [], [], []
    doms, rngs, sups, typs = [], [], [], []
    default_pfx = rec.get("prefix", "")
    nsmap = rec.get("namespaces", {}) or {}
    for kind, key in (("class", "classes"), ("prop", "props")):
        for t in rec.get(key, []):
            pfx = t.get("pfx", default_pfx)
            kinds.append(kind)
            codes.append(t.get("c", ""))
            labels.append(t.get("l", "") or t.get("c", ""))
            defs.append(t.get("d", "") or "")
            pfxs.append(pfx)
            a = t.get("a") or []
            if isinstance(a, str):
                a = [a]
            opts = []
            for x in a:  # Split each "|" (OR) into separate options.
                for piece in str(x).split("|"):
                    piece = piece.strip()
                    if piece and piece not in opts:
                        opts.append(piece)
            code = t.get("c", "")
            # The local identifier carries its namespace, for example
            # BFO_0000196. It still matches the bare number "0000196".
            if code and code not in opts:
                opts.append(code)
            alts.append(" | ".join(opts))
            iris.append(
                t.get("u", "") or (nsmap.get(pfx, "") + t.get("c", ""))
            )
            doms.append(" ".join(t.get("dom", [])) if kind == "prop" else "")
            rngs.append(" ".join(t.get("rng", [])) if kind == "prop" else "")
            sups.append(" ".join(t.get("sup", [])) if kind == "class" else "")
            typs.append(" ".join(t.get("typ", [])) if kind == "class" else "")
    return kinds, codes, labels, defs, pfxs, alts, iris, doms, rngs, sups, typs


def _write_sidecar(refdir: Path, slug: str, rec: dict):
    kinds, codes, labels, defs, pfxs, alts, iris, doms, rngs, sups, typs = (
        _rows(rec)
    )
    table = pa.table(
        {
            "kind": kinds,
            "code": codes,
            "label": labels,
            "definition": defs,
            "pfx": pfxs,
            "alt": alts,
            "iri": iris,
            "dom": doms,
            "rng": rngs,
            "sup": sups,
            "typ": typs,
        }
    )
    pq.write_table(table, (refdir / (slug + ".parquet")).as_posix())


def _read_terms(refdir: Path, slug: str) -> dict:
    side = refdir / (slug + ".parquet")
    mt = int(side.stat().st_mtime)
    cached = _TABLES.get(slug)
    if cached and cached[0] == mt:
        return cached[1]
    d = pq.read_table(side.as_posix()).to_pydict()
    has_alt, has_iri = "alt" in d, "iri" in d
    has_dom, has_rng, has_sup, has_typ = (
        "dom" in d,
        "rng" in d,
        "sup" in d,
        "typ" in d,
    )
    by = {"class": [], "prop": []}
    for i in range(len(d["code"])):
        rec = {"c": d["code"][i], "l": d["label"][i], "pfx": d["pfx"][i]}
        if d["definition"][i]:
            rec["d"] = d["definition"][i]
        if has_alt and d["alt"][i]:
            rec["a"] = d["alt"][i]
        if has_iri and d["iri"][i]:
            rec["u"] = d["iri"][i]
        if has_dom and d["dom"][i]:
            rec["dom"] = d["dom"][i].split()
        if has_rng and d["rng"][i]:
            rec["rng"] = d["rng"][i].split()
        if has_sup and d["sup"][i]:
            rec["sup"] = d["sup"][i].split()
        if has_typ and d["typ"][i]:
            rec["typ"] = d["typ"][i].split()
        by[d["kind"][i]].append(rec)
    _TABLES[slug] = (mt, by)
    return by


def _parse_file_wrapper(path_str):
    """Parse a file and return its record. Worker for parallel parsing."""
    try:
        p = Path(path_str)
        recs = _parse_file(p)
        rec = (
            recs[0]
            if recs
            else {"file": p.name, "error": "no declared terms found"}
        )
        return path_str, rec, None
    except Exception as e:  # noqa: BLE001
        return path_str, {"file": Path(path_str).name}, str(e)


# Build.
def build(refdir: Path, sources: list, parse: bool = True) -> list:
    """Make the catalog current for sources. Return one record for each
    file.

    The argument sources is a list of {path, source, role}. The code
    detects changes by content hash. A changed file increases its
    version. The code flags duplicate content. If parse is False, the
    function only reads the catalog. It skips new and changed files,
    because the asynchronous builder parses them and shows the progress.
    """
    t_start = time.time()
    refdir.mkdir(parents=True, exist_ok=True)
    cat = _load_catalog(refdir)
    total = len(sources)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if parse:
        _set(
            phase="load", file="", done=0, total=total, note="reading catalog"
        )

    # 1) Hash each file. Decide if the file is new, unchanged or
    # changed. Detect duplicates.
    t_hash_start = time.time()
    plan, seen_hash = [], {}
    for i, s in enumerate(sources):
        p = Path(s["path"])
        try:
            st = p.stat()
        except OSError:
            continue
        size, mtime = int(st.st_size), int(st.st_mtime)
        prev = cat.get(str(p))
        # Fast path: if the size and the modification time match the
        # catalog, trust the stored hash. Do not read the file. A hash
        # of a 164 MB ontology at each open is the slow part.
        if (
            prev
            and int(prev.get("size") or 0) == size
            and int(prev.get("mtime") or 0) == mtime
            and prev.get("sha256")
        ):
            sha = prev["sha256"]
        else:
            try:
                sha = _sha256(p)
            except OSError:
                continue
        dup_of = seen_hash.get(
            sha, ""
        )  # Another file in this scan has identical bytes.
        seen_hash.setdefault(sha, str(p))
        if prev and prev.get("sha256") == sha:
            state = "unchanged"
            version = int(prev.get("version") or 1)
        elif prev:
            state = "changed"
            version = (
                int(prev.get("version") or 1) + 1
            )  # The bytes changed. Make a new version.
        else:
            state = "new"
            version = 1
        slug = _slug(sha)
        have_side = (refdir / (slug + ".parquet")).exists()
        plan.append(
            (s, p, sha, slug, state, version, dup_of, have_side, size, mtime)
        )
        if parse:
            _set(
                phase="check", file=p.name, done=i + 1, total=total, note=state
            )

    t_hash_end = time.time()
    n_unchanged = sum(1 for x in plan if x[4] == "unchanged" and x[7])
    logger.debug(
        "Hash phase: %.3fs, %d files, %d unchanged (fast path)",
        t_hash_end - t_hash_start, len(plan), n_unchanged
    )

    # 2) Check if catalog is current. If every file passes fast path and
    # has a sidecar, skip the build entirely and return phase "ready".
    todo = [
        x for x in plan if not x[6] and (x[4] != "unchanged" or not x[7])
    ]
    if parse and not todo:
        _set(phase="ready", file="", done=total, total=total, note="")
        logger.debug("Catalog current, skipping build")
        # Skip to records phase
        t_parse_start = time.time()
        t_write_start = t_parse_start
        t_write_end = t_parse_start
        parsed_n = 0
    else:
        # 3) Update the catalog. Parse the new and changed files, and the
        # files with no sidecar. Use parallel parsing for speed.
        t_parse_start = time.time()
        parsed_n = 0
        if parse and todo:
            n_todo = len(todo)
            # Sort todo by size descending (start largest first)
            todo_with_size = [(x, x[8]) for x in todo]
            todo_with_size.sort(key=lambda t: t[1], reverse=True)
            todo_sorted = [x[0] for x in todo_with_size]

            # Prepare parse jobs (only non-duplicates needing parse)
            parse_jobs = []
            for x in todo_sorted:
                s, p, sha, slug, state, version, dup_of, have_side, size, mtime = x
                if not dup_of and (state != "unchanged" or not have_side):
                    parse_jobs.append((str(p), p, sha, slug, s, version, size, mtime))

            # Parse in parallel
            parsed_results = {}
            if parse_jobs:
                paths_to_parse = [job[0] for job in parse_jobs]
                with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    for i, (path_str, rec, err) in enumerate(
                        executor.map(_parse_file_wrapper, paths_to_parse)
                    ):
                        parsed_n += 1
                        p = Path(path_str)
                        _set(
                            phase="parse",
                            file=p.name,
                            done=parsed_n,
                            total=len(parse_jobs),
                            note="parsing — one-time",
                        )
                        if err:
                            rec["error"] = err
                        parsed_results[path_str] = rec

            # Process all entries (duplicates, unchanged, and parsed)
            for (
                s,
                p,
                sha,
                slug,
                state,
                version,
                dup_of,
                have_side,
                size,
                mtime,
            ) in plan:
                if dup_of:  # The bytes are identical to an earlier file.
                    canon = cat.get(dup_of) or {}
                    cat[str(p)] = {
                        "path": str(p),
                        "file": p.name,
                        "sha256": sha,
                        "prefix": canon.get("prefix", ""),
                        "title": canon.get("title", ""),
                        "desc": canon.get("desc", ""),
                        "iri": canon.get("iri", ""),
                        "namespaces": canon.get("namespaces", "{}"),
                        "n_classes": 0,
                        "n_props": 0,
                        "version": version,
                        "status": "duplicate",
                        "error": "",
                        "dup_of": dup_of,
                        "updated_at": now,
                        "mtime": mtime,
                        "size": size,
                    }
                    continue
                if state == "unchanged" and have_side and str(p) in cat:
                    # Keep the parsed data. Only update the version.
                    cat[str(p)]["version"] = version
                    cat[str(p)]["status"] = (
                        "error" if cat[str(p)].get("error") else "ok"
                    )
                    cat[str(p)]["mtime"] = mtime
                    cat[str(p)]["size"] = size
                    continue
                # Get parsed result
                rec = parsed_results.get(str(p))
                if not rec:
                    continue
                rec.setdefault("file", p.name)
                rec.setdefault("prefix", "")
                rec.setdefault("iri", "")
                rec.setdefault("classes", [])
                rec.setdefault("props", [])
                rec.setdefault("namespaces", {})
                if not rec.get("error"):
                    _write_sidecar(refdir, slug, rec)
                    _TABLES.pop(slug, None)
                cat[str(p)] = {
                    "path": str(p),
                    "file": rec["file"],
                    "sha256": sha,
                    "prefix": rec["prefix"],
                    "title": rec.get("title") or "",
                    "desc": rec.get("desc") or "",
                    "iri": rec["iri"],
                    "namespaces": json.dumps(rec["namespaces"]),
                    "n_classes": len(rec["classes"]),
                    "n_props": len(rec["props"]),
                    "version": version,
                    "status": "error" if rec.get("error") else "ok",
                    "error": rec.get("error") or "",
                    "dup_of": "",
                    "updated_at": now,
                    "mtime": mtime,
                    "size": size,
                }
            # Remove the catalog rows of files that no longer exist.
            present = {str(Path(s["path"])) for s in sources}
            for gone in [k for k in cat if k not in present]:
                cat.pop(gone, None)
            t_write_start = time.time()
            _save_catalog(refdir, cat)
            t_write_end = time.time()
            _set(phase="ready", file="", done=total, total=total, note="")
        else:
            t_write_start = t_parse_start
            t_write_end = t_parse_start

        logger.debug(
            "Parse phase: %.3fs, %d files parsed",
            t_write_start - t_parse_start, parsed_n
        )
        logger.debug("Catalog write: %.3fs", t_write_end - t_write_start)

    # 3) Make the records, one for each file.
    t_records_start = time.time()
    out = []
    for (
        s,
        p,
        sha,
        slug,
        state,
        version,
        dup_of,
        have_side,
        size,
        mtime,
    ) in plan:
        row = cat.get(str(p))
        if row is None:
            continue
        if not parse and state != "unchanged":
            continue  # Consult-only: skip the files that wait for a parse.
        n = int(row.get("n_classes") or 0) + int(row.get("n_props") or 0)
        try:
            ns = json.loads(row.get("namespaces") or "{}")
        except Exception:  # noqa: BLE001
            ns = {}
        rec = {
            "prefix": row.get("prefix", ""),
            "iri": row.get("iri", ""),
            "file": row.get("file", p.name),
            "title": row.get("title") or None,
            "desc": row.get("desc") or None,
            "namespaces": ns,
            "source": s.get("source"),
            "role": s.get("role"),
            "cls": int(row.get("n_classes") or 0),
            "rel": int(row.get("n_props") or 0),
            "slug": slug,
            "sha": sha[:12],
            "version": version,
            "status": row.get("status", "ok"),
        }
        if dup_of:
            rec["error"] = (
                "duplicate of " + Path(dup_of).name + " (identical content)"
            )
            rec["classes"], rec["props"] = [], []
        elif row.get("error"):
            rec["error"] = row["error"]
            rec["classes"], rec["props"] = [], []
        elif n > BIG_TERMS:
            rec["searchable"] = True
            rec["classes"], rec["props"] = [], []
        else:
            try:
                by = _read_terms(refdir, slug)
                rec["classes"], rec["props"] = by["class"], by["prop"]
            except Exception:  # noqa: BLE001
                rec["classes"], rec["props"] = [], []
        out.append(rec)

    t_end = time.time()
    logger.debug("Records phase: %.3fs", t_end - t_records_start)
    logger.debug("Total build time: %.3fs", t_end - t_start)
    return out


def build_async(refdir: Path, sources: list):
    """Run build on a background thread.

    The UI can then poll the progress.
    """
    global _THREAD
    _RESULT["records"] = None
    _set(phase="load", file="", done=0, total=len(sources), note="starting")

    def run():
        try:
            _RESULT["records"] = build(refdir, sources)
        except Exception as e:  # noqa: BLE001
            _set(phase="error", note=str(e))

    _THREAD = threading.Thread(target=run, daemon=True)
    _THREAD.start()
    return _THREAD


def result():
    return _RESULT["records"]


# Search and glossary. Go through the catalog and read the sidecars by
# hash.
def _sidecars_for(refdir: Path, prefix: str):
    """Yield (slug, namespaces) for each catalog row that declares the
    prefix.
    """
    for row in _load_catalog(refdir).values():
        if row.get("status") == "duplicate" or row.get("error"):
            continue
        try:
            ns = json.loads(row.get("namespaces") or "{}")
        except Exception:  # noqa: BLE001
            ns = {}
        if prefix in ns or row.get("prefix") == prefix:
            yield _slug(row.get("sha256", "")), ns


def search(
    refdir: Path, prefix: str, q: str, kind: str = "class", limit: int = 50
) -> list:
    ql = (q or "").strip().lower()
    want = "prop" if kind in ("prop", "rel") else "class"
    res, seen = [], set()
    for slug, ns in _sidecars_for(refdir, prefix):
        try:
            by = _read_terms(refdir, slug)
        except Exception:  # noqa: BLE001
            continue
        for t in by[want]:
            if t["pfx"] != prefix:
                continue
            if (
                ql
                and ql not in t["l"].lower()
                and ql not in t["c"].lower()
                and not any(
                    ql in o.lower() for o in (t.get("a", "") or "").split("|")
                )
            ):
                continue
            # Deduplicate by URI, not prefix:code, to avoid showing the
            # same class or property multiple times with different
            # namespace prefixes.
            iri = t.get("u", "")
            if iri and iri in seen:
                continue
            if iri:
                seen.add(iri)
            res.append(
                {
                    "code": t["c"],
                    "label": t.get("l") or t["c"],
                    "pfx": t["pfx"],
                    "def": t.get("d", ""),
                    "alt": t.get("a", ""),
                    "iri": iri,
                }
            )
            if len(res) >= limit * 6:
                break
    if ql:
        res.sort(
            key=lambda r: (
                (
                    0
                    if (
                        r["label"].lower().startswith(ql)
                        or r["code"].lower().startswith(ql)
                    )
                    else 1
                ),
                len(r["label"]),
            )
        )
    return res[:limit]


def glossary(
    refdir: Path,
    prefix: str,
    q: str = "",
    kind: str = "all",
    offset: int = 0,
    limit: int = 200,
    letter: str = "",
) -> dict:
    ql = (q or "").strip().lower()
    if kind in ("prop", "rel"):
        wants = ("prop",)
    elif kind in ("class", "classes"):
        wants = ("class",)
    else:
        wants = ("class", "prop")
    rows, seen = [], set()
    for slug, ns in _sidecars_for(refdir, prefix):
        nsfull = ns.get(prefix, "")
        try:
            by = _read_terms(refdir, slug)
        except Exception:  # noqa: BLE001
            continue
        for w in wants:
            for t in by[w]:
                if t["pfx"] != prefix:
                    continue
                # Deduplicate by URI, not prefix:code
                iri = t.get("u", "")
                if iri and iri in seen:
                    continue
                if iri:
                    seen.add(iri)
                lab = t.get("l") or t["c"]
                alt = t.get("a", "") or ""
                df = t.get("d", "") or ""
                if (
                    ql
                    and ql not in lab.lower()
                    and ql not in t["c"].lower()
                    and ql not in alt.lower()
                    and ql not in df.lower()
                ):
                    continue
                rows.append(
                    {
                        "code": t["c"],
                        "label": lab,
                        "alt": alt,
                        "def": df,
                        "iri": t.get("u", "") or (nsfull + t["c"]),
                        "kind": w,
                    }
                )
    rows.sort(key=lambda r: (r["label"] or r["code"]).lower())

    def _bucket(r):
        ch = (r["label"] or r["code"] or "").strip()[:1].upper()
        return ch if ("A" <= ch <= "Z") else "#"

    letters = sorted({_bucket(r) for r in rows}, key=lambda x: (x == "#", x))
    if letter:
        lf = letter.strip().upper()
        rows = [r for r in rows if _bucket(r) == lf]
    stop = offset + limit
    return {
        "prefix": prefix,
        "total": len(rows),
        "offset": offset,
        "letters": letters,
        "rows": rows[offset:stop],
    }
