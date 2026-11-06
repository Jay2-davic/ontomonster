# SPDX-License-Identifier: BSD-3-Clause
"""Scan an ontology folder and return reference vocabularies for the UI.

The design suits real bundles such as OBO, IOF, QUDT and CCO.

- There is one record for each file. Users think in files. Also, several
  OBO ontologies (BFO, CHMO, SWO and others) share the obo: namespace. A
  grouping by namespace only would collapse them into one bucket and
  hide files. Each record has a display prefix. This is the namespace in
  which the file defines the most terms. Each defined term has its own
  prefix. The UI can then index the terms by their true namespace and
  make correct curies.
- A record has only the terms that a file declares (rdf:type Class,
  Property or NamedIndividual) in a namespace that it defines. This
  removes the many foreign terms that the file only references, for
  example foaf, owl and rdf imports and mentions of superclasses.
- The code skips the standard base vocabularies (owl, rdf, rdfs, xsd)
  and the terms with owl:deprecated.
- The code caches the results of each file by modification time. The
  files are large (QUDT and CHMO are about 6 MB each), and the code
  scans the folder again at each status call.

The record shape is:

    {prefix, iri, file, title, desc,
     classes: [{c, l, d, pfx}], props: [{c, l, d, pfx}],
     namespaces: {pfx: ns}}

A file that failed gives {file, error}.
"""

import re
from pathlib import Path
from rdflib import Graph, RDF, RDFS, OWL, URIRef
from rdflib.namespace import SKOS
from rdflib.collection import Collection

IAO_DEF = URIRef("http://purl.obolibrary.org/obo/IAO_0000115")
DEPRECATED = URIRef("http://www.w3.org/2002/07/owl#deprecated")
_OIO = "http://www.geneontology.org/formats/oboInOwl#"
_DC = "http://purl.org/dc/elements/1.1/"
VOAF_VOCAB = URIRef("http://purl.org/vocommons/voaf#Vocabulary")
SYN_PREDS = [
    SKOS.altLabel,
    URIRef(_OIO + "hasExactSynonym"),
    URIRef(_OIO + "hasRelatedSynonym"),
    URIRef(_OIO + "hasBroadSynonym"),
    URIRef(_OIO + "hasNarrowSynonym"),
    URIRef(_OIO + "hasSynonym"),
]
DCT = "http://purl.org/dc/terms/"
EXTS = {".ttl", ".owl", ".rdf", ".n3", ".nt", ".jsonld", ".trig"}
BASE_PREFIXES = {
    "owl",
    "rdf",
    "rdfs",
    "xsd",
}  # Standard vocabularies. The code treats them as built-in.


_CACHE: dict = {}  # Maps a path to (mtime, [records]).


def clear_cache() -> None:
    """Clear the parse cache of each file in this process. A restart of
    the app also clears it.
    """
    _CACHE.clear()


def _cls_iris(node, g) -> list:
    """Resolve a domain, range or superclass node to named class IRIs.

    Flatten owl:unionOf. Ignore restrictions and intersections. Treat
    them as unconstrained.
    """
    out = []
    if isinstance(node, URIRef):
        out.append(str(node))
    else:
        u = g.value(node, OWL.unionOf)
        if u is not None:
            try:
                for m in Collection(g, u):
                    if isinstance(m, URIRef):
                        out.append(str(m))
            except Exception:
                pass
    return out


def _local(uri: str) -> str:
    i = max(uri.rfind("#"), uri.rfind("/"))
    start = i + 1
    return uri[start:] if i >= 0 else uri


def _ns(uri: str) -> str:
    i = max(uri.rfind("#"), uri.rfind("/"))
    return uri[: i + 1] if i >= 0 else uri


_EXT_TLD = {
    "owl",
    "rdf",
    "ttl",
    "xml",
    "nt",
    "n3",
    "jsonld",
    "trig",
    "org",
    "com",
    "net",
    "io",
    "gov",
    "edu",
    "co",
    "uk",
    "html",
}


