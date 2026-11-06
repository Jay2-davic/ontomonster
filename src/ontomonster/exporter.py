# SPDX-License-Identifier: BSD-3-Clause
"""Export a project through a validated LinkML schema.

The pipeline has four steps:
1. Convert the project JSON to a LinkML schema (YAML).
2. Validate the schema in the same process with SchemaView of
   linkml_runtime. Validation comes first, before any output.
3. Generate SHACL, OWL (ttl) and JSON Schema with linkml.generators in
   the same process.
4. Also write a direct domain ontology TTL with rdflib. It has
   owl:imports of the references.

LinkML runs in the same process. If the package is not installed, the
module still writes the schema YAML and the domain TTL.
"""

from __future__ import annotations
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
import yaml
from rdflib import Graph, Namespace, RDF, RDFS, OWL, BNode, Literal, URIRef
from rdflib.namespace import SKOS, XSD

# LinkML runs in the same process, with no subprocess. The guard lets
# the module import even if LinkML is not installed. The export endpoint
# then reports the problem clearly.
try:
    from linkml_runtime.utils.schemaview import SchemaView
    from linkml.generators.shaclgen import ShaclGenerator
    from linkml.generators.owlgen import OwlSchemaGenerator
    from linkml.generators.jsonschemagen import JsonSchemaGenerator

    _LINKML = True
    _LINKML_ERR = ""
except Exception as _e:  # noqa: BLE001  pragma: no cover
    _LINKML = False
    _LINKML_ERR = str(_e)

from .std_prefixes import (
    STD_PREFIXES,
    canon_curie,
    dedouble,
    obo_curie,
    obo_binding_map,
)
from .ontology_loader import load_ontologies

REQUIRED = {"rdf:type", "rdfs:label", "skos:definition"}
STD = {**STD_PREFIXES, "linkml": "https://w3id.org/linkml/"}
XSD2LINKML = {
    "xsd:string": "string",
    "xsd:integer": "integer",
    "xsd:decimal": "decimal",
    "xsd:double": "double",
    "xsd:boolean": "boolean",
    "xsd:date": "date",
    "xsd:dateTime": "datetime",
    "xsd:time": "time",
    "xsd:anyURI": "uri",
}


def _local(name: str) -> str:
    return name.split(":", 1)[1] if ":" in name else name


def _pfx(name: str) -> "str | None":
    return name.split(":", 1)[0] if ":" in name else None


def _safe(s: str) -> str:
    return re.sub(r"\W+", "_", s or "").strip("_") or "x"


def _is_lit(v: str) -> bool:
    return (v or "").strip().startswith('"')


def _lit_text(v: str) -> str:
    return (v or "").strip().strip('"')


def _prefix_map(project, onto_dir: Path, extra: dict | None = None) -> dict:
    pm = dict(STD)
    pm[project.get("prefix", "ex")] = project.get(
        "iri", "http://example.org/ontology/"
    )
    for o in load_ontologies(onto_dir):
        if o.get("prefix") and o.get("iri"):
            pm.setdefault(o["prefix"], o["iri"])
    if (
        extra
    ):  # Namespaces from the loaded reference ontologies (ground truth).
        for p, n in extra.items():
            if p and n:
                pm.setdefault(p, n)
    return pm


