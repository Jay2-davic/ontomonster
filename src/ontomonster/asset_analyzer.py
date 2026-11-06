# SPDX-License-Identifier: BSD-3-Clause
"""Analyze a data asset (prototype) for OntoMonster.

The module reads a data asset (CSV, HuggingFace yaml and md card, XML,
JSON or Parquet). It makes one normalized draft. The app can use the
draft to fill a workspace in advance. The draft has these parts:

- ONE parent term for each asset (or for each table, sheet or config).
- Many LEAF rows (label, altLabels, definition and datatype). The
  predicates are BLANK.
- GROUPS for nesting (dotted CSV headers, the XML element tree, HF
  struct and sequence).
- EDGES that the source states. The endpoints are filled and the
  predicate is BLANK.
- A content HASH. It answers the question "Did the app analyze this file
  before?"
- Conservative type detection, flags for enums and sensitive text, and
  data on confidence and provenance.

The module never guesses the semantics of subclass and predicate. The
user aligns them later.

Design rule: each reader returns a draft dictionary of the SAME shape
(see build_draft).
"""

from __future__ import annotations
import csv as _csv
import datetime as _dt
import hashlib
import io
import os
import re
from collections import Counter, OrderedDict

# Utilities.

_NA = {"", "na", "nan", "null", "none", "n/a", "#n/a"}


def _is_missing(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in _NA)


_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
_BOOL = {"true", "false"}
_PATHY = re.compile(
    r"([A-Za-z]:[\\/]|[\\/]{1,2}[\w.$-]+[\\/]|https?://|@[\w.-]+\.\w+)"
)


def sniff_datatype(values):
    """Assign an xsd type only if ALL non-missing samples agree.

    This rule is conservative.
    """
    vals = [str(v).strip() for v in values if not _is_missing(v)]
    if not vals:
        return None, 0.0

    def allmatch(pred):
        return all(pred(v) for v in vals)

    if allmatch(lambda v: _DT_RE.match(v)):
        return "xsd:dateTime", 1.0
    if allmatch(lambda v: _DATE_RE.match(v)):
        return "xsd:date", 1.0
    if allmatch(lambda v: _INT_RE.match(v)):
        return "xsd:integer", 1.0
    if allmatch(lambda v: _FLOAT_RE.match(v)):
        return "xsd:decimal", 0.95
    if allmatch(lambda v: v.lower() in _BOOL):
        return "xsd:boolean", 1.0
    return "xsd:string", 0.6


def detect_enum(values, max_card=12, max_frac=0.30):
    """Detect a small closed set of repeated strings.

    It is a candidate for a code list.
    """
    vals = [str(v).strip() for v in values if not _is_missing(v)]
    if len(vals) < 4:
        return None
    distinct = sorted(set(vals))
    if 1 < len(distinct) <= max_card and len(distinct) <= max_frac * len(vals):
        # Do not treat a value set that is only numeric, date or
        # datetime as an enum.
        if all(_FLOAT_RE.match(d) for d in distinct):
            return None
        if all(_DATE_RE.match(d) or _DT_RE.match(d) for d in distinct):
            return None
        return distinct
    return None


def index_leaf(name, values, provenance):
    """Detect an index column.

    If a column has no name, or an index-like name, and holds a running
    integer, model it as a row ordinal. Do not model it as a domain
    field. Return a leaf or None.
    """
    if str(name).strip() not in (
        "",
        "index",
        "__index_level_0__",
        "unnamed: 0",
    ):
        return None
    nonmiss = [v for v in values if not _is_missing(v)]
    if nonmiss and all(_INT_RE.match(str(v).strip()) for v in nonmiss):
        return leaf(
            "row_index",
            definition="Row ordinal from the source file "
            "(position in the dataset; not a domain attribute).",
            datatype="xsd:integer",
            dt_conf=1.0,
            provenance=provenance,
        )
    return None


def is_sensitive(values):
    # The "sensitivity" check of paths and URLs is removed: a string is
    # only a string. The function stays as a no-op. Existing calls then
    # remain valid and flag no fields.
    return False


# A map from datatype to CCO class. It is conservative: it assigns a
# class only when it is confident, and only for fields with one value. A
# numeric or temporal value of one field is confidently measurement
# information (MICE). The code leaves strings, booleans and enums
# unassigned. The user aligns them. Each class is a CCO superclass that
# is a URI-safe reference (a curie). It is not a free-text label,
# because a value with spaces cannot be a class reference and breaks the
# RDF and Turtle serialization. Each curie is a REAL CCO ont-ID that
# resolves. An import then loads an actual CCO URI and not a free-text
# label. The user can remap to the exact CCO IRI during the review.
_MICE = "cco:ont00001163"  # Class: Measurement Information Content Entity.
_ICE = "cco:ont00000958"  # Class: Information Content Entity.
_DICE = "cco:ont00000853"  # Class: Descriptive Information Content Entity.
_IBE = "cco:ont00000253"  # Class: Information Bearing Entity.
_PRESCRIBED_BY = "cco:prescribed_by"
# Real BFO relations (the merged CCO imports BFO). The relation
# "is_carried_by" does NOT exist. ICE to IBE (content that a file or
# dataset bears): generically depends on (BFO_0000084). ICE to ICE (a
# subfield in a content container, where the parent is not a bearer):
# continuant part of (BFO_0000176).
_GDEP = "bfo:0000084"  # Generically depends on (field to file or dataset IBE).
_CPART = "bfo:0000176"  # Continuant part of (subfield to its ICE container).
_CCO_BY_DTYPE = {
    "xsd:decimal": _MICE,
    "xsd:double": _MICE,
    "xsd:integer": _MICE,
    "xsd:dateTime": _MICE,
    "xsd:date": _MICE,
}


def cco_for(datatype, multivalued=False):
    """Return a confident CCO superclass for a field with ONE value.

    Otherwise return None.
    """
    if multivalued:
        return None
    return _CCO_BY_DTYPE.get(datatype or "")


_ID_RX = re.compile(
    r"(^|[_\W])(id|ids|identifier|uuid|guid|key|code|pk)$", re.I
)


def _looks_like_id(name):
    """Detect an identifier by its name.

    An id, key or code column is Designative. It is not a measurement.
    """
    n = str(name or "").strip()
    if not n:
        return False
    return bool(_ID_RX.search(n)) or bool(
        re.search(r"[a-z0-9](Id|Ids|Key|Code|Uuid|Guid|Pk)$", n)
    )


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# Draft model.


def leaf(
    label,
    *,
    definition="",
    alt=None,
    datatype=None,
    dt_conf=0.0,
    enum=None,
    multivalued=False,
    sensitive=False,
    samples=None,
    group=None,
    provenance="",
    subclassOf=None,
):
    return OrderedDict(
        label=label,
        altLabels=alt or [],
        definition=definition,
        datatype=datatype,
        predicate=None,  # The predicate is deferred.
        subclassOf=subclassOf,  # A confident CCO class, or None.
        enum=enum,
        multivalued=multivalued,
        sensitive=sensitive,
        group=group,
        confidence=round(dt_conf, 2),
        samples=[str(x) for x in (samples or [])][:3],
        provenance=provenance,
        status="unconfirmed",
    )


