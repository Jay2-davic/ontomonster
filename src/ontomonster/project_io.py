# SPDX-License-Identifier: BSD-3-Clause
"""Read an ontology file into the term-centric model, and write it back.

The module parses an ontology file (TTL, OWL or RDF) into the
term-centric project model of the editor. It also serializes a project
back to Turtle. It mirrors the importer in the client
(buildProjectFromTurtle in static/index.html):

- Each owl:Class, owl:NamedIndividual or typed instance in YOUR
  namespace becomes an editable term. Subjects of public ontologies
  become references.
- The module keeps all rdf:type assertions as they are.
- Attribute triples become editable rows of relationship and value.
- A literal with the text "xsd:<type>" is datatype-only. Its value is
  empty.
- An IRI with no known prefix stays as <iri>.
- The module never invents rdfs:label. An owl:Ontology header is not a
  term.
"""

from __future__ import annotations
import re
from .std_prefixes import STD_PREFIXES, obo_binding_map
from collections import Counter
from pathlib import Path as _Path
from rdflib import Graph, RDF, RDFS, OWL, URIRef, BNode, Literal
from rdflib.namespace import SKOS
import logging as _logging

# rdflib logs a noisy warning for each literal whose lexical form does
# not match its datatype. These are data issues that the app shows in
# Scan. Do not fill the server console with them.
_logging.getLogger("rdflib.term").setLevel(_logging.ERROR)

IAO = URIRef("http://purl.obolibrary.org/obo/IAO_0000115")
XSD = "http://www.w3.org/2001/XMLSchema#"

META = {
    OWL.Class,
    OWL.NamedIndividual,
    OWL.Thing,
    OWL.Ontology,
    OWL.ObjectProperty,
    OWL.DatatypeProperty,
    OWL.AnnotationProperty,
    RDFS.Class,
    RDF.Property,
}
SKIP_PREDS = {RDF.type, RDFS.label, SKOS.definition, IAO, RDFS.comment}

# Namespaces of public ontologies. These are not "your" namespace.
EXTERNAL = re.compile(
    r"(commoncoreontologies\.org|obolibrary\.org|purl\.org|w3\.org|w3id\.org|"
    r"qudt\.org|ebi\.ac\.uk|edamontology\.org|industrialontologies\.org|"
    r"xmlns\.com|sparontologies\.net|schema\.org|dublincore\.org)",
    re.I,
)

_STD = {"owl", "rdf", "rdfs", "skos", "xsd", "dcterms", "dc"}


def _local(u: str) -> str:
    i = max(u.rfind("#"), u.rfind("/"))
    if i >= 0:  # A full IRI has # or /. Take the fragment or the last segment.
        start = i + 1
        return u[start:]
    j = u.find(":")  # A curie such as ex:Foo or cco:ont... Remove the prefix.
    start = j + 1
    return u[start:] if j >= 0 else u


def _ns(u: str) -> str:
    i = max(u.rfind("#"), u.rfind("/"))
    return u[: i + 1] if i >= 0 else ""


