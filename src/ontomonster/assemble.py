# SPDX-License-Identifier: BSD-3-Clause
"""Write the export files of a project.

The module writes each unit in five formats. Turtle (.ttl), RDF/XML
(.rdf) and JSON-LD (.jsonld) come from the rdflib graph. The LinkML
schema (.yaml) and the SHACL shapes (.shacl.ttl) come from LinkML in the
same process. The ontology header of each unit has owl:versionInfo and
owl:versionIRI. The module writes only the latest version to disk and
overwrites it at each export. The version history stays in Parquet.

The writers are:
- _write_unit writes one unit, for example one workspace.
- write_consolidate writes the consolidated bundle of one org to
  <org>/<org>_consolidate/.
- write_universal and assemble write the earlier layout. In that layout,
  one folder holds each workspace, and a universal folder holds the
  union of all workspaces.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from rdflib import Graph, URIRef, Literal, RDF, OWL

from . import exporter
from . import parquet_store as pqs
from .project import Project
from .project_io import build_graph, _local
import re as _re

_BAD_IRI = _re.compile(r"[\s<>\"{}|\\^`]")


def _term_serializable(t) -> "str | None":
    """Return None if the term serializes cleanly. Otherwise return a
    short reason for the report.

    A term cannot serialize if its own id or a non-literal value is an
    invalid IRI.
    """
    loc = _local(t.get("name", ""))
    if loc and _BAD_IRI.search(loc):
        return "invalid id"
    for c in t.get("children", []):
        v = (c.get("val", "") or "").strip()
        if v and not v.startswith('"') and _BAD_IRI.search(v):
            return "invalid value \u201c%s\u201d" % v
    return None


_GRAPH_FORMATS = [("ttl", "turtle"), ("rdf", "xml"), ("jsonld", "json-ld")]


def _unit_graph(
    project_dict,
    prefix,
    namespace,
    onto_iri,
    version,
    imports=None,
    prefixes=None,
) -> Graph:
    g0 = build_graph(project_dict, prefixes)
    g = Graph()
    for p, n in g0.namespaces():
        # Remove the empty binding that build_graph makes. Terms then
        # use the prefix.
        if p == "":
            continue
        g.bind(p, n)
    g.bind("owl", str(OWL))
    g.bind(prefix, namespace, replace=True)
    for t in g0:
        g.add(t)
    o = URIRef(onto_iri)
    g.add((o, RDF.type, OWL.Ontology))
    g.add((o, OWL.versionInfo, Literal(version)))
    g.add((o, OWL.versionIRI, URIRef(f"{onto_iri}/{version}")))
    for imp in imports or []:
        g.add((o, OWL.imports, URIRef(imp)))
    return g


def _write_unit(
    root: Path,
    slug: str,
    project_dict: dict,
    prefix: str,
    namespace: str,
    onto_iri: str,
    version: str,
    onto_dir: Path,
    imports=None,
    prefixes=None,
    flat=False,
) -> dict:
    # If flat is true, write the five files directly in root, named
    # <slug>.*, with no subfolder. Otherwise write them in root/<slug>/.
    d = root if flat else (root / slug)
    d.mkdir(parents=True, exist_ok=True)
    files = []

    g = _unit_graph(
        project_dict, prefix, namespace, onto_iri, version, imports, prefixes
    )
    for ext, fmt in _GRAPH_FORMATS:
        name = f"{slug}.{ext}"
        (d / name).write_text(g.serialize(format=fmt), encoding="utf-8")
        files.append(name)

    pm = exporter._prefix_map(project_dict, onto_dir, extra=prefixes)
    schema = exporter.build_schema(project_dict, pm)
    schema["version"] = version
    yaml_path = d / f"{slug}.yaml"
    yaml_path.write_text(
        yaml.safe_dump(schema, sort_keys=False), encoding="utf-8"
    )
    files.append(yaml_path.name)

    ok, err = exporter._gen("shacl", yaml_path, d / f"{slug}.shacl.ttl")
    if ok:
        files.append(f"{slug}.shacl.ttl")
    valid, notes = exporter.validate_schema(yaml_path)
    print(
        f"[export] {slug} v{version}: {len(files)} files, valid={valid}"
        + ("" if ok else f", shacl_error={err[:60]}")
    )
    return {
        "slug": slug,
        "version": version,
        "files": files,
        "valid": valid,
        "validation": notes,
        "shacl_ok": ok,
        "shacl_error": None if ok else err,
        "terms": len(project_dict.get("terms", [])),
    }


def write_consolidate(
    project: "Project",
    org: str,
    terms: list,
    version: str = "0.1",
    prefixes=None,
) -> dict:
    """Write the consolidated bundle of one org.

    This bundle replaces the earlier top-level universal/ folder. The
    function writes the union of the terms of the workspaces of the org
    to <org>/<org>_consolidate/<org>_consolidate.* in five formats. The
    argument terms is the list of terms that is already merged and has
    no duplicates.
    """
    prefix, ns, base = project.prefix, project.namespace, project.base_iri
    onto_dir = project.ontologies_dir
    slug = f"{org}_consolidate"
    excluded, kept = [], []
    for t in terms:
        reason = _term_serializable(t)
        (excluded if reason else kept).append(
            _local(t.get("name", "")) + " (" + reason + ")" if reason else t
        )
    upd = {"prefix": prefix, "iri": ns, "terms": kept}
    res = _write_unit(
        project.root / org,
        slug,
        upd,
        prefix,
        ns,
        f"{base}/{slug}",
        version,
        onto_dir,
        imports=[base],
        prefixes=prefixes,
    )
    if excluded:
        res = dict(res)
        res["excluded"] = excluded
    return res


def write_universal(project: "Project", prefixes=None) -> dict:
    """Rewrite universal/ with the union of the latest version of every
    workspace.

    The collection then shows all workspaces, also the workspaces that
    the user exported most recently.
    """
    save = project.save_dir
    prefix, ns, base = project.prefix, project.namespace, project.base_iri
    onto_dir = project.ontologies_dir
    all_terms, max_vnum, max_ver = [], -1, "0.1"
    slugs = set(pqs.list_workspaces(save)) | set(pqs.list_working(save))
    for slug in sorted(slugs):
        data = pqs.load_working(save, slug) or pqs.load_version(
            save, slug
        )  # Use the current edits.
        if not data or not data.get("terms"):
            continue
        ver = data.get("version") or "0.1"
        if pqs.vnum_of(ver) > max_vnum:
            max_vnum, max_ver = pqs.vnum_of(ver), ver
        all_terms.extend(data["terms"])
    seen, uterms = set(), []
    for t in all_terms:
        if t["name"] in seen:
            continue
        seen.add(t["name"])
        uterms.append(t)
    # The universal folder holds a derived union. A term that cannot
    # serialize is excluded and reported. Usually, such a term has a
    # typed label that remains in a value in another workspace. The
    # collection then refreshes from all valid terms. It does not fail
    # as a whole.
    excluded = []
    kept = []
    for t in uterms:
        reason = _term_serializable(t)
        if reason:
            excluded.append(_local(t.get("name", "")) + " (" + reason + ")")
        else:
            kept.append(t)
    upd = {"prefix": prefix, "iri": ns, "terms": kept}
    res = _write_unit(
        project.root,
        "universal",
        upd,
        prefix,
        ns,
        base,
        max_ver,
        onto_dir,
        imports=None,
        prefixes=prefixes,
    )
    if excluded:
        res = dict(res)
        res["excluded"] = excluded
    return res


def assemble(project: Project) -> dict:
    save = project.save_dir
    prefix, ns, base = project.prefix, project.namespace, project.base_iri
    onto_dir = project.ontologies_dir
    universal_iri = base

    slugs = pqs.list_workspaces(save)
    if not slugs:
        return {"error": "Nothing to export — no saved workspaces yet."}

    modules, all_terms = [], []
    max_vnum, max_ver = -1, "0.1"
    for slug in slugs:
        data = pqs.load_version(save, slug)  # Load the latest version.
        terms = data["terms"]
        ver = data["version"] or "0.1"
        if pqs.vnum_of(ver) > max_vnum:
            max_vnum, max_ver = pqs.vnum_of(ver), ver
        pd = {"prefix": prefix, "iri": ns, "terms": terms}
        modules.append(
            _write_unit(
                project.root,
                slug,
                pd,
                prefix,
                ns,
                f"{base}/{slug}",
                ver,
                onto_dir,
                imports=[universal_iri],
            )
        )
        all_terms.extend(terms)

    seen, uterms = set(), []
    for t in all_terms:
        if t["name"] in seen:
            continue
        seen.add(t["name"])
        uterms.append(t)
    upd = {"prefix": prefix, "iri": ns, "terms": uterms}
    universal = _write_unit(
        project.root,
        "universal",
        upd,
        prefix,
        ns,
        universal_iri,
        max_ver,
        onto_dir,
        imports=None,
    )

    valid_all = universal["valid"] and all(m["valid"] for m in modules)
    print(
        f"[export] done: {len(modules)} module(s) + universal, "
        f"namespace={ns}, valid={valid_all}"
    )
    return {
        "namespace": ns,
        "prefix": prefix,
        "universal_iri": universal_iri,
        "modules": modules,
        "universal": universal,
        "valid": valid_all,
    }