def build_draft(
    *,
    filename,
    fmt,
    raw_bytes,
    parents,
    groups=None,
    n_records=None,
    notes=None,
):
    return OrderedDict(
        source=OrderedDict(
            filename=filename,
            format=fmt,
            sha256=sha256_bytes(raw_bytes),
            analyzed_at=_now(),
            n_records=n_records,
        ),
        parents=parents,
        groups=groups or [],
        notes=notes or [],
    )


def parent_term(
    label,
    *,
    definition="",
    alt=None,
    subclassOf=None,
    notes=None,
    props=None,
    seq_next=None,
):
    return OrderedDict(
        label=label,
        altLabels=alt or [],
        definition=definition,
        subclassOf=subclassOf,  # None: the user aligns it later.
        leaves=[],
        edges=[],
        # Bare tokens (without =) become a skos:note of this group.
        notes=notes or [],
        props=props
        or [],  # Variables key="value" become data rows [{pred, val, dtype}].
        # The label of the next group in document order (a deferred
        # edge).
        seq_next=seq_next,
        status="unconfirmed",
    )


# CSV reader.


def read_csv(raw_bytes, filename, sample_rows=400):
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    rows = list(_csv.reader(io.StringIO(text)))
    if not rows:
        return build_draft(
            filename=filename,
            fmt="csv",
            raw_bytes=raw_bytes,
            parents=[],
            n_records=0,
            notes=["empty file"],
        )
    header = rows[0]
    data = rows[1:]
    ncols = len(header)
    cols = [[r[i] if i < len(r) else "" for r in data] for i in range(ncols)]

    parent = parent_term(
        "Tabular dataset",
        subclassOf=_IBE,
        definition="Tabular dataset (rows and columns).",
    )
    parents = [parent]
    # Maps a prefix to a class term (one class for each grouping).
    group_terms = OrderedDict()
    notes = []

    def _edge(frm, to):
        frm["edges"].append(
            OrderedDict(
                to=to,
                predicate=None,
                confidence=0.0,
                provenance=f"csv:group {to}",
            )
        )

    for i, name in enumerate(header):
        colvals = cols[i]
        raw = name.strip()
        # A column without a name, or with an index-like name, that has
        # a running integer is a row ordinal. It is not a field.
        il = index_leaf(raw, colvals, provenance=f"csv:col[{i}] ('{raw}')")
        if il is not None:
            notes.append(
                "Detected an index-like running-integer column -> treated "
                "as row index (ordinal), not a domain field."
            )
            parent["leaves"].append(il)
            continue
        if raw == "":
            raw = f"column_{i}"

        # A dotted header becomes a CLASS for each grouping. For
        # example, InitialGuess.R0 gives the class InitialGuess and the
        # leaf R0.
        target = parent
        lbl = raw
        if "." in raw:
            grp, lbl = raw.split(".", 1)
            if grp not in group_terms:
                gt = parent_term(
                    grp,
                    subclassOf=_ICE,
                    definition=f"Grouping '{grp}' within the tabular data.",
                )
                group_terms[grp] = gt
                parents.append(gt)
                # Edge: dataset, blank predicate, grouping class.
                _edge(parent, grp)
            target = group_terms[grp]

        dtype, conf = sniff_datatype(colvals)
        enum = detect_enum(colvals)
        if enum:
            dtype, conf = "xsd:string", 1.0
        sens = is_sensitive(colvals)
        samples = [v for v in colvals if not _is_missing(v)][:3]
        # A column with one scalar value: a numeric or date value gives
        # Measurement ICE. An identifier column (by name) is Designative
        # and never a measurement. The user types everything else.
        if enum:
            sc = None
        elif _looks_like_id(lbl):
            sc = _DICE
        else:
            sc = cco_for(dtype, multivalued=False)
        target["leaves"].append(
            leaf(
                lbl,
                datatype=dtype,
                dt_conf=conf,
                enum=enum,
                sensitive=sens,
                samples=samples,
                subclassOf=sc,
                provenance=f"csv:{raw}",
            )
        )

    if group_terms:
        notes.append(
            f"{len(group_terms)} dotted-prefix grouping(s) promoted to their "
            f"own content (ICE) "
            "container class terms, linked from the dataset by a blank "
            "(deferred) edge."
        )
    notes.append(
        "Dataset typed as InformationBearingEntity; each column is an "
        "InformationContentEntity it carries \u2014 "
        "numeric/date columns proposed as Measurement ICE, id/key/code "
        "columns as Designative ICE, others left "
        "for you to type (Descriptive / Designative / Directive / "
        "Measurement). Confirm/refine the exact terms."
    )
    return build_draft(
        filename=filename,
        fmt="csv",
        raw_bytes=raw_bytes,
        parents=parents,
        groups=[],
        n_records=len(data),
        notes=notes,
    )


# HuggingFace yaml and md.