def build_schema(project: dict, pm: dict) -> dict:
    prefix = project.get("prefix", "ex")
    base = project.get("iri", "http://example.org/ontology/")
    terms = project.get("terms", [])
    own = {_local(t["name"]) for t in terms}
    parent_of: dict = (
        {}
        # Own local name: its rdfs:subClassOf parent curie. This finds
        # common ancestors for ranges.
    )
    for _t in terms:
        for _c in _t.get("children", []):
            if (
                _c.get("pred") == "rdfs:subClassOf"
                and (_c.get("val") or "").strip()
            ):
                parent_of[_local(_t["name"])] = (_c.get("val") or "").strip()
                break

    _DT = set(XSD2LINKML.keys()) | {
        "owl:real",
        "owl:rational",
        "rdfs:Literal",
        "rdf:langString",
        "rdf:XMLLiteral",
        "rdf:HTML",
        "rdf:PlainLiteral",
    }

    def _is_dt(v):
        v = (v or "").strip()
        return v in _DT or v.split(":")[0] == "xsd"

    def _is_prop_term(t):
        for c in t.get("children", []):
            if c.get("pred") == "rdf:type":
                lv = (c.get("val") or "").strip()
                if lv.split(":")[-1] in (
                    "ObjectProperty",
                    "DatatypeProperty",
                    "AnnotationProperty",
                ):
                    return True
        return False

    def _value_type_of(t):
        for c in t.get("children", []):
            if (
                c.get("pred") == "om:valueType"
                and (c.get("val") or "").strip()
            ):
                return (c.get("val") or "").strip()
        if not _is_prop_term(t):
            for c in t.get("children", []):
                if c.get("pred") == "rdfs:range" and _is_dt(
                    c.get("val") or ""
                ):
                    return (c.get("val") or "").strip()
        return None

    # Closed enumerations. A value class with "om:closedEnum true"
    # becomes a LinkML enum. Its member individuals (terms with this
    # type) become permissible_values. The code detects these classes
    # first. The slot range of a field can then point at the enum
    # instead of a class.
    def _is_enum_marker(c):
        return (
            c.get("pred") == "om:closedEnum"
            and (c.get("val") or "").strip().strip('"').lower() == "true"
        )

    enum_classes = {}  # Maps a local name to its term.
    for t in terms:
        if any(_is_enum_marker(c) for c in t.get("children", [])):
            enum_classes[_local(t["name"])] = t
    # Maps an enum local name to the list of member terms.
    enum_members = {k: [] for k in enum_classes}
    member_ids = set()
    if enum_classes:
        for t in terms:
            for c in t.get("children", []):
                if (
                    c.get("pred") == "rdf:type"
                    and _local((c.get("val") or "").strip()) in enum_classes
                ):
                    enum_members[_local((c.get("val") or "").strip())].append(
                        t
                    )
                    member_ids.add(id(t))
                    break

    def _member_pv(m):
        note = lbl = defn = None
        for c in m.get("children", []):
            p, v = c.get("pred"), (c.get("val") or "").strip()
            if not v:
                continue
            if p == "skos:notation":
                note = _lit_text(v)
            elif p == "rdfs:label":
                lbl = _lit_text(v)
            elif p == "skos:definition":
                defn = _lit_text(v)
        key = note or lbl or _local(m["name"])
        pv = {"meaning": "%s:%s" % (prefix, _safe(_local(m["name"])))}
        if defn:
            pv["description"] = defn
        if lbl and lbl != key:
            pv["title"] = lbl
        return key, pv

    enums: dict = {}
    # Literal value sets (om:valueSet). A named list of allowed strings
    # becomes a LinkML enum with bare permissible_values. It has no
    # meaning IRIs, no class and no individuals. Many fields can reuse
    # it.
    value_sets = {}
    for t in terms:
        if any(
            c.get("pred") == "om:valueSet"
            and (c.get("val") or "").strip().strip('"').lower() == "true"
            for c in t.get("children", [])
        ):
            value_sets[_local(t["name"])] = t
    for loc, t in value_sets.items():
        vals, title = [], None
        for c in t.get("children", []):
            p, v = c.get("pred"), (c.get("val") or "").strip()
            if p == "om:enumValue" and v:
                vals.append(_lit_text(v))
            elif p == "rdfs:label":
                title = _lit_text(v)
        edef = {"permissible_values": {v: {} for v in vals}}
        if title:
            edef["title"] = title
        enums[_safe(loc)] = edef

    for loc, t in enum_classes.items():
        title = defn = None
        for c in t.get("children", []):
            if c.get("pred") == "rdfs:label":
                title = _lit_text((c.get("val") or ""))
            elif c.get("pred") == "skos:definition":
                defn = _lit_text((c.get("val") or ""))
        pvs = {}
        for m in enum_members.get(loc, []):
            k, pv = _member_pv(m)
            pvs[k] = pv
        edef = {"permissible_values": pvs}
        if title:
            edef["title"] = title
        if defn:
            edef["description"] = defn
        enums[_safe(loc)] = edef

    classes: dict = {}
    slots: dict = {}
    stubs: dict = {}  # Stubs for external class_uri values.

    def stub_for(curie: str) -> str:
        nm = _safe(_local(curie))
        stubs.setdefault(
            nm, {"class_uri": obo_curie(curie, pm)}
        )  # An external reference keeps the URI. It has no placeholder text.
        return nm

    for t in terms:
        if (
            _local(t["name"]) in enum_classes
            or id(t) in member_ids
            or _local(t["name"]) in value_sets
        ):
            # Enum value classes, enum members and literal value sets
            # are enums. They are not classes.
            continue
        cname = _safe(_local(t["name"]))
        cdef = {
            "class_uri": canon_curie(prefix + ":" + _local(t["name"]), pm),
            "slots": [],
            "slot_usage": {},
        }
        _relrng: dict = (
            {}
            # Maps a slot to a list of ranges. This collects the ranges,
            # so that predicates with many targets do not collapse.
        )
        label = defn = type_val = None
        vtype = _value_type_of(t)
        vt_is_prop = _is_prop_term(t)
        vsref = None
        for c in t.get("children", []):
            if (
                c.get("pred") == "om:valueSetRef"
                and (c.get("val") or "").strip()
            ):
                vsref = _safe(_local((c.get("val") or "").strip()))
        for c in t.get("children", []):
            p, v = c.get("pred"), (c.get("val") or "").strip()
            if not v or p in (
                "om:closedEnum",
                "om:valueType",
                "om:valueSet",
                "om:enumValue",
                "om:valueSetRef",
            ):
                continue
            if (
                p == "rdfs:range"
                and vtype is not None
                and not vt_is_prop
                and _is_dt(v)
            ):
                # This is the value type of the field. The code emits it
                # as the slot value below.
                continue
            if p == "rdfs:label":
                label = _lit_text(v)
            elif p == "skos:definition":
                defn = _lit_text(v)
            elif p == "rdf:type":
                # Example: rdf:type owl:Class. This is meta-typing. It
                # is not is_a and not a slot.
                continue
            elif p == "rdfs:subClassOf":
                type_val = v
            else:
                sname = _safe(c.get("pred"))  # One slot for each property.
                if sname not in slots:
                    slots[sname] = {"slot_uri": obo_curie(c["pred"], pm)}
                tgt = _local(v) if _pfx(v) in (None, prefix) else None
                if _is_lit(v):
                    rng = XSD2LINKML.get(
                        c.get("dtype", "xsd:string"), "string"
                    )
                elif (
                    tgt
                    and tgt in enum_classes
                    # The field points at a closed enum. The range is
                    # that enum.
                ):
                    rng = _safe(tgt)
                else:
                    rng = _safe(tgt) if (tgt and tgt in own) else stub_for(v)
                _relrng.setdefault(sname, []).append((rng, v))
        # Make the relation slots. A slot is multivalued (this matches
        # someValuesFrom: at least 0, no maximum). Keep a single
        # sh:class only when the predicate has ONE target on this class.
        # A predicate can have many different targets on one class, for
        # example 4 times designated_by, 2 times page and 2 times
        # has-part. Then the range stays open. This prevents a wrong
        # single sh:class and a wrong maxCount of 1.
        for sname, items in _relrng.items():
            cdef["slots"].append(sname)
            uniq = list(dict.fromkeys(r for (r, _v) in items))
            su: dict = {"multivalued": True}
            if len(uniq) == 1:
                # A single target gives its own sh:class.
                su["range"] = uniq[0]
            else:
                # A predicate with many targets on one object property
                # stays object-valued. It never gets a string default.
                # The range is the common parent of the targets. If the
                # targets have no single common parent, the range is
                # Information Content Entity.
                own_tgts = [
                    _local(v) for (_r, v) in items if _pfx(v) in (None, prefix)
                ]
                parents = {
                    parent_of.get(t) for t in own_tgts if parent_of.get(t)
                }
                if len(parents) == 1:
                    cp = parents.pop()
                    su["range"] = (
                        _safe(_local(cp))
                        if _local(cp) in own
                        else stub_for(cp)
                    )
                else:
                    su["range"] = stub_for(
                        "cco:ont00000958"
                    )  # ICE is a safe object super-type.
            cdef["slot_usage"][sname] = su
        if (
            vsref is not None and vsref in enums
        ):  # The value of the field is limited to a reusable value set.
            slots.setdefault("value", {"slot_uri": prefix + ":value"})
            cdef["slots"].append("value")
            cdef["slot_usage"]["value"] = {"range": vsref}
        elif (
            vtype is not None
        ):  # The field holds a literal value of this datatype.
            slots.setdefault("value", {"slot_uri": prefix + ":value"})
            cdef["slots"].append("value")
            cdef["slot_usage"]["value"] = {
                "range": XSD2LINKML.get(vtype, "string")
            }
        if label:
            cdef["title"] = label
        if defn:
            cdef["description"] = defn
        if type_val:
            cdef["is_a"] = (
                _safe(_local(type_val))
                if _local(type_val) in own and _pfx(type_val) in (None, prefix)
                else stub_for(type_val)
            )
        if not cdef["slots"]:
            cdef.pop("slots")
        if not cdef["slot_usage"]:
            cdef.pop("slot_usage")
        classes[cname] = cdef

    classes.update(stubs)
    used = {p for p in (list(pm))}
    schema = {
        "id": base.rstrip("/#"),
        "name": _safe(prefix + "_ontology"),
        "title": project.get("title") or (prefix + " ontology"),
        "description": project.get("description") or (prefix + " ontology."),
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "prefixes": obo_binding_map(pm),
        "default_prefix": prefix,
        "default_range": "string",
        "imports": ["linkml:types"],
        "classes": classes,
    }
    if slots:
        schema["slots"] = slots
    if enums:
        schema["enums"] = enums
    return schema