def import_project_from_text(text: str, hint: str | None = None) -> dict:
    if (
        text and text[:1] == "\ufeff"
    ):  # Remove the UTF-8 BOM. It breaks the Turtle data parser of rdflib.
        text = text[1:]
    # Normalize damaged CR and CRLF newlines.
    text = re.sub(r"\r\n?", "\n", text)
    g = Graph()
    fmt = "xml" if (hint == "xml" or text.lstrip()[:1] == "<") else "turtle"
    g.parse(data=text, format=fmt)

    ns2pfx = {str(n): p for p, n in g.namespaces() if p}

    def is_entity(s) -> bool:
        types = set(g.objects(s, RDF.type))
        if OWL.Ontology in types:
            return False
        if (
            OWL.Class in types
            or RDFS.Class in types
            or OWL.NamedIndividual in types
        ):
            return True
        return any(t not in META for t in types)

    ents = [
        s
        for s in set(g.subjects())
        if isinstance(s, URIRef) and _local(str(s)) and is_entity(s)
    ]
    if not ents:
        return {
            "error": (
                "No owl:Class or owl:NamedIndividual entities "
                "found in the file."
            )
        }

    # YOUR namespace is the most frequent namespace that is not a known
    # public ontology.
    cnt = Counter(_ns(str(s)) for s in ents)
    non_ext = [ns for ns in cnt if not EXTERNAL.search(ns)]
    own_ns = max((non_ext or list(cnt)), key=lambda ns: cnt[ns])
    prefix = ns2pfx.get(own_ns) or _local(own_ns.rstrip("#/")) or "ex"
    prefix = "".join(ch for ch in prefix if ch.isalnum()) or "ex"

    own_set = {str(s) for s in ents if _ns(str(s)) == own_ns}

    def ref(u) -> str:
        u = str(u)
        if u in own_set:
            return _local(u)  # Your own term: use the bare local name.
        # OBO normalization. Reset ".../obo/<TOK>_<id>" and each doubled
        # ".../obo/<TOK>_<TOK>_<id>" artefact to the canonical form of
        # the app, "<tok>:<TOK>_<id>".
        m = re.match(
            r"^http://purl\.obolibrary\.org/obo/([A-Za-z]+)_(?:\1_)?(\d+)$", u
        )
        if m:
            tok = m.group(1)
            return f"{tok.lower()}:{tok}_{m.group(2)}"
        ns, lo = _ns(u), _local(u)
        p = ns2pfx.get(ns)
        if p and lo:
            return f"{p}:{lo}"  # A clean curie.
        return f"<{u}>"  # A full IRI. Keep it as it is.

    def lit_row(pred, lit: Literal):
        s = str(lit)
        m = re.fullmatch(r"xsd:([A-Za-z][A-Za-z0-9]*)", s.strip())
        if lit.datatype is None and m:  # The datatype-only convention.
            return {
                "pred": pred,
                "val": "",
                "dtype": "xsd:" + m.group(1),
                "dtAuto": False,
            }
        if lit.datatype is not None:
            dt = (
                "xsd:" + _local(str(lit.datatype))
                if str(lit.datatype).startswith(XSD)
                else "xsd:string"
            )
        else:
            dt = "xsd:string"
        return {
            "pred": pred,
            "val": '"' + s + '"',
            "dtype": dt,
            "dtAuto": False,
        }

    terms, references = [], {}
    for s in ents:
        if str(s) not in own_set:
            # A subject of a public ontology is a reference vocabulary.
            # It is not a term.
            ns = _ns(str(s))
            p = (
                ns2pfx.get(ns)
                or "".join(
                    c for c in _local(ns.rstrip("#/")) if c.isalnum()
                ).lower()
                or "ns"
            )
            lab = g.value(s, RDFS.label)
            grp = references.setdefault(
                p, {"prefix": p, "iri": ns, "classes": [], "props": []}
            )
            rec = {
                "c": _local(str(s)),
                "l": str(lab) if lab else _local(str(s)),
            }
            d = (
                g.value(s, SKOS.definition)
                or g.value(s, IAO)
                or g.value(s, RDFS.comment)
            )
            if d:
                rec["d"] = str(d)
            grp["classes"].append(rec)
            continue

        children = []
        for t in g.objects(s, RDF.type):  # Keep every type as it is.
            if isinstance(t, URIRef):
                children.append({"pred": "rdf:type", "val": ref(t)})
        lab = g.value(s, RDFS.label)
        dfn = (
            g.value(s, SKOS.definition)
            or g.value(s, IAO)
            or g.value(s, RDFS.comment)
        )
        children.append(
            {
                "pred": "rdfs:label",
                "val": ('"' + str(lab) + '"') if lab is not None else "",
            }
        )
        children.append(
            {
                "pred": "skos:definition",
                "val": ('"' + str(dfn) + '"') if dfn is not None else "",
            }
        )
        for p, o in g.predicate_objects(s):
            if p in SKIP_PREDS:
                continue
            pred = ref(p)
            if isinstance(o, URIRef):
                children.append({"pred": pred, "val": ref(o)})
            elif isinstance(o, Literal):
                children.append(lit_row(pred, o))
            elif isinstance(o, BNode) and (o, OWL.onProperty, None) in g:
                # An owl:Restriction blank node becomes a row of
                # relationship and value.
                prop = g.value(o, OWL.onProperty)
                if prop is None:
                    continue
                rp = ref(prop)
                sv = g.value(o, OWL.someValuesFrom) or g.value(
                    o, OWL.allValuesFrom
                )
                hv = g.value(o, OWL.hasValue)
                if isinstance(sv, URIRef):
                    children.append({"pred": rp, "val": ref(sv)})
                elif isinstance(hv, URIRef):
                    children.append({"pred": rp, "val": ref(hv)})
                elif isinstance(hv, Literal):
                    children.append(lit_row(rp, hv))
        terms.append({"name": _local(str(s)), "children": children})

    out = {"prefix": prefix, "iri": own_ns, "terms": terms}
    if references:
        out["references"] = list(references.values())
    return out