def _split_frontmatter(text):
    """HF rule: if the file starts with --- and has a closing ---, split
    it.

    Otherwise all of it is md.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if m:
        return m.group(1), m.group(2)
    return "", text


def _md_field_descriptions(md):
    """Read the bullet descriptions of Data Fields.

    The form is: - `name`: description.
    """
    out = {}
    m = re.search(
        r"#+\s*Data Fields(.*?)(\n#+\s|\Z)", md, re.DOTALL | re.IGNORECASE
    )
    body = m.group(1) if m else md
    for line in body.splitlines():
        mm = re.match(r"\s*[-*]\s*`?([\w.]+)`?\s*[:\-]\s*(.+)", line)
        if mm:
            out[mm.group(1)] = mm.group(2).strip()
    return out


def _md_summary(md):
    m = re.search(
        r"#+\s*Dataset Summary\s*\n+(.+?)(\n#+\s|\Z)",
        md,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:600]
    # Otherwise use the first paragraph that is not a heading.
    for para in re.split(r"\n\s*\n", md):
        p = para.strip()
        if p and not p.startswith("#"):
            return re.sub(r"\s+", " ", p)[:600]
    return ""


def _xsd_from_typename(t):
    """Map a type name that a person wrote ("float", "int", "bool",
    "str", "date" and others) to an xsd type.
    """
    s = str(t or "").strip().lower()
    if s in ("bool", "boolean"):
        return "xsd:boolean"
    if s in ("int", "integer", "long", "short", "count"):
        return "xsd:integer"
    if s in ("float", "double", "decimal", "real", "number", "numeric"):
        return "xsd:decimal"
    if s in ("date",):
        return "xsd:date"
    if s in ("datetime", "timestamp"):
        return "xsd:dateTime"
    if s in ("time",):
        return "xsd:time"
    return "xsd:string"


def _dtype_from_desc(desc):
    """Infer an xsd datatype from the free text of a field description
    or from a trailing (type).
    """
    s = (desc or "").lower()
    m = re.search(
        r"\((float|double|decimal|real|int(?:eger)?|bool(?:ean)?|str(?:ing)?"
        r"|date(?:time)?|time|number|numeric)\)\s*$",
        s,
    )
    if m:
        return _xsd_from_typename(m.group(1))
    if re.search(r"\bbool(ean)?\b|\btrue\s*/\s*false\b|\bwhether\b", s):
        return "xsd:boolean"
    if re.search(r"\b(integer|count)\b|\bnumber of\b|\bint\b", s):
        return "xsd:integer"
    if re.search(r"\b(float|decimal|double|real|numeric|number)\b", s):
        return "xsd:decimal"
    return "xsd:string"


def _clean_desc(desc):
    return re.sub(
        r"\s*\((?:float|double|decimal|real|int(?:eger)?|bool(?:ean)?"
        r"|str(?:ing)?|date(?:time)?|time|number|numeric)\)\s*$",
        "",
        desc or "",
        flags=re.IGNORECASE,
    ).strip()


def _autodetect_dtype(v):
    """Infer an xsd datatype from a literal VALUE.

    A description does not count.
    """
    s = (v or "").strip()
    if s == "":
        return "xsd:string"
    if re.fullmatch(r"(true|false|yes|no)", s, re.IGNORECASE):
        return "xsd:boolean"
    if re.fullmatch(
        r"0\d+", s
    ):  # An id with a leading zero (for example 010920244) stays a string.
        return "xsd:string"
    if re.fullmatch(r"[+-]?\d+", s):
        return "xsd:integer"
    if re.fullmatch(
        r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?", s
    ) and re.search(r"[.eE]", s):
        return "xsd:decimal"
    if re.fullmatch(r"\d{4}-\d\d-\d\d", s):
        return "xsd:date"
    if re.fullmatch(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d)?", s):
        return "xsd:dateTime"
    return "xsd:string"


def _clean_field_name(s):
    """Remove harmless markup that must not be part of a field name.

    The forms `name`, **name**, *name*, _name_, "name" and 'name' become
    name. The function does not remove square brackets. A line such as
    '[x]:' is noise. It is not a field.
    """
    s = (s or "").strip()
    prev = None
    while prev != s and s:
        prev = s
        for a, b in (
            ("`", "`"),
            ("**", "**"),
            ("*", "*"),
            ("_", "_"),
            ('"', '"'),
            ("'", "'"),
        ):
            if len(s) > len(a) + len(b) and s.startswith(a) and s.endswith(b):
                start, stop = len(a), len(s) - len(b)
                s = s[start:stop].strip()
    return s


def _split_value_comment(s):
    """Split 'value  # comment' into (value, comment).

    The # is a comment marker only when it is OUTSIDE quotes and has
    whitespace before it (or is at the start). A # inside a quoted value
    or in a URL fragment (http://x#y) is then not a comment.
    """
    inq = None
    for i, ch in enumerate(s):
        if inq:
            if ch == inq:
                inq = None
            continue
        if ch in ('"', "'"):
            inq = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            after = i + 1
            return s[:i].strip(), s[after:].strip()
    return s.strip(), ""


def _strip_quotes(v):
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


_MD_META_KEYS = {
    "license",
    "licenses",
    "language",
    "languages",
    "tags",
    "pretty_name",
    "task_categories",
    "task_ids",
    "size_categories",
    "source_datasets",
    "annotations_creators",
    "language_creators",
    "multilinguality",
    "paperswithcode_id",
    "dataset_info",
    "configs",
    "config_names",
    "viewer",
    "extra_gated_fields",
    "extra_gated_prompt",
    "dataset_summary",
    "description",
    "homepage",
    "repository",
    "citation",
    "version",
    "language_details",
}
_MD_WRAPPER_KEYS = {
    "fields",
    "columns",
    "schema",
    "properties",
    "attributes",
    "variables",
    "params",
    "parameters",
}


def _parse_md_fields(md):
    """Parse indented lines 'name: value  # definition' into a tree of
    fields.

    - The function ignores full-line # comments and markdown headers.
    - A trailing '# ...' after the value is the definition of the field.
    - The indentation (one tab or two spaces for each level) nests a
      field under the field above it. The child links to its parent with
      cco:prescribed_by (child to parent).
    - The function leaves bulleted lines ('- name: ...') for the prose
      path Data Fields.

    Return a flat list of dictionaries {name, value, definition, level,
    parent} in document order.
    """


def _parse_md_fields(md, continuation=False):
    fields = []
    _TODO = re.compile(r"(?i)(todo|fixme|xxx|tbd|wip)\b")

    def _is_sep(t):  # A rule such as '# ---' or '# ===' is not real content.
        return bool(re.fullmatch(r"[-=*_ ]{2,}", (t or "").strip()))

    # The last field. It is used for a definition that continues on more
    # lines.
    last = None
    for raw in md.splitlines():
        if not raw.strip():
            last = None  # A blank line ends a continued definition.
            continue
        stripped = raw.strip()
        if stripped.startswith("#"):  # A full-line comment.
            if (
                continuation and last is not None
            ):  # A continuation of the definition of the previous field.
                body = re.sub(r"<[^>]*>", "", stripped.lstrip("#")).strip()
                if body and not _is_sep(body):  # Skip the separators '# ---'.
                    last["definition"] = (
                        (last["definition"] + " " + body).strip()
                        if last["definition"]
                        else body
                    )
            continue  # Headings and stray comments are otherwise ignored.
        lead = len(raw) - len(raw.lstrip(" \t"))
        level = len(raw[:lead].replace("\t", "  ")) // 2
        was_list = bool(re.match(r"[-*]\s+", stripped))
        if was_list:  # A leading dash.
            if (
                continuation
                # Front matter: remove '- '. The item can be a field or
                # a value item.
            ):
                stripped = re.sub(r"^[-*]\s+", "", stripped)
            else:  # Body: a bulleted line goes to the Data Fields path.
                last = None
                continue
        if re.match(
            r"\[[^\]]+\]:\s", stripped
        ):  # A markdown reference-link definition is not a field.
            last = None
            continue
        line = re.sub(
            r"<[^>]*>",
            "",
            stripped,
            # Remove <...> placeholders and annotations. They are not
            # relevant.
        ).strip()
        if (
            not line
        ):  # The line was only a <...> placeholder (for example <TODO: ...>).
            last = None
            continue
        if (
            ":"
            not in line
            # NO colon: this is a VALUE, not a field. This is also true
            # if it had '- '.
        ):
            # Remove any trailing '# comment'.
            v, c = _split_value_comment(line)
            v = _strip_quotes(v.strip())
            if continuation and last is not None and v and not _is_sep(v):
                last["value"] = (
                    (last["value"] + ", " + v) if last.get("value") else v
                )
                last.setdefault("values", []).append(v)
                if c and not _is_sep(
                    c
                    # A '# comment' on a value line extends the
                    # definition of the field.
                ):
                    last["definition"] = (
                        (last["definition"] + " " + c).strip()
                        if last.get("definition")
                        else c
                    )
            continue  # It never becomes a field or a subfield.
        name_part, rest = line.split(":", 1)  # First split the field name.
        value, definition = _split_value_comment(
            rest
        )  # Then remove a trailing '# comment' from the value.
        if _is_sep(definition):  # An inline '# ---' is not a definition.
            definition = ""
        value = _strip_quotes(value)
        name = _clean_field_name(name_part)
        if not name or _TODO.match(
            name
        ):  # A blank line, or a note with TODO, FIXME or TBD: ignore it.
            last = None
            continue
        f = {
            "name": name,
            "value": value,
            "definition": definition,
            "level": level,
            "parent": None,
        }
        fields.append(f)
        last = f  # Next '# ...' lines extend the definition of this field.
    # Assign the ids, the parents and the top-level root from the
    # indentation.
    stack = []
    for i, f in enumerate(fields):
        f["id"] = i
        while stack and stack[-1]["level"] >= f["level"]:
            stack.pop()
        f["parent_id"] = stack[-1]["id"] if stack else None
        f["parent"] = stack[-1]["name"] if stack else None
        f["root"] = stack[0]["name"] if stack else f["name"]
        stack.append(f)
    return fields


def _walk_features(features, descs, container, parents, path=""):
    """Convert HF features to leaves on the container.

    A struct or a sequence of structs becomes its OWN class term
    (requirement 1). A blank (deferred) edge links it from its
    container.
    """

    def xmap(dt):
        if not isinstance(dt, str):
            return "xsd:string"
        m = {
            "string": "xsd:string",
            "large_string": "xsd:string",
            "int8": "xsd:integer",
            "int16": "xsd:integer",
            "int32": "xsd:integer",
            "int64": "xsd:integer",
            "float16": "xsd:decimal",
            "float32": "xsd:decimal",
            "float64": "xsd:decimal",
            "bool": "xsd:boolean",
        }
        if dt.startswith("timestamp"):
            return "xsd:dateTime"
        return m.get(dt, "xsd:string")

    def classlabel_names(feat):
        if isinstance(feat.get("class_label"), dict):
            return feat["class_label"].get("names")
        dt = feat.get("dtype")
        if isinstance(dt, dict) and isinstance(dt.get("class_label"), dict):
            return dt["class_label"].get("names")
        if dt == "ClassLabel":
            return feat.get("names")
        return None

    for feat in features or []:
        name = feat.get("name", "?")
        full = f"{path}{name}"
        desc = descs.get(full) or descs.get(name) or ""
        names = classlabel_names(feat)
        sub = feat.get("struct") or feat.get("sequence") or feat.get("list")
        seq = ("sequence" in feat) or ("list" in feat)
        if names is not None:
            container["leaves"].append(
                leaf(
                    name,
                    definition=desc,
                    datatype="xsd:string",
                    dt_conf=1.0,
                    enum=names,
                    provenance=f"hf:features:{full} (ClassLabel)",
                )
            )
        elif isinstance(sub, list):
            # A struct or a sequence of named sub-fields becomes a CLASS
            # term of its own (a table gives MICE).
            cls = parent_term(
                name,
                subclassOf=_ICE,
                definition=desc or f"Structured group '{name}'.",
            )
            parents.append(cls)
            container["edges"].append(
                OrderedDict(
                    to=name,
                    predicate=None,
                    confidence=0.0,
                    provenance=f"hf:{'sequence' if seq else 'struct'}:{full}",
                )
            )
            _walk_features(sub, descs, cls, parents, path=f"{full}.")
        elif isinstance(sub, dict) and "dtype" in sub:
            # A sequence or list of a scalar becomes a multivalued leaf
            # on the container.
            dt = xmap(sub["dtype"])
            kind = "sequence" if seq else "struct"
            container["leaves"].append(
                leaf(
                    name,
                    definition=desc,
                    datatype=dt,
                    dt_conf=1.0,
                    multivalued=seq,
                    subclassOf=cco_for(dt, multivalued=seq),
                    provenance=f"hf:features:{full} ({kind})",
                )
            )
        elif "dtype" in feat:
            dt = xmap(feat["dtype"])
            container["leaves"].append(
                leaf(
                    name,
                    definition=desc,
                    datatype=dt,
                    dt_conf=1.0,
                    subclassOf=cco_for(dt, multivalued=False),
                    provenance=f"hf:features:{full} (dtype={feat['dtype']})",
                )
            )
        else:
            container["leaves"].append(
                leaf(
                    name,
                    definition=desc,
                    multivalued=seq,
                    provenance=f"hf:features:{full}",
                )
            )


def read_hf_card(raw_bytes, filename):
    import yaml

    text = raw_bytes.decode("utf-8-sig", errors="replace")
    fm, md = _split_frontmatter(text)
    meta = {}
    if fm.strip():
        try:
            meta = yaml.safe_load(fm) or {}
        except Exception as e:
            meta = {}
    descs = _md_field_descriptions(md)
    summary = _md_summary(md)

    title = (
        meta.get("pretty_name")
        or os.path.splitext(os.path.basename(filename))[0]
        or "Dataset"
    )
    parent = parent_term(title, subclassOf=_IBE, definition=summary)
    # Metadata at card level gives the parent context (altLabels and
    # notes). It does not give leaves.
    tags = meta.get("tags") or []
    if tags:
        parent["altLabels"] = [
            str(t) for t in tags if isinstance(t, (str, int))
        ][:6]
    notes = []
    for k in ("license", "task_categories", "language", "size_categories"):
        if k in meta:
            notes.append(f"{k}: {meta[k]}")

    # The features can be at the top level or under dataset_info (it can
    # be a list of configs).
    parents = [parent]
    di = meta.get("dataset_info")
    feature_blocks = []
    if isinstance(di, dict) and di.get("features"):
        feature_blocks.append(di["features"])
    elif isinstance(di, list):
        for cfg in di:
            if isinstance(cfg, dict) and cfg.get("features"):
                feature_blocks.append(cfg["features"])
    elif meta.get("features"):
        feature_blocks.append(meta["features"])

    for fb in feature_blocks:
        _walk_features(fb, descs, parent, parents)

    # Fallbacks for cards that a person wrote by hand and that have no
    # machine-readable HF features block:
    if not parent["leaves"]:

        def _emit(mdf, where):
            parent["subclassOf"] = (
                _IBE  # The file or card is the information bearer.
            )
            # The same field name is ONE standard term. The code makes
            # one leaf for each different (name, parent). A name that is
            # used under several parents then gives several
            # prescribed_by links on the same term. The front end merges
            # them by label. Exact repeats of (name, parent) collapse.
            from collections import defaultdict as _dd

            parents_by_name = _dd(set)
            nesting_names = set(
                f["parent"] for f in mdf if f["parent"]
            )  # Names that have subfields are bearers.
            for f in mdf:
                parents_by_name[f["name"]].add(f["parent"] or title)
            seen = set()
            n_added = n_sub = n_dup = n_ibe = 0
            for f in mdf:
                to = f["parent"] or title
                key = (f["name"], to)
                # The same name AND the same parent: an exact duplicate.
                if key in seen:
                    n_dup += 1
                    continue
                seen.add(key)
                nests = (
                    f["name"]
                    in nesting_names
                    # A field with subfields is a CONTENT container. It
                    # is still an ICE.
                )
                sub = (
                    _ICE
                    if nests
                    else _DICE
                    # A container gives a generic ICE (the role is set
                    # later). A leaf gives a Designative ICE.
                )
                if nests:
                    n_ibe += 1
                dt = _autodetect_dtype(f["value"])
                lf = leaf(
                    f["name"],
                    definition=f["definition"],
                    datatype=dt,
                    dt_conf=(0.9 if dt != "xsd:string" else 0.4),
                    samples=(
                        f.get("values")
                        or ([f["value"]] if f["value"] else None)
                    ),
                    subclassOf=sub,
                    provenance="md:%s:%s" % (where, f["name"]),
                )
                # The parent is the file (IBE) for a top-level field:
                # use generically depends on. The parent is a container
                # field (ICE) for a subfield: use continuant part of.
                _rel = _CPART if f["parent"] else _GDEP
                lf["prescribedBy"] = OrderedDict(pred=_rel, to=to)
                parent["leaves"].append(lf)
                n_added += 1
                if f["parent"]:
                    n_sub += 1
            n_multi = sum(
                1 for nm, ps in parents_by_name.items() if len(ps) > 1
            )
            msg = (
                "%d generically-depends-on link(s) from the %s ('name: value' "
                "with indentation); the file/card is the "
                "InformationBearingEntity, every field is an "
                "InformationContentEntity it carries (containers stay "
                "generic ICE for you to type as "
                "Designative/Directive/Descriptive/Measurement); "
                "datatypes autodetected from the values; '#' comments "
                "captured as definitions; %d nested under a parent."
                % (
                    n_added,
                    ("front-matter" if where == "fm" else "body"),
                    n_sub,
                )
            )
            if n_multi:
                msg += (
                    " %d field name(s) appear under multiple parents \u2014 "
                    "kept as a single term with a "
                    "prescribed_by link to each parent." % n_multi
                )
            if n_dup:
                msg += " %d exact duplicate(s) collapsed." % n_dup
            msg += " Remap the cco: placeholders as needed."
            notes.append(msg)

        # (a) Fields that the YAML front matter declares (the common
        # case): nested keys and '# comments'. Drop the card-metadata
        # keys (license, tags, dataset_info and others). Unwrap a
        # fields: or schema: container.
        fmf = []
        if fm.strip():
            for f in _parse_md_fields(fm, continuation=True):
                if f["root"].lower() in _MD_META_KEYS:
                    continue  # This is metadata. It is not a field.
                if f["level"] == 0 and f["name"].lower() in _MD_WRAPPER_KEYS:
                    continue  # Drop the wrapper key itself. Keep its children.
                if f["parent"] and f["parent"].lower() in _MD_WRAPPER_KEYS:
                    f["parent"] = None
                    f["parent_id"] = (
                        # Move the children of the wrapper to the top
                        # level.
                        None
                    )
                fmf.append(f)

        # (b) A simple front-matter type map: fields: {mu: float, ...}
        # (or columns: or schema:).
        fmap = None
        for key in ("fields", "columns", "schema"):
            v = meta.get(key)
            if (
                isinstance(v, dict)
                and v
                and all(not isinstance(x, (dict, list)) for x in v.values())
            ):
                fmap = v
                break  # Accept only a flat map {name: type}.
            if (
                isinstance(v, list)
                and v
                and all(isinstance(x, str) for x in v)
            ):
                fmap = OrderedDict((x, "") for x in v)
                break

        if fmf:
            _emit(fmf, "fm")
        elif fmap:
            parent["subclassOf"] = _IBE
            for nm, typ in fmap.items():
                dt = _xsd_from_typename(typ) if typ else "xsd:string"
                lf = leaf(
                    str(nm),
                    definition=descs.get(str(nm), ""),
                    datatype=dt,
                    dt_conf=(1.0 if typ else 0.4),
                    subclassOf=_DICE,
                    provenance="md:frontmatter:%s" % (nm,),
                )
                lf["prescribedBy"] = OrderedDict(pred=_GDEP, to=title)
                parent["leaves"].append(lf)
            notes.append(
                "%d field(s) read from the card's front-matter map (leaf "
                "fields typed as "
                "DesignativeInformationContentEntity)." % len(fmap)
            )
        else:
            # (c) Indented lines 'name: value  # definition' in the BODY
            # (only if the front matter had none).
            mdf = _parse_md_fields(md)
            if len(mdf) >= 2 or (
                mdf and (mdf[0]["definition"] or any(x["level"] for x in mdf))
            ):
                _emit(mdf, "body")
            # (d) The bullet list of the prose section '## Data Fields':
            # - `name`: description (float).
            elif descs:
                for nm, desc in descs.items():
                    dt = _dtype_from_desc(desc)
                    parent["leaves"].append(
                        leaf(
                            str(nm),
                            definition=_clean_desc(desc),
                            datatype=dt,
                            dt_conf=(0.9 if dt != "xsd:string" else 0.4),
                            subclassOf=cco_for(dt, multivalued=False),
                            provenance="md:Data Fields:%s" % (nm,),
                        )
                    )
                notes.append(
                    "%d field(s) read from the prose 'Data Fields' section "
                    "(datatypes inferred from the descriptions \u2014 review "
                    "them)." % len(descs)
                )

    if len(parents) > 1:
        notes.append(
            f"{len(parents)-1} structured feature group(s) promoted to their "
            f"own "
            "class terms, linked from the dataset by a blank (deferred) edge."
        )
    if not parent["leaves"] and len(parents) == 1:
        notes.append(
            "No machine-readable `features` block found; only a parent term "
            "+ definition were extracted from the prose."
        )
    return build_draft(
        filename=filename,
        fmt="hf-card",
        raw_bytes=raw_bytes,
        parents=parents,
        groups=[],
        n_records=None,
        notes=notes,
    )


# XML reader.


def read_xml(raw_bytes, filename, max_depth=6):
    """Read lenient tags. The data is not always well-formed XML.

    - Each <name ...> or <name .../> tag is a GROUP. The tag name is the
      group name.
    - Each key="value" (or key='value') in a tag is a VARIABLE. It
      becomes a data row. The predicate is the variable name, the object
      is the value, and the datatype is detected.
    - A bare token with no = becomes a skos:note on that group.
    - The groups link in document order (a deferred edge to the next
      group), because their order can have a meaning.
    """
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    # Send RDF/XML away. It is an ontology. It is not a data asset.
    if (
        re.search(r"http://www\.w3\.org/1999/02/22-rdf-syntax-ns#", text)
        or "<rdf:RDF" in text
    ):
        return build_draft(
            filename=filename,
            fmt="rdf-xml",
            raw_bytes=raw_bytes,
            parents=[],
            notes=[
                "This XML is RDF/OWL — route to the ontology importer, "
                "not the asset analyzer."
            ],
        )

    tag_re = re.compile(
        r"<\s*([A-Za-z_][^>]*?)\s*/?>", re.S
    )  # Opening and self-closing tags.
    attr_re = re.compile(
        r"""([^\s=<>/"']+)\s*=\s*"([^"]*)"|([^\s=<>/"']+)\s*=\s*'([^']*)'"""
    )

    groups = (
        OrderedDict()
    )  # Maps a tag name to {order, vars: {name: [values...]}, notes: [...]}.
    order = 0
    for m in tag_re.finditer(text):
        inner = m.group(1).strip()
        # A closing tag, <?xml?> or <!-- -->.
        if not inner or inner[0] in "/?!":
            continue
        nm = re.match(r"[^\s/>]+", inner)
        if not nm:
            continue
        name = nm.group(0)
        after_name = nm.end()
        rest = inner[after_name:]
        g = groups.get(name)
        if g is None:
            order += 1
            g = groups[name] = {
                "order": order,
                "vars": OrderedDict(),
                "notes": [],
            }
        for a in attr_re.finditer(rest):  # Variables (key="value").
            k = a.group(1) if a.group(1) is not None else a.group(3)
            v = a.group(2) if a.group(2) is not None else a.group(4)
            g["vars"].setdefault(k, []).append(
                v
            )  # Collect the values ONLY to detect the type.
        leftover = (
            attr_re.sub(" ", rest).replace("/", " ").strip()
        )  # Bare tokens (no =) become skos:note.
        for tok in re.split(r"\s+", leftover):
            if tok and tok not in g["notes"]:
                g["notes"].append(tok)

    if not groups:
        return build_draft(
            filename=filename,
            fmt="xml",
            raw_bytes=raw_bytes,
            parents=[],
            notes=["No <…> tags found."],
        )

    parents, prev = [], None
    # The first-appearance order is the document order.
    for name, g in groups.items():
        p = parent_term(
            name, definition=f"XML group <{name}>.", notes=list(g["notes"])
        )
        for vk, vals in g[
            "vars"
        ].items():  # One term for each DISTINCT variable (no duplicates).
            dt, conf = sniff_datatype(vals)
            p["leaves"].append(
                leaf(
                    vk,
                    datatype=dt,
                    # Keep the name and the value TYPE. Do not keep the
                    # value.
                    dt_conf=conf,
                    group=name,
                    provenance=f"xml:@{vk}",
                )
            )
        parents.append(p)
        if prev is not None:
            # A deferred order edge from the previous group to this
            # group.
            prev["seq_next"] = name
        prev = p

    return build_draft(
        filename=filename,
        fmt="xml",
        raw_bytes=raw_bytes,
        parents=parents,
        groups=[
            OrderedDict(path=name, name=name, parent=None, order=g["order"])
            for name, g in groups.items()
        ],
        n_records=len(parents),
        notes=[
            "Each distinct <tag> is one group named by its tag (duplicates "
            'merged); each key="…" '
            "is captured as a variable term with its value TYPE only (not the "
            "value); bare tokens "
            'with no "=" become a skos:note; groups are linked in document '
            "order via a deferred edge."
        ],
    )


# JSON reader.


def read_json(raw_bytes, filename, max_depth=6):
    """Analyze JSON.

    - A top-level ARRAY of objects gives a tabular dataset. The keys
      become typed fields, as for CSV and Parquet.
    - A top-level OBJECT gives nested groups. Each nested object is a
      group (a folder). Each scalar key becomes a variable term with its
      value TYPE only. This mirrors the XML model.

    The code uses the values only to detect the datatype. It does not
    store them.
    """
    import json as _json

    try:
        data = _json.loads(raw_bytes.decode("utf-8-sig", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return build_draft(
            filename=filename,
            fmt="json",
            raw_bytes=raw_bytes,
            parents=[],
            notes=[f"Invalid JSON: {e}"],
        )

    def _scalars(vals):
        return [
            ("" if v is None else str(v))
            for v in vals
            if not isinstance(v, (dict, list))
        ]

    # Array of records: a tabular dataset.
    if isinstance(data, list):
        records = [r for r in data if isinstance(r, dict)]
        parent = parent_term(
            "JSON dataset",
            subclassOf=_IBE,
            definition="JSON array of records (rows and fields).",
        )
        cols = OrderedDict()
        for r in records:
            for k, v in r.items():
                cols.setdefault(k, []).append(v)
        for k, vals in cols.items():
            mv = any(isinstance(v, list) for v in vals)
            dt, conf = sniff_datatype(_scalars(vals))
            enum = detect_enum(_scalars(vals))
            parent["leaves"].append(
                leaf(
                    k,
                    datatype=("xsd:string" if enum else dt),
                    dt_conf=conf,
                    enum=enum,
                    multivalued=mv,
                    sensitive=is_sensitive(_scalars(vals)),
                    group="JSON dataset",
                    subclassOf=(None if enum else cco_for(dt, multivalued=mv)),
                    provenance=f"json:{k}",
                )
            )
        return build_draft(
            filename=filename,
            fmt="json",
            raw_bytes=raw_bytes,
            parents=[parent],
            groups=[],
            n_records=len(records),
            notes=[
                "JSON array of records read as a tabular dataset; datatypes "
                "sniffed from values."
            ],
        )

    # Object: nested groups. Each object is a group. Scalars are typed
    # variables.
    if isinstance(data, dict):
        parents, groups, order = [], [], [0]

        def add_group(name, obj, depth):
            order[0] += 1
            groups.append(
                OrderedDict(path=name, name=name, parent=None, order=order[0])
            )
            g = parent_term(name, definition=f"JSON object '{name}'.")
            parents.append(g)
            for k, v in obj.items():
                if isinstance(v, dict) and depth < max_depth:
                    g["edges"].append(
                        OrderedDict(
                            to=k,
                            predicate=None,
                            confidence=0.0,
                            provenance=f"json:{name}.{k}",
                        )
                    )
                    add_group(k, v, depth + 1)
                elif (
                    isinstance(v, list)
                    and v
                    and all(isinstance(x, dict) for x in v)
                    and depth < max_depth
                ):
                    merged = OrderedDict()
                    for x in v:
                        for kk, vv in x.items():
                            merged.setdefault(kk, []).append(vv)
                    g["edges"].append(
                        OrderedDict(
                            to=k,
                            predicate=None,
                            confidence=0.0,
                            provenance=f"json:{name}.{k}[]",
                        )
                    )
                    order[0] += 1
                    groups.append(
                        OrderedDict(
                            path=k, name=k, parent=None, order=order[0]
                        )
                    )
                    gg = parent_term(
                        k, definition=f"JSON array of objects '{k}'."
                    )
                    parents.append(gg)
                    for kk, vvs in merged.items():
                        dt, conf = sniff_datatype(_scalars(vvs))
                        gg["leaves"].append(
                            leaf(
                                kk,
                                datatype=dt,
                                dt_conf=conf,
                                group=k,
                                provenance=f"json:{k}.{kk}",
                            )
                        )
                else:
                    mv = isinstance(v, list)
                    dt, conf = sniff_datatype(
                        _scalars(v if isinstance(v, list) else [v])
                    )
                    g["leaves"].append(
                        leaf(
                            k,
                            datatype=dt,
                            dt_conf=conf,
                            multivalued=mv,
                            group=name,
                            provenance=f"json:{name}.{k}",
                        )
                    )

        rootname = os.path.splitext(os.path.basename(filename))[0] or "root"
        add_group(rootname, data, 0)
        return build_draft(
            filename=filename,
            fmt="json",
            raw_bytes=raw_bytes,
            parents=parents,
            groups=groups,
            n_records=len(parents),
            notes=[
                "JSON object read as nested groups; each object is a group "
                "(folder), each scalar "
                "key a variable term with its value TYPE only (not the value)."
            ],
        )

    # A scalar or other value gives nothing meaningful.
    return build_draft(
        filename=filename,
        fmt="json",
        raw_bytes=raw_bytes,
        parents=[],
        notes=["Top-level JSON is a scalar — nothing to model."],
    )


def read_parquet(raw_bytes, filename, sample_rows=500):
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as e:  # noqa: BLE001
        return build_draft(
            filename=filename,
            fmt="parquet",
            raw_bytes=raw_bytes,
            parents=[],
            notes=[
                f"Parquet needs pyarrow on the server (pip install pyarrow): "
                f"{e}"
            ],
        )
    try:
        table = pq.read_table(io.BytesIO(raw_bytes))
    except Exception as e:  # noqa: BLE001
        return build_draft(
            filename=filename,
            fmt="parquet",
            raw_bytes=raw_bytes,
            parents=[],
            notes=[f"Could not read Parquet: {e}"],
        )
    parent = parent_term(
        "Tabular dataset",
        subclassOf=_IBE,
        definition=(
            "Tabular dataset (rows and columns); "
            "Parquet schema is authoritative."
        ),
    )
    parents = [parent]
    group_terms = OrderedDict()
    _AR = {
        "string": "xsd:string",
        "large_string": "xsd:string",
        "int": "xsd:integer",
        "int32": "xsd:integer",
        "int64": "xsd:integer",
        "double": "xsd:decimal",
        "float": "xsd:decimal",
        "bool": "xsd:boolean",
        "date32[day]": "xsd:date",
    }

    def maparrow(t):
        s = str(t)
        if s.startswith("timestamp"):
            return "xsd:dateTime"
        if s.startswith("dictionary"):
            return "xsd:string"
        return _AR.get(s, "xsd:string")

    def _edge(frm, to):
        frm["edges"].append(
            OrderedDict(
                to=to,
                predicate=None,
                confidence=0.0,
                provenance=f"parquet:group {to}",
            )
        )

    def _emit_field(fname, ftype, target, prov_prefix):
        """Convert a struct to a nested group (a folder) with one typed
        leaf for each child field.

        A list gives the same result, but multivalued. A scalar gives
        one typed leaf. The function recurses for nested structs. The
        real structure of the Parquet schema is then modeled. It is not
        flattened to a string.
        """
        multiv = False
        t = ftype
        while pa.types.is_list(t) or pa.types.is_large_list(t):
            multiv = True
            t = t.value_type
        if pa.types.is_struct(t):
            gt = parent_term(
                fname,
                # A container that bears fields is an Information
                # Bearing Entity.
                subclassOf=_IBE,
                definition=("Repeated nested" if multiv else "Nested")
                + f" structure '{fname}' from the Parquet schema.",
            )
            group_terms[fname] = gt
            parents.append(gt)
            _edge(target, fname)
            for i in range(t.num_fields):
                cf = t.field(i)
                _emit_field(cf.name, cf.type, gt, prov_prefix + fname + ".")
            return
        dt = maparrow(t)
        target["leaves"].append(
            leaf(
                fname,
                datatype=dt,
                dt_conf=1.0,
                multivalued=multiv,
                subclassOf=(
                    cco_for(dt, multivalued=multiv) or _ICE
                ),  # A value field is content: ICE.
                provenance=prov_prefix + fname + f" ({ftype})",
            )
        )

    sample = table.slice(0, sample_rows)
    for field in table.schema:
        name = field.name
        # Nested (struct) or repeated (list) columns carry a real
        # structure. Model it.
        if (
            pa.types.is_struct(field.type)
            or pa.types.is_list(field.type)
            or pa.types.is_large_list(field.type)
        ):
            _emit_field(name, field.type, parent, "parquet:")
            continue
        col = sample.column(field.name).to_pylist()
        il = index_leaf(name, col, provenance=f"parquet:{name!r}")
        if il is not None:
            parent["leaves"].append(il)
            continue
        target = parent
        lbl = name
        if "." in name:
            grp, lbl = name.split(".", 1)
            if grp not in group_terms:
                gt = parent_term(
                    grp,
                    subclassOf=_ICE,
                    definition=f"Grouping '{grp}' within the tabular data.",
                )
                group_terms[grp] = gt
                parents.append(gt)
                _edge(parent, grp)
            target = group_terms[grp]
        enum = detect_enum(col)
        dt = maparrow(field.type)
        sc = None if enum else (cco_for(dt, multivalued=False) or _ICE)
        target["leaves"].append(
            leaf(
                lbl,
                datatype=("xsd:string" if enum else dt),
                dt_conf=1.0,
                enum=enum,
                sensitive=is_sensitive(col),
                samples=[c for c in col if not _is_missing(c)][:3],
                subclassOf=sc,
                provenance=f"parquet:{name} ({field.type})",
            )
        )
    return build_draft(
        filename=filename,
        fmt="parquet",
        raw_bytes=raw_bytes,
        parents=parents,
        groups=[],
        n_records=table.num_rows,
        notes=[
            "Datatypes taken from the Parquet schema (authoritative).",
            "Tabular data preassigned subclass of Measurement Information "
            "Content Entity; singular "
            "numeric/date fields preassigned a CCO class by datatype.",
        ],
    )


# Dispatch.


def sniff_format(filename, raw_bytes):
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in (".parquet", ".pq"):
        return "parquet"
    if ext in (".xml", ".rdf"):
        return "xml"
    if ext in (".json",):
        return "json"
    if ext in (".yaml", ".yml"):
        return "yaml"
    if ext in (".md", ".markdown"):
        return "hf-card"
    head = raw_bytes[:64].lstrip()
    if raw_bytes[:4] == b"PAR1":
        return "parquet"
    if head.startswith(b"<"):
        return "xml"
    if head[:1] in (b"{", b"["):
        return "json"
    if head.startswith(b"---"):
        return "hf-card"
    return "csv"


_READERS = {
    "csv": read_csv,
    "hf-card": read_hf_card,
    "xml": read_xml,
    "parquet": read_parquet,
    "json": read_json,
}

_VALUEISH = re.compile(
    r"^(?:\d+(?:[.,]\d+)?"  # Examples: 42 and 3.14.
    r"|\d{4}-\d\d-\d\d(?:[T ].*)?"  # Dates and timestamps.
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F-]{4,}"  # A uuid-like value.
    r"|[0-9a-fA-F]{12,}"  # A long hex or hash value.
    r"|[A-Za-z]{1,4}\d{2,})$"
)  # Examples: m12, row42 and id1000.


def _looks_like_value(name: str) -> bool:
    return bool(_VALUEISH.match((name or "").strip()))


# This number of siblings with the same shape and with value-like names
# is a collection. It is not a set of fields.
_COLLAPSE_MIN = 4
# Hard limit. Above this number, collapse ALL repeats with the same
# shape.
_MAX_TERMS = 800


def _collapse_draft(draft: dict) -> dict:
    """Distinguish schema FIELDS from data VALUES.

    Many sibling groups can have the same field signature and value-like
    names (12345, 2024-01-02, m12 and others). They are instances of one
    type. They are not thousands of different terms. The function
    collapses them to one representative group, so that the number of
    terms does not explode. A note records the collapse.
    """
    parents = draft.get("parents") or []
    if len(parents) < _COLLAPSE_MIN:
        return draft
    buckets = OrderedDict()
    for p in parents:
        sig = tuple(sorted(leaf["label"] for leaf in p.get("leaves", [])))
        buckets.setdefault(sig, []).append(p)
    forced = (
        len(parents) > _MAX_TERMS
    )  # Over the limit: collapse every repeated shape.
    keep, remap, collapsed = [], {}, 0
    for sig, ps in buckets.items():
        valueish = sum(1 for p in ps if _looks_like_value(p["label"]))
        value_keyed = valueish >= (len(ps) + 1) // 2
        is_collection = len(ps) >= _COLLAPSE_MIN and (value_keyed or forced)
        if sig and is_collection:
            rep = ps[0]
            names = [p["label"] for p in ps]
            if value_keyed:
                rep["label"] = "item"  # Value-like keys give one generic type.
            rep.setdefault("notes", [])
            rep["notes"].append(
                f"Collapsed {len(ps)} same-shaped instances whose keys look "
                f"like values "
                f"(e.g. "
                f"{', '.join(names[:5])}{'…' if len(names) > 5 else ''}); the "
                f"key is an "
                f"identifier value, not a field."
            )
            for p in ps[1:]:
                remap[p["label"]] = rep["label"]
            collapsed += len(ps) - 1
            keep.append(rep)
        else:
            keep.extend(ps)
    if not collapsed:
        return draft
    # Remap the edges and order links that pointed at collapsed
    # instances.
    for p in keep:
        for e in p.get("edges", []):
            if e.get("to") in remap:
                e["to"] = remap[e["to"]]
        if p.get("seq_next") in remap:
            p["seq_next"] = remap[p["seq_next"]]
    kept_labels = {p["label"] for p in keep}
    draft["parents"] = keep
    draft["groups"] = [
        g for g in (draft.get("groups") or []) if g["name"] in kept_labels
    ]
    draft.setdefault("notes", [])
    draft["notes"].append(
        f"Collapsed {collapsed} value-keyed instance group(s) into "
        f"representative types "
        f"to keep the term count manageable."
    )
    return draft


def analyze(filename, raw_bytes):
    fmt = sniff_format(filename, raw_bytes)
    reader = _READERS.get(fmt, read_csv)
    draft = reader(raw_bytes, filename)
    if fmt in ("xml", "json"):
        draft = _collapse_draft(draft)
    return draft


# De-duplication (scope: one workspace).


def check_already_analyzed(workspace, sha):
    """The workspace is {'analyzed': {sha: {...}}, 'terms': {label:
    term}}.
    """
    return workspace.get("analyzed", {}).get(sha)


def merge_into_workspace(workspace, draft, policy="merge-additive"):
    """Add a draft to a workspace. Return a report of what happened.

    The argument policy has two values:
    - "merge-additive": add to existing terms. Never overwrite. Flag
      conflicts.
    - "skip-existing": leave existing terms unchanged.
    """
    ws_terms = workspace.setdefault("terms", {})
    ws_analyzed = workspace.setdefault("analyzed", {})
    report = {
        "added_terms": [],
        "skipped_terms": [],
        "added_leaves": [],
        "conflicts": [],
        "file_seen_before": False,
    }

    sha = draft["source"]["sha256"]
    if sha in ws_analyzed:
        report["file_seen_before"] = ws_analyzed[sha]

    for p in draft["parents"]:
        label = p["label"]
        if label not in ws_terms:
            ws_terms[label] = {
                "label": label,
                "definition": p["definition"],
                "altLabels": list(p["altLabels"]),
                "leaves": [dict(leaf) for leaf in p["leaves"]],
                "edges": [dict(e) for e in p["edges"]],
            }
            report["added_terms"].append(label)
            continue
        # The term exists in THIS workspace.
        if policy == "skip-existing":
            report["skipped_terms"].append(label)
            continue
        existing = ws_terms[label]
        have = {leaf["label"]: leaf for leaf in existing["leaves"]}
        for leaf in p["leaves"]:
            if leaf["label"] not in have:
                existing["leaves"].append(dict(leaf))
                report["added_leaves"].append(f"{label}.{leaf['label']}")
            else:
                old = have[leaf["label"]]
                if (
                    old.get("datatype")
                    and leaf.get("datatype")
                    and old["datatype"] != leaf["datatype"]
                ):
                    report["conflicts"].append(
                        f"{label}.{leaf['label']}: "
                        f"{old['datatype']} vs {leaf['datatype']}"
                    )

    ws_analyzed[sha] = {
        "filename": draft["source"]["filename"],
        "analyzed_at": draft["source"]["analyzed_at"],
        "n_terms": len(draft["parents"]),
    }
    return report