_GENERATORS = {}
if _LINKML:
    _GENERATORS = {
        "shacl": ShaclGenerator,
        "owl": OwlSchemaGenerator,
        "jsonschema": JsonSchemaGenerator,
    }


def _gen(kind: str, schema_path: Path, out_path: Path):
    """Run a LinkML generator in the same process (linkml.generators)
    and write the output.
    """
    if not _LINKML:
        return (
            False,
            f"LinkML not installed ({_LINKML_ERR or 'pip install linkml'})",
        )
    G = _GENERATORS.get(kind)
    if G is None:
        return False, f"no generator '{kind}'"
    try:
        text = G(schema_path.as_posix()).serialize()
        out_path.write_text(text, encoding="utf-8")
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:400]


def validate_schema(schema_path: Path):
    """Validate first, in the same process, with SchemaView of the
    LinkML runtime.
    """
    notes = []
    ok = True
    if not _LINKML:
        return True, [
            f"LinkML not installed ({_LINKML_ERR or 'pip install linkml'}) - "
            f"validation skipped"
        ]
    try:
        sv = SchemaView(schema_path.as_posix())
        notes.append(
            f"LinkML schema valid: {len(sv.all_classes())} classes, "
            f"{len(sv.all_slots())} slots"
        )
    except Exception as e:  # noqa: BLE001
        ok = False
        notes.append(f"LinkML schema error: {e}")
    return ok, notes