def _file_prefix(name: str) -> str:
    """Return the prefix from the file name.

    Use the first part of the stem, before the first underscore, and
    clean it. For example: cco.ttl gives cco, chebi_lite.owl gives chebi
    and ma-ont.rdf gives ma-ont.
    """
    stem = Path(name).stem
    first = stem.split("_", 1)[0]
    return "".join(c for c in first if c.isalnum() or c == "-").lower()


def _derive_prefix(ns: str) -> str:
    tail = ns.rstrip("#/").replace("#", "/").split("/")[-1]
    if tail.lower().startswith(
        "www."
        # Example: www.commoncoreontologies.org becomes
        # commoncoreontologies.org.
    ):
        tail = tail[4:]
    if (
        "."
        in tail
        # Examples: foo.owl becomes foo. commoncoreontologies.org
        # becomes commoncoreontologies.
    ):
        head, _, ext = tail.rpartition(".")
        if head and ext.lower() in _EXT_TLD:
            tail = head
    return "".join(c for c in tail if c.isalnum()).lower() or "ns"


def _bad_ns(ns: str) -> bool:
    """Return True for a namespace that is not a real vocabulary. Such a
    namespace must never become a prefix.
    """
    if not ns:
        return True
    low = ns.lower()
    if (
        low.startswith("urn:")
        or low.startswith("_:")
        or low.startswith("nodeid")
    ):
        return True  # SWRL variables (urn:swrl:var) and blank-node artefacts.
    if "swrl" in low or "/.well-known/" in low:
        return True
    if not (low.startswith("http://") or low.startswith("https://")):
        # Accept only http and https vocabularies. Other schemes are not
        # real.
        return True
    return False


# General RDF loading that depends on the file type. This is the rdflib
# parser name to try first for each file extension. The Turtle family,
# the RDF/XML family, the line-based formats and JSON-LD each have their
# own first parser. The other parsers follow as fallbacks. A file with a
# wrong extension then still loads.
_TURTLE_FB = ["turtle", "n3", "xml", "nt", "json-ld", "trig"]
_XML_FB = ["xml", "turtle", "n3", "nt", "json-ld", "trig"]
_FORMAT_ORDER = {
    ".ttl": _TURTLE_FB,
    ".n3": ["n3", "turtle", "xml", "nt", "json-ld", "trig"],
    ".nt": ["nt", "turtle", "n3", "xml", "json-ld", "trig"],
    ".owl": _XML_FB,
    ".rdf": _XML_FB,
    ".xml": _XML_FB,
    ".rdfs": _XML_FB,
    ".jsonld": ["json-ld", "xml", "turtle", "n3", "nt", "trig"],
    ".json": ["json-ld", "xml", "turtle", "n3", "nt", "trig"],
}
_DEFAULT_ORDER = _TURTLE_FB


def _decode_text(raw: bytes) -> "str | None":
    """Convert bytes to clean text.

    Choose an encoding, remove a BOM and normalize the newlines. The
    newline step is the important one. Some downloaded or edited
    ontologies have damaged line endings, for example after repeated
    CRLF conversions. The Turtle and N3 parsers of rdflib reject a bare
    CR between statements with the error "expected directive or
    statement". The RDF/XML parser ignores it. For this reason only
    Turtle files fail. The function changes each run of CR or CRLF to
    one LF. Then every text-based RDF syntax parses correctly.
    """
    text = None
    # The encoding utf-8-sig also removes a BOM.
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:  # noqa: BLE001
            continue
    if text is None:
        return None
    # Remove a BOM again. This is a second safety step.
    if text[:1] == "\ufeff":
        text = text[1:]
    # Change CR, CRLF and runs of CR to LF.
    return re.sub(r"\r\n?", "\n", text)