# ---------------------------------------------------------------------------
# Serialize a project back to Turtle (CEMENTO and rdflib style).
# ---------------------------------------------------------------------------
_KNOWN = dict(STD_PREFIXES)


def build_graph(project: dict, prefixes: dict | None = None) -> Graph:
    """Build an rdflib Graph from the term-centric project model.

    The argument prefixes can add more prefix-to-IRI mappings, for
    example mappings that the import found. Then the referenced curies
    resolve to their real namespaces.
    """
    iri = project.get("iri") or "http://example.org/ontology/"
    pmap = dict(_KNOWN)
    if prefixes:
        pmap.update({k: v for k, v in prefixes.items() if k and v})
    pmap[""] = iri

    g = Graph()
    for p, n in obo_binding_map(pmap).items():
        g.bind(p, URIRef(n), replace=True)

    def term_uri(local: str) -> URIRef:
        return URIRef(iri + local)

    def resolve(token: str) -> URIRef:
        if token.startswith("<") and token.endswith(">"):
            return URIRef(token[1:-1])
        if token == "rdf:type":
            return RDF.type
        if ":" in token:
            p, lo = token.split(":", 1)
            base = pmap.get(p)
            if base:
                # OBO doubling guard. An OBO prefix binds to
                # ".../obo/<TOK>_". The app stores the full local name
                # "<TOK>_<id>", for example bfo: and BFO_0000176. A
                # simple join gives ".../obo/BFO_BFO_0000176". This IRI
                # does not exist. If the local name already has the
                # token at the end of the base, remove the repeated
                # token.
                if base.endswith("_"):
                    # The token, for example "BFO_".
                    tok = base.rstrip("/").rsplit("/", 1)[-1]
                    if tok and lo.startswith(tok):
                        return URIRef(
                            base[: -len(tok)] + lo
                        )  # The join of ".../obo/" and "BFO_0000176".
                return URIRef(base + lo)
            return term_uri(lo) if p == "" else URIRef(iri + lo)
        return term_uri(token)  # Own term or bare local name.

    _NUM = {
        "integer",
        "int",
        "long",
        "short",
        "byte",
        "nonNegativeInteger",
        "positiveInteger",
        "negativeInteger",
        "nonPositiveInteger",
        "unsignedInt",
        "unsignedLong",
        "unsignedShort",
        "unsignedByte",
    }
    _DEC = {"decimal", "float", "double"}
    _typemismatch = []

    def _lex_ok(text: str, dt: str) -> bool:
        t = (text or "").strip()
        try:
            if dt in _NUM:
                int(t)
                return True
            if dt in _DEC:
                float(t)
                return True
            if dt == "boolean":
                return t.lower() in ("true", "false", "0", "1")
        except (ValueError, TypeError):
            return False
        # For dates, strings and unknown types, let rdflib handle the
        # value. Do not block.
        return True

    def obj_node(val: str, dtype: str):
        v = (val or "").strip()
        if v == "":
            if dtype and dtype != "xsd:string":  # A datatype-only placeholder.
                return Literal(dtype)
            return None
        if v.startswith('"'):
            text = v[1:-1] if v.endswith('"') else v[1:]
            if dtype and dtype != "xsd:string":
                dt = dtype.split(":", 1)[1] if ":" in dtype else dtype
                if _lex_ok(text, dt):
                    return Literal(text, datatype=URIRef(pmap["xsd"] + dt))
                # If the value does not match its datatype, keep a valid
                # string literal and record the mismatch.
                _typemismatch.append((text, dtype))
                return Literal(text)
            return Literal(text)
        return resolve(v)

    _bad = re.compile(r"[\s<>\"{}|\\^`]")
    _problems = []
    for t in project.get("terms", []):
        loc = _local(t.get("name", ""))
        if loc and _bad.search(loc):
            _problems.append("term name \u201c%s\u201d" % loc)
        for c in t.get("children", []):
            v = (c.get("val", "") or "").strip()
            if v and not v.startswith('"') and _bad.search(v):
                _problems.append(
                    "value \u201c%s\u201d (in %s)" % (v, loc or "?")
                )
    if _problems:
        raise ValueError(
            "Cannot export: these identifiers contain spaces or reserved "
            "characters and are not valid IRIs \u2014 "
            + "; ".join(_problems[:8])
            + (" \u2026" if len(_problems) > 8 else "")
            + ". Rename the term or fix the value (a typed label usually "
            "belongs in rdfs:label, not the identifier)."
        )

    def _is_dt_val(v):
        v = (v or "").strip()
        if ":" not in v:
            return False
        p = v.split(":", 1)[0]
        return p == "xsd" or v in (
            "owl:real",
            "owl:rational",
            "rdfs:Literal",
            "rdf:langString",
            "rdf:XMLLiteral",
            "rdf:HTML",
            "rdf:PlainLiteral",
        )

    def _is_prop_term(t):
        for c in t.get("children", []):
            if c.get("pred") == "rdf:type" and (
                c.get("val") or ""
            ).strip().split(":")[-1] in (
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
                if c.get("pred") == "rdfs:range" and _is_dt_val(
                    c.get("val") or ""
                ):
                    return (c.get("val") or "").strip()
        return None

    def _is_value_set(t):
        return any(
            c.get("pred") == "om:valueSet"
            and (c.get("val") or "").strip().strip('"').lower() == "true"
            for c in t.get("children", [])
        )

    # Predicates that stay as direct triples: class axioms, annotations
    # and property definitions. Each other predicate that points at a
    # term is an object-property relation. The code wraps it as an
    # owl:someValuesFrom restriction below. The exported ontology is
    # then DL-valid. It is not a bare class-to-class assertion.
    _DIRECT_PREDS = frozenset(
        [
            "rdf:type",
            "rdfs:subClassOf",
            "rdfs:subPropertyOf",
            "rdfs:domain",
            "rdfs:range",
            "rdfs:label",
            "rdfs:comment",
            "rdfs:isDefinedBy",
            "rdfs:seeAlso",
            "skos:definition",
            "skos:prefLabel",
            "skos:altLabel",
            "skos:note",
            "skos:example",
            "skos:scopeNote",
            "owl:equivalentClass",
            "owl:disjointWith",
            "owl:inverseOf",
            "owl:sameAs",
        ]
    )

    for t in project.get("terms", []):
        if _is_value_set(t):
            continue  # Literal value sets have no OWL classes or individuals.
        subj = term_uri(_local(t["name"]))
        _vt = _value_type_of(t)
        _prop = _is_prop_term(t)
        for c in t.get("children", []):
            pred = c.get("pred")
            if not pred or pred in (
                "om:closedEnum",
                "om:valueType",
                "om:valueSet",
                "om:enumValue",
                "om:valueSetRef",
            ):
                continue  # Internal markers never become a stray triple.
            if (
                pred == "rdfs:range"
                and _vt is not None
                and not _prop
                and _is_dt_val(c.get("val") or "")
            ):
                # The value type of the field. The code emits it as a
                # restriction below.
                continue
            o = obj_node(c.get("val", ""), c.get("dtype", "xsd:string"))
            if o is None:
                continue
            if (
                not _prop
                and isinstance(o, URIRef)
                and pred not in _DIRECT_PREDS
            ):
                # An object-property relation between classes becomes a
                # DL-valid restriction: subj rdfs:subClassOf [
                # owl:onProperty pred ; owl:someValuesFrom o ].
                r = BNode()
                g.add((subj, RDFS.subClassOf, r))
                g.add((r, RDF.type, OWL.Restriction))
                g.add((r, OWL.onProperty, resolve(pred)))
                g.add((r, OWL.someValuesFrom, o))
            else:
                g.add((subj, resolve(pred), o))

    # The value type of each field becomes an OWL restriction: field
    # subClassOf (value some xsd:type).
    _VALUE_PROP = URIRef(iri + "value")
    _any_vt = False
    for t in project.get("terms", []):
        vt = _value_type_of(t)
        if not vt:
            continue
        _any_vt = True
        r = BNode()
        g.add((term_uri(_local(t["name"])), RDFS.subClassOf, r))
        g.add((r, RDF.type, OWL.Restriction))
        g.add((r, OWL.onProperty, _VALUE_PROP))
        g.add((r, OWL.someValuesFrom, resolve(vt)))
    if _any_vt:
        g.add((_VALUE_PROP, RDF.type, OWL.DatatypeProperty))
        g.add((_VALUE_PROP, RDFS.label, Literal("value")))

    # Closed enumerations become owl:oneOf ( members ). The raw TTL is
    # then also closed.
    from rdflib.collection import Collection

    _enum_locals = {
        _local(t["name"])
        for t in project.get("terms", [])
        if any(
            c.get("pred") == "om:closedEnum"
            and (c.get("val") or "").strip().strip('"').lower() == "true"
            for c in t.get("children", [])
        )
    }
    if _enum_locals:
        _members = {k: [] for k in _enum_locals}
        for t in project.get("terms", []):
            for c in t.get("children", []):
                if (
                    c.get("pred") == "rdf:type"
                    and _local((c.get("val") or "").strip()) in _enum_locals
                ):
                    _members[_local((c.get("val") or "").strip())].append(
                        term_uri(_local(t["name"]))
                    )
                    break
        for loc, mem in _members.items():
            if mem:
                lst = BNode()
                Collection(g, lst, mem)
                g.add((term_uri(loc), OWL.oneOf, lst))
    try:
        g._om_typemismatch = (
            _typemismatch  # noqa: SLF001 (surfaced by callers if useful)
        )
    except Exception:
        pass

    return g


def project_to_turtle(project: dict, prefixes: dict | None = None) -> str:
    """Serialize the project to Turtle.

    This is a thin wrapper over build_graph.
    """
    return build_graph(project, prefixes).serialize(format="turtle")


if __name__ == "__main__":
    import json
    import sys

    print(
        json.dumps(
            import_project_from_text(open(sys.argv[1]).read()), indent=2
        )[:2000]
    )


# ---------------------------------------------------------------------------
# Loading of a project folder (universal/, ontologies/ and ttl/).
# ---------------------------------------------------------------------------
PROJECT_SUBDIRS = {"universal": "universal", "ontologies": "mid-level"}
_TERM_EXTS = {".ttl", ".owl", ".rdf", ".n3", ".nt"}


def load_project(location) -> dict:
    """Load a project folder into workspaces and references that have a
    role tag.

    The folders are:
    - ttl/: one workspace for each file. The user can edit it.
    - ontologies/: references with the role "mid-level".
    - universal/: references with the role "universal".

    The function prints the progress to stdout. The uvicorn console
    shows it.
    """
    from .ontology_loader import load_ontologies

    loc = _Path(location).expanduser()
    print(f"[load_project] folder: {loc}")
    if not loc.is_dir():
        print("[load_project] ERROR: not a directory")
        return {"error": f"Project folder not found: {loc}"}

    references = []
    for sub, role in PROJECT_SUBDIRS.items():
        d = loc / sub
        if d.is_dir():
            loaded = load_ontologies(d)
            for o in loaded:
                o["role"] = role
                references.append(o)
            print(
                f"[load_project] {sub}/ -> {len(loaded)} reference file(s) "
                f"[{role}]"
            )
        else:
            print(f"[load_project] {sub}/ not present")

    workspaces, errors = [], []
    tdir = loc / "ttl"
    if tdir.is_dir():
        files = [
            f for f in sorted(tdir.glob("*")) if f.suffix.lower() in _TERM_EXTS
        ]
        print(f"[load_project] ttl/ -> {len(files)} candidate file(s)")
        for f in files:
            try:
                pj = import_project_from_text(
                    f.read_text(encoding="utf-8", errors="replace")
                )
            except Exception as e:  # noqa: BLE001
                errors.append(f"{f.name}: {e}")
                print(f"[load_project]   ws THREW: {f.name}: {e}")
                continue
            if pj.get("error"):
                errors.append(f"{f.name}: {pj['error']}")
                print(f"[load_project]   ws ERROR: {f.name}: {pj['error']}")
                continue
            references.extend(pj.get("references", []))
            workspaces.append(
                {
                    "name": f.name,
                    "prefix": pj.get("prefix"),
                    "iri": pj.get("iri"),
                    "terms": pj.get("terms", []),
                    "file": f.name,
                }
            )
            print(
                f"[load_project]   ws OK: {f.name} "
                f"({len(pj.get('terms', []))} terms, "
                f"prefix={pj.get('prefix')})"
            )
    else:
        print("[load_project] ttl/ not present")

    # Remove duplicate references by IRI. The entries of a subfolder
    # with a role tag come first and win.
    seen_ref, refs = set(), []
    for o in references:
        key = o.get("iri")
        if key and key in seen_ref:
            continue
        if key:
            seen_ref.add(key)
        refs.append(o)

    # For backward compatibility, the top-level project is the largest
    # workspace.
    if workspaces:
        primary = max(workspaces, key=lambda w: len(w.get("terms", [])))
        prefix, iri, terms = (
            primary["prefix"],
            primary["iri"],
            primary["terms"],
        )
    else:
        prefix, iri, terms = "ex", "http://example.org/ontology/", []

    print(
        f"[load_project] DONE: {len(workspaces)} workspace(s), "
        f"{len(refs)} reference(s), {len(errors)} error(s)"
    )
    out = {
        "prefix": prefix,
        "iri": iri,
        "terms": terms,
        "workspaces": workspaces,
        "references": refs,
        "location": str(loc),
    }
    if errors:
        out["warnings"] = errors
    return out