def domain_ttl(project: dict, pm: dict, out_path: Path):
    """Write the direct OWL/TTL of the terms (TBox).

    It has owl:imports of the references.
    """
    g = Graph()
    for p, n in obo_binding_map(pm).items():
        g.bind(p, Namespace(n))
    prefix = project.get("prefix", "ex")
    base = project.get("iri", "http://example.org/ontology/")
    EX = Namespace(base)

    def resolve(curie):
        if _is_lit(curie):
            return None
        p = _pfx(curie)
        if p is None or p == prefix:
            return EX[_local(curie)]
        return URIRef(dedouble(pm.get(p, "urn:x:" + p + ":"), _local(curie)))

    onto = URIRef(base.rstrip("/#"))
    g.add((onto, RDF.type, OWL.Ontology))
    for o in (
        load_ontologies(Path(project.get("_onto_dir", ".")))
        if project.get("_onto_dir")
        else []
    ):
        if o.get("iri"):
            g.add((onto, OWL.imports, URIRef(o["iri"].rstrip("/#"))))

    for t in project.get("terms", []):
        s = EX[_local(t["name"])]
        g.add((s, RDF.type, OWL.Class))
        for c in t.get("children", []):
            p, v = c.get("pred"), (c.get("val") or "").strip()
            if not v:
                continue
            if p == "rdfs:label":
                g.add((s, RDFS.label, Literal(_lit_text(v))))
            elif p == "skos:definition":
                g.add((s, SKOS.definition, Literal(_lit_text(v))))
            elif p == "rdf:type":
                tgt = resolve(v)
                if tgt:
                    g.add((s, RDFS.subClassOf, tgt))
            else:
                prop = resolve(p)
                r = BNode()
                g.add((s, RDFS.subClassOf, r))
                g.add((r, RDF.type, OWL.Restriction))
                g.add((r, OWL.onProperty, prop))
                if _is_lit(v):
                    dt = c.get("dtype", "xsd:string")
                    dturi = (
                        URIRef(STD["xsd"] + _local(dt))
                        if dt.startswith("xsd:")
                        else None
                    )
                    g.add(
                        (
                            r,
                            OWL.hasValue,
                            Literal(_lit_text(v), datatype=dturi),
                        )
                    )
                else:
                    tgt = resolve(v)
                    g.add((r, OWL.someValuesFrom, tgt))
    g.serialize(out_path.as_posix(), format="turtle")
    return out_path