def _format_order(path: Path) -> list:
    """Return the list of rdflib parser names to try, in order. The file
    extension decides the list.
    """
    return _FORMAT_ORDER.get(path.suffix.lower(), _DEFAULT_ORDER)


def _load_graph(f: Path, nm: str) -> Graph:
    """Parse a supported ontology file into an rdflib Graph.

    The function works on the decoded content, not on the OS path. A
    path breaks on Windows when a drive letter such as C: is read as a
    URL scheme. The function first fixes the encoding, the BOM and the
    newlines. It then tries the correct parser for the file type first.
    """
    raw = Path(f).read_bytes()
    text = _decode_text(raw)
    first_err = None
    for fmt in _format_order(f):
        try:
            g = Graph()
            if text is not None:
                g.parse(data=text, format=fmt)
            else:  # The bytes cannot be decoded. This is the last resort.
                g.parse(f.as_posix(), format=fmt)
            return g
        except Exception as e:  # noqa: BLE001
            if first_err is None:
                first_err = e
            continue
    raise RuntimeError(
        "could not parse %s (starts with %r): %s"
        % (nm, raw[:48], str(first_err)[:120])
    )


def _parse_file(f: Path, name: str = None) -> list:
    nm = name or f.name
    g = _load_graph(f, nm)  # Decode, normalize and parse by file type.
    ns2pfx = {str(n): p for p, n in g.namespaces() if p}

    onto = next((s for s in g.subjects(RDF.type, OWL.Ontology)), None) or next(
        (s for s in g.subjects(RDF.type, VOAF_VOCAB)), None
    )
    title = desc = None
    if onto is not None:
        t = (
            g.value(onto, RDFS.label)
            or g.value(onto, URIRef(DCT + "title"))
            or g.value(onto, URIRef(_DC + "title"))
            or g.value(onto, SKOS.prefLabel)
        )
        d = (
            g.value(onto, URIRef(DCT + "description"))
            or g.value(onto, URIRef(DCT + "abstract"))
            or g.value(onto, URIRef(_DC + "description"))
            or g.value(onto, RDFS.comment)
            or g.value(onto, SKOS.definition)
        )
        title = str(t) if t else None
        desc = str(d) if d else None

    typed_classes = {
        s
        for s in (
            set(g.subjects(RDF.type, OWL.Class))
            | set(g.subjects(RDF.type, RDFS.Class))
        )
        if isinstance(s, URIRef)
    }
    typed_inds = {
        s
        for s in g.subjects(RDF.type, OWL.NamedIndividual)
        if isinstance(s, URIRef)
    }
    typed_props = {
        s
        for s in (
            set(g.subjects(RDF.type, OWL.ObjectProperty))
            | set(g.subjects(RDF.type, OWL.DatatypeProperty))
            | set(g.subjects(RDF.type, RDF.Property))
        )
        if isinstance(s, URIRef)
    }

    # Domain individuals are subjects with a type that is not a
    # structural class, for example unit:Meter a qudt:Unit. They are
    # instance vocabularies, such as QUDT units, SKOS concept schemes
    # and code lists. The user matches against them. For this reason the
    # code shows them together with the classes.
    _STRUCT = (
        "http://www.w3.org/2002/07/owl#",
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "http://www.w3.org/2000/01/rdf-schema#",
        "http://www.w3.org/2001/XMLSchema#",
    )
    # Tie instances to classes that this file declares. Without this
    # rule, the code would include other subjects that are not
    # vocabulary terms. Examples are SWRL variables, author and ORCID
    # IRIs, individuals of licence and version metadata, and subjects of
    # a foreign type.
    dom_inds = set()
    for s, _, ob in g.triples((None, RDF.type, None)):
        if isinstance(s, URIRef) and ob in typed_classes:
            dom_inds.add(s)
    dom_inds -= typed_classes | typed_props
    typed_inds |= dom_inds

    own_ns = {_ns(str(s)) for s in (typed_classes | typed_inds | typed_props)}

    # Fallback for files that a person made by hand and that declare
    # nothing with rdf:type.
    generous = not (typed_classes or typed_inds or typed_props)
    if generous:
        for s in set(g.subjects(RDFS.subClassOf, None)):
            if isinstance(s, URIRef):
                typed_classes.add(s)
                own_ns.add(_ns(str(s)))

    onto_ns = _ns(str(onto)) if onto is not None else None
    terms = {"classes": {}, "props": {}}  # Maps pfx to {code: record}.
    nsmap = {}

    def deprecated(uri) -> bool:
        v = g.value(uri, DEPRECATED)
        return v is not None and str(v).lower() == "true"

    def add(uri, kind):
        u = str(uri)
        ns = _ns(u)
        if ns not in own_ns or deprecated(uri) or _bad_ns(ns):
            return
        pfx = ns2pfx.get(ns) or _derive_prefix(ns)
        if pfx in BASE_PREFIXES:
            return
        nsmap.setdefault(pfx, ns)
        rec = {"c": _local(u), "l": "", "pfx": pfx}
        lab = g.value(uri, RDFS.label)
        rec["l"] = str(lab) if lab else _local(u)
        defn = (
            g.value(uri, SKOS.definition)
            or g.value(uri, IAO_DEF)
            or g.value(uri, RDFS.comment)
        )
        if defn:
            rec["d"] = str(defn)
        alts = []
        for sp in SYN_PREDS:
            for v in g.objects(uri, sp):
                sv = str(v).strip()
                if sv and sv.lower() != rec["l"].lower() and sv not in alts:
                    alts.append(sv)
        if alts:
            rec["a"] = alts[:15]
        rec["u"] = u
        if kind == "props":
            dom = sorted(
                {
                    x
                    for d in g.objects(uri, RDFS.domain)
                    for x in _cls_iris(d, g)
                }
            )
            rng = sorted(
                {
                    x
                    for r in g.objects(uri, RDFS.range)
                    for x in _cls_iris(r, g)
                }
            )
            if dom:
                rec["dom"] = dom
            if rng:
                rec["rng"] = rng

            # Declared inverse relations (owl:inverseOf, in either
            # direction). The map can then show a real inverse pair as
            # one edge with two heads. The code never infers an inverse.
            def _cur(x):
                xs = str(x)
                p2 = ns2pfx.get(_ns(xs)) or _derive_prefix(_ns(xs))
                lo = _local(xs)
                return (p2 + ":" + lo) if (p2 and lo) else lo

            inv = []
            for x in g.objects(uri, OWL.inverseOf):
                if isinstance(x, URIRef):
                    inv.append(_cur(x))
            for x in g.subjects(OWL.inverseOf, uri):
                if isinstance(x, URIRef):
                    inv.append(_cur(x))
            if inv:
                rec["inv"] = sorted(set(inv))
            if (uri, RDF.type, OWL.SymmetricProperty) in g:
                rec["sym"] = True
        else:
            sup = sorted(
                {
                    str(x)
                    for x in g.objects(uri, RDFS.subClassOf)
                    if isinstance(x, URIRef)
                }
            )
            if sup:
                rec["sup"] = sup
            # rdf:type classes (instance-of). The range check can then
            # accept a range through instance-of, not only through
            # subClassOf. For example, VolumeFlowRate of QUDT has
            # rdf:type qudt:QuantityKind. It is not a subclass. The code
            # skips structural meta-types (owl:Class,
            # owl:NamedIndividual and others).
            typ = sorted(
                {
                    str(x)
                    for x in g.objects(uri, RDF.type)
                    if isinstance(x, URIRef) and _ns(str(x)) not in _STRUCT
                }
            )
            if typ:
                rec["typ"] = typ
        terms[kind].setdefault(pfx, {})[rec["c"]] = rec

    for c in typed_classes | typed_inds:
        add(c, "classes")
    for p in typed_props:
        add(p, "props")

    classes = [r for byp in terms["classes"].values() for r in byp.values()]
    props = [r for byp in terms["props"].values() for r in byp.values()]
    if not classes and not props:
        return []

    # The primary prefix is the prefix in which this file defines the
    # most classes, then the most properties. If there is a tie, prefer
    # the namespace of the ontology IRI.
    counts = {}
    for r in classes:
        counts[r["pfx"]] = counts.get(r["pfx"], [0, 0])
        counts[r["pfx"]][0] += 1
    for r in props:
        counts.setdefault(r["pfx"], [0, 0])[1] += 1
    onto_pfx = ns2pfx.get(onto_ns) if onto_ns else None

    def score(pfx):
        c, p = counts[pfx]
        return (c, p, 1 if pfx == onto_pfx else 0)

    primary = max(counts, key=score)

    # The rule to keep a namespace comes from the file. No ontology list
    # is hard-coded. Keep the primary vocabulary. Also keep each
    # namespace that the file binds to a @prefix AND fills
    # substantially. This removes the undeclared module namespaces (for
    # example the licence and data of SWO). It also removes small
    # declared cross-references (for example foaf in BIBO). The evidence
    # comes only from the file.
    declared_ns = set(ns2pfx.keys())
    primary_n = counts[primary][0] or 1
    primary_p = counts[primary][1] or 0
    keep = {primary}
    for pfx in counts:
        if pfx == primary:
            continue
        if nsmap.get(pfx, "") not in declared_ns:
            continue
        cn, pn = counts[pfx]
        # Keep a namespace if it is substantial in classes OR in
        # properties. The schema namespace of QUDT has about 94 object
        # properties but few classes. A check by class count only would
        # wrongly remove all relations.
        if cn >= max(8, 0.10 * primary_n) or pn >= max(5, 0.10 * primary_p):
            keep.add(pfx)
    classes = [r for r in classes if r["pfx"] in keep]
    props = [r for r in props if r["pfx"] in keep]
    nsmap = {p: n for p, n in nsmap.items() if p in keep}

    # Label the primary vocabulary of the file with the file name
    # (cco.ttl gives cco). This is the prefix that the user controls.
    # Keep the real namespace IRI, so that the curies expand correctly.
    fp = _file_prefix(nm)
    # Do not rename the primary vocabulary to the file name if that name
    # already belongs to a different kept namespace. The QUDT file has
    # the name "qudt", but its vocabulary with the most classes is
    # unit:. The real qudt: SCHEMA namespace (with the properties) is a
    # different namespace. A rename of unit to qudt would overwrite the
    # schema.
    _collision = (
        fp in nsmap and nsmap.get(fp) and nsmap.get(fp) != nsmap.get(primary)
    )
    if fp and fp != primary and not _collision:
        for r in classes:
            if r["pfx"] == primary:
                r["pfx"] = fp
        for r in props:
            if r["pfx"] == primary:
                r["pfx"] = fp
        if primary in nsmap:
            nsmap[fp] = nsmap.pop(primary)
        primary = fp

    return [
        {
            "prefix": primary,
            "iri": nsmap.get(primary, onto_ns or ""),
            "file": nm,
            "title": title,
            "desc": desc,
            "classes": classes,
            "props": props,
            "namespaces": nsmap,
        }
    ]


def load_ontologies(folder: Path):
    out = []
    for f in sorted(
        p for p in Path(folder).glob("*") if p.suffix.lower() in EXTS
    ):
        key = str(f)
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        cached = _CACHE.get(key)
        if cached and cached[0] == mtime:
            out.extend(cached[1])
            continue
        try:
            recs = _parse_file(f)
        except Exception as e:  # noqa: BLE001
            recs = [{"file": f.name, "error": str(e)}]
        _CACHE[key] = (mtime, recs)
        out.extend(recs)
    return out


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(load_ontologies(Path(sys.argv[1])), indent=2)[:2000])
