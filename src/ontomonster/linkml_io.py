# SPDX-License-Identifier: BSD-3-Clause
"""Import any LinkML asset through LinkML validation and translation.

The module validates a LinkML schema (YAML) in the same process. It uses
SchemaView from linkml_runtime. It then translates the schema into the
term-centric project model of the editor. This lets the user open any
LinkML asset.

The caller sends the other formats (ttl, owl, rdf and json) to other
code. This module handles the YAML and LinkML path. It also converts a
schema back to a project.
"""

from __future__ import annotations
import re
import yaml
from linkml_runtime.utils.schemaview import SchemaView
from linkml_runtime.loaders import yaml_loader
from linkml_runtime.linkml_model.meta import SchemaDefinition

LINKML2XSD = {
    "string": "xsd:string",
    "integer": "xsd:integer",
    "decimal": "xsd:decimal",
    "float": "xsd:float",
    "double": "xsd:double",
    "boolean": "xsd:boolean",
    "date": "xsd:date",
    "datetime": "xsd:dateTime",
    "time": "xsd:time",
    "uri": "xsd:anyURI",
    "uriorcurie": "xsd:anyURI",
    "ncname": "xsd:string",
}


def _safe(s: str) -> str:
    return re.sub(r"\W+", "_", s or "").strip("_") or "x"


def validate_schema_text(yaml_text: str):
    """Validate a LinkML schema with the LinkML runtime.

    Load the YAML into a SchemaDefinition. Then build a SchemaView. Each
    step raises an error if the structure of the schema is not valid.
    Return the tuple (ok, notes).
    """
    notes, ok = [], True
    try:
        sd = yaml_loader.loads(yaml_text, target_class=SchemaDefinition)
        sv = SchemaView(sd)
        notes.append(
            f"LinkML schema valid: {len(sv.all_classes())} classes, "
            f"{len(sv.all_slots())} slots"
        )
    except Exception as e:  # noqa: BLE001
        ok = False
        notes.append(f"LinkML schema error: {e}")
    return ok, notes


def schema_text_to_project(yaml_text: str) -> dict:
    """Convert LinkML schema YAML to a term-centric project.

    Validate the schema first.
    """
    ok, notes = validate_schema_text(yaml_text)
    if not ok:
        return {"error": "LinkML validation failed.", "validation": notes}
    try:
        data = yaml.safe_load(yaml_text)
    except Exception as e:  # noqa: BLE001
        return {"error": f"Invalid YAML: {e}"}
    if not isinstance(data, dict):
        return {"error": "YAML is not a LinkML schema (expected a mapping)."}

    prefixes = data.get("prefixes", {}) or {}
    default_prefix = data.get("default_prefix") or "ex"
    base = data.get("id", "http://example.org/ontology/")
    iri = prefixes.get(default_prefix) or (base.rstrip("/#") + "/")
    classes = data.get("classes", {}) or {}
    slots = data.get("slots", {}) or {}
    types = data.get("types", {}) or {}
    default_range = data.get("default_range", "string")

    def slot_pred(sname):
        sd = slots.get(sname, {}) or {}
        return sd.get("slot_uri") or sname

    def range_row(pred, rng):
        if rng and rng in classes:
            # The range is a reference to a class.
            return {"pred": pred, "val": _safe(rng)}
        if rng in LINKML2XSD:
            return {"pred": pred, "val": "", "dtype": LINKML2XSD[rng]}
        if rng in types:  # The range is a custom type. Use its base type.
            base_t = (types.get(rng, {}) or {}).get("typeof") or (
                types.get(rng, {}) or {}
            ).get("base")
            return {
                "pred": pred,
                "val": "",
                "dtype": LINKML2XSD.get((base_t or "").lower(), "xsd:string"),
            }
        if rng:  # The range is an unknown class. Treat it as a reference.
            return {"pred": pred, "val": _safe(rng)}
        return {
            "pred": pred,
            "val": "",
            "dtype": LINKML2XSD.get(default_range, "xsd:string"),
        }

    terms = []
    for cname, cdef in classes.items():
        cdef = cdef or {}
        kids = [{"pred": "rdf:type", "val": "owl:Class"}]
        label = cdef.get("title") or cname
        kids.append({"pred": "rdfs:label", "val": '"' + str(label) + '"'})
        desc = cdef.get("description")
        kids.append(
            {
                "pred": "skos:definition",
                "val": ('"' + str(desc) + '"') if desc else "",
            }
        )
        if cdef.get("is_a"):
            kids.append(
                {"pred": "rdfs:subClassOf", "val": _safe(cdef["is_a"])}
            )
        for mx in cdef.get("mixins") or []:
            kids.append({"pred": "rdfs:subClassOf", "val": _safe(mx)})
        su = cdef.get("slot_usage", {}) or {}
        for sname in cdef.get("slots") or []:
            pred = slot_pred(sname)
            rng = (su.get(sname, {}) or {}).get("range") or (
                slots.get(sname, {}) or {}
            ).get("range")
            kids.append(range_row(pred, rng))
        terms.append({"name": cname, "children": kids})

    out = {"prefix": default_prefix, "iri": iri, "terms": terms}
    if notes:
        out["validation"] = notes
    return out