def export_project(project: dict, onto_dir: Path, export_dir: Path) -> dict:
    export_dir.mkdir(parents=True, exist_ok=True)
    project = dict(project)
    project["_onto_dir"] = str(onto_dir)
    pm = _prefix_map(project, onto_dir)

    files, warnings = [], []

    # 1. Schema YAML.
    schema = build_schema(project, pm)
    schema_path = export_dir / "schema.yaml"
    schema_path.write_text(
        yaml.safe_dump(schema, sort_keys=False), encoding="utf-8"
    )
    files.append("schema.yaml")

    # 2. Validate first.
    valid, notes = validate_schema(schema_path)

    # 3. Generators. Skip them if the validation failed hard.
    if valid:
        for kind, outp in [
            ("shacl", export_dir / "shapes.shacl.ttl"),
            ("owl", export_dir / "ontology.owl.ttl"),
            ("jsonschema", export_dir / "schema.json"),
        ]:
            ok, err = _gen(kind, schema_path, outp)
            if ok:
                files.append(outp.name)
            else:
                warnings.append(f"gen-{kind}: {err}")
    else:
        warnings.append("schema invalid - generators skipped")

    # 4. Direct domain TTL. Always write it.
    try:
        dt = domain_ttl(project, pm, export_dir / "domain.ttl")
        files.append(dt.name)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"domain.ttl: {e}")

    return {
        "valid": valid,
        "validation": notes,
        "files": files,
        "warnings": warnings,
    }


if __name__ == "__main__":
    proj = json.loads(Path(sys.argv[1]).read_text())
    print(
        json.dumps(
            export_project(proj, Path("ontologies"), Path("exports")), indent=2
        )
    )


