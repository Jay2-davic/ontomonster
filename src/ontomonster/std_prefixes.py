# SPDX-License-Identifier: BSD-3-Clause
"""Bundled standard namespace prefixes.

The module has the universal standard vocabularies: the W3C core
vocabularies, Dublin Core, common linked-data vocabularies and units. It
also has the base namespace obo. It does not have the prefix of each OBO
ontology. The app reads these prefixes from the ontology files that it
loads.

If a prefix is not bundled and is not loaded, the app flags it. An
export cannot give such a prefix a real namespace.

This module is the single source of truth for the Python side
(project_io and exporter). The front end keeps a matching copy in
index.html (STD_PREFIXES).
"""

_OBO = "http://purl.obolibrary.org/obo/"

STD_PREFIXES: dict[str, str] = {
    # W3C and core RDF vocabularies.
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "skosxl": "http://www.w3.org/2008/05/skos-xl#",
    # Dublin Core and related vocabularies.
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dctype": "http://purl.org/dc/dcmitype/",
    "terms": "http://purl.org/dc/terms/",
    # Common linked-data vocabularies.
    "foaf": "http://xmlns.com/foaf/0.1/",
    "prov": "http://www.w3.org/ns/prov#",
    "sh": "http://www.w3.org/ns/shacl#",
    "vann": "http://purl.org/vocab/vann/",
    "void": "http://rdfs.org/ns/void#",
    "dcat": "http://www.w3.org/ns/dcat#",
    "org": "http://www.w3.org/ns/org#",
    "time": "http://www.w3.org/2006/time#",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "sosa": "http://www.w3.org/ns/sosa/",
    "ssn": "http://www.w3.org/ns/ssn/",
    "qb": "http://purl.org/linked-data/cube#",
    "schema": "https://schema.org/",
    # Units and engineering vocabularies.
    "qudt": "http://qudt.org/schema/qudt/",
    "unit": "http://qudt.org/vocab/unit/",
    "quantitykind": "http://qudt.org/vocab/quantitykind/",
    "cco": "https://www.commoncoreontologies.org/",
    "iof": "https://spec.industrialontologies.org/ontology/core/Core/",
    "pmd": "https://w3id.org/pmd/co/",
    "emmo": "https://w3id.org/emmo#",
    # The OBO base namespace.
    "obo": _OBO,
}

# NOTE: The code does not hard-code OBO namespaces or other domain
# namespaces. The code reads them from the ontology files when it loads
# them. rdflib parses the real @prefix and xmlns declarations and the
# URIs. A hard-coded namespace is a guess and can mislead. This module
# keeps only the universal standard vocabularies above.


def dedouble(base: str, local: str) -> str:
    """Join an OBO namespace and a local name without a double token.

    An OBO prefix binds to .../obo/<TOK>_. A term has the full local
    name <TOK>_<id>, for example the prefix bfo: and the name
    BFO_0000176. A simple join gives .../obo/BFO_BFO_0000176. If the
    local name starts with the token at the end of the base, remove the
    repeated token. The result is .../obo/BFO_0000176.
    """
    if base.endswith("_"):
        # The token is the last part of the base, for example "BFO_".
        tok = base.rstrip("/").rsplit("/", 1)[-1]
        if tok and local.startswith(tok):
            return base[: -len(tok)] + local
    return base + local


def canon_curie(curie: str, pm: dict) -> str:
    """Normalize a curie so that it expands correctly under pm.

    If the prefix binds to .../obo/<TOK>_ and the local name repeats
    <TOK>_, remove the repeated token from the local name. For example,
    bfo:BFO_0000176 becomes bfo:0000176. This expands to
    .../obo/BFO_0000176. Do not change a curie that is not OBO.
    """
    if ":" not in curie:
        return curie
    p, lo = curie.split(":", 1)
    base = pm.get(p)
    if base and base.endswith("_"):
        tok = base.rstrip("/").rsplit("/", 1)[-1]
        if tok and lo.startswith(tok):
            return f"{p}:{lo[len(tok):]}"
    return curie


_OBO_NS = "http://purl.obolibrary.org/obo/"


def obo_curie(curie: str, pm: dict) -> str:
    """Write an OBO term in the standard form obo:<TOK>_<id>.

    For example, bfo:BFO_0000176 and bfo:0000176 both become
    obo:BFO_0000176. Do not change a curie that is not OBO.
    """
    if ":" not in curie:
        return curie
    p, lo = curie.split(":", 1)
    base = pm.get(p, "")
    if isinstance(base, str) and base.startswith(_OBO_NS):
        start = len(_OBO_NS)
        # The tail is "BFO_" for a prefix of one ontology. The tail is
        # empty for the bare obo prefix.
        tail = base[start:]
        if tail and lo.startswith(tail):
            return "obo:" + lo
        return "obo:" + tail + lo
    return curie


def obo_binding_map(pm: dict) -> dict:
    """Return a prefix map with all .../obo/<TOK>_ prefixes collapsed to
    one obo binding.

    Use the map for the output prefix declarations. OBO terms then
    serialize in the same way, as obo:<TOK>_<id>, in the ontology,
    SHACL, OWL and JSON Schema. Do not change a prefix that is not OBO.
    """
    out = {}
    saw_obo = False
    for p, n in pm.items():
        if isinstance(n, str) and n.startswith(_OBO_NS):
            saw_obo = True
            # Remove each .../obo/ prefix. The code adds one obo binding
            # below.
            continue
        out[p] = n
    if saw_obo:
        out["obo"] = _OBO_NS
    return out