# ---------------------------------------------------------------------------
# Versioned export for each workspace: yaml, shacl and ttl, validated by
# LinkML. The code writes a new version only if the content changed
# since the last version. The code always overwrites current/. A
# repeated save is then idempotent.
# ---------------------------------------------------------------------------


def _content_hash(project: dict) -> str:
    """Hash the meaningful content (names and rows).

    Ignore the row ids and the groups. A repeated save of identical
    content then does not create a new version.
    """
    canon = []
    for t in project.get("terms", []):
        rows = sorted(
            (
                (c.get("pred") or ""),
                (c.get("val") or ""),
                (c.get("dtype") or ""),
            )
            for c in t.get("children", [])
        )
        canon.append((_local(t["name"]), rows))
    canon.sort(key=lambda x: x[0])
    blob = json.dumps(canon, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_artifacts(
    project: dict,
    schema: dict,
    pm: dict,
    outdir: Path,
    base: str,
    onto_dir: Path,
) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    files, warnings = [], []
    sp = outdir / (base + ".yaml")
    sp.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    files.append(sp.name)

    valid, notes = validate_schema(sp)

    if valid:
        ok, err = _gen("shacl", sp, outdir / (base + ".shacl.ttl"))
        if ok:
            files.append(base + ".shacl.ttl")
        else:
            warnings.append(f"gen-shacl: {err}")
        ok, err = _gen("owl", sp, outdir / (base + ".owl.ttl"))
        if ok:
            files.append(base + ".owl.ttl")
        else:
            warnings.append(f"gen-owl: {err}")
    else:
        warnings.append("schema invalid - SHACL/OWL generation skipped")

    try:
        domain_ttl(
            dict(project, _onto_dir=str(onto_dir)),
            pm,
            outdir / (base + ".ttl"),
        )
        files.append(base + ".ttl")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"domain ttl: {e}")

    return {
        "files": files,
        "valid": valid,
        "validation": notes,
        "warnings": warnings,
    }


def export_workspace(
    project: dict, name: str, exports_root: Path, onto_dir: Path
) -> dict:
    """Export one workspace to exports/<name>/current.

    Make a new version if the content changed.
    """
    exports_root = Path(exports_root)
    ws = exports_root / _safe(name)
    cur = ws / "current"
    vers = ws / "versions"
    cur.mkdir(parents=True, exist_ok=True)
    vers.mkdir(parents=True, exist_ok=True)
    manifest_path = ws / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"name": name, "versions": []}
    )

    h = _content_hash(project)
    last = manifest["versions"][-1]["hash"] if manifest["versions"] else None
    changed = h != last

    project = dict(project)
    project["_onto_dir"] = str(onto_dir)
    pm = _prefix_map(project, onto_dir)
    schema = build_schema(project, pm)
    base = _safe(name)

    art = _write_artifacts(project, schema, pm, cur, base, onto_dir)

    result = {
        "workspace": name,
        "changed": changed,
        "valid": art["valid"],
        "validation": art["validation"],
        "warnings": art["warnings"],
        "current_files": [f"{_safe(name)}/current/{f}" for f in art["files"]],
        "total_versions": len(manifest["versions"]),
    }

    if changed:
        v = len(manifest["versions"]) + 1
        vdir = vers / f"v{v}"
        vdir.mkdir(parents=True, exist_ok=True)
        for fn in art["files"]:
            shutil.copy(cur / fn, vdir / fn)
        manifest["versions"].append(
            {
                "v": v,
                "hash": h,
                "time": datetime.datetime.now().isoformat(timespec="seconds"),
                "valid": art["valid"],
                "terms": len(project.get("terms", [])),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        result["version"] = v
        result["total_versions"] = v
        result["versioned_files"] = [
            f"{_safe(name)}/versions/v{v}/{f}" for f in art["files"]
        ]
    else:
        result["version"] = len(manifest["versions"]) or None
        result["note"] = (
            "no changes since last saved version - not re-versioned"
        )

    return result
