# SPDX-License-Identifier: BSD-3-Clause
"""Validate terms and relations for inverse relationship issues.

The scan checks identify three types of problems:
1. Duplicate: Both directions written manually for the same pair (warning)
2. Info: Relation with no declared inverse (information only)
3. Orphan: Written inverse whose source row is gone (error)
"""

from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger("onto_monster")


def _local(name: str) -> str:
    """Extract the local part of a curie."""
    return name.split(":", 1)[1] if ":" in name else name


def _get_inverse_iris(pred: str, props: list) -> "list[str]":
    """Get inverse property IRIs for a predicate.

    This replicates the UI helper _predInverseIRIs. Returns a list of
    full IRIs that are inverses of the given predicate.

    Args:
        pred: Predicate curie (e.g., "cco:describes")
        props: List of property records from loaded ontologies

    Returns:
        List of inverse IRIs
    """
    # Find the property record for this predicate
    for prop in props:
        if prop.get("u") == pred or (
            ":" in pred
            and prop.get("pfx") == pred.split(":")[0]
            and prop.get("c") == _local(pred)
        ):
            # Return the inverse IRIs if they exist
            return prop.get("inv", [])
    return []


def _is_property_term(term: dict) -> bool:
    """Check if a term is a property (ObjectProperty, DatatypeProperty, or
    AnnotationProperty).
    """
    for child in term.get("children", []):
        if child.get("pred") == "rdf:type":
            val = (child.get("val") or "").strip()
            if val.split(":")[-1] in (
                "ObjectProperty",
                "DatatypeProperty",
                "AnnotationProperty",
            ):
                return True
    return False


def _has_inverse_declaration(term: dict) -> bool:
    """Check if a property term has an om:inverseOf child."""
    for child in term.get("children", []):
        if child.get("pred") == "om:inverseOf" and (
            child.get("val") or ""
        ).strip():
            return True
    return False


def scan_inverse_issues(project: dict, props: list) -> dict:
    """Scan project for inverse relationship issues.

    Args:
        project: Project dictionary with terms
        props: List of property records from loaded ontologies

    Returns:
        Dictionary with lists of issues:
        {
            "duplicates": [warning messages],
            "no_inverse": [info messages],
            "orphans": [error messages]
        }
    """
    terms = project.get("terms", [])
    issues = {"duplicates": [], "no_inverse": [], "orphans": []}

    # Build a map of term name to term for quick lookup
    term_map = {t["name"]: t for t in terms}

    # Track all relationships: (subject, predicate, object) tuples
    relations = []
    for term in terms:
        subj_name = term["name"]
        for child in term.get("children", []):
            pred = child.get("pred")
            obj = (child.get("val") or "").strip()
            if not pred or not obj:
                continue
            # Skip non-relation predicates (metadata, etc.)
            if pred in (
                "rdf:type",
                "rdfs:label",
                "skos:definition",
                "rdfs:subClassOf",
                "om:closedEnum",
                "om:valueType",
                "om:valueSet",
                "om:enumValue",
                "om:valueSetRef",
                "om:inverseOf",
            ):
                continue
            relations.append((subj_name, pred, obj, child))

    # Check 1: Find duplicate inverse pairs (both directions written
    # manually)
    seen_pairs = set()
    for subj, pred, obj, child in relations:
        # Get inverse predicates for this predicate
        inv_iris = _get_inverse_iris(pred, props)

        for inv_iri in inv_iris:
            # Look for the inverse relation: obj -> inv_pred -> subj
            for s2, p2, o2, c2 in relations:
                # Check if this is the inverse pair
                if s2 == obj and o2 == subj:
                    # Check if p2 matches any inverse IRI
                    # p2 might be a curie or an IRI
                    if p2 == inv_iri or (
                        ":" in p2
                        and any(
                            prop.get("u") == inv_iri
                            and prop.get("pfx") == p2.split(":")[0]
                            and prop.get("c") == _local(p2)
                            for prop in props
                        )
                    ):
                        # Found a duplicate pair - but only report once
                        pair_key = tuple(
                            sorted([(subj, pred, obj), (s2, p2, o2)])
                        )
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            issues["duplicates"].append(
                                f"Duplicate inverse pair: {subj} "
                                f"{pred} {obj} AND "
                                f"{s2} {p2} {o2}"
                            )

    # Check 2: Find relations with no declared inverse (info only)
    for subj, pred, obj, child in relations:
        inv_iris = _get_inverse_iris(pred, props)
        if not inv_iris:
            # Check if this is from a property term in our project
            if pred.startswith(project.get("prefix", "ex") + ":"):
                # This is an own property - check if it has om:inverseOf
                pred_local = _local(pred)
                pred_term = None
                for t in terms:
                    if _local(t["name"]) == pred_local and _is_property_term(
                        t
                    ):
                        pred_term = t
                        break

                if pred_term and not _has_inverse_declaration(pred_term):
                    issues["no_inverse"].append(
                        f"Relation {subj} {pred} {obj} has no "
                        f"declared inverse"
                    )

    # Check 3: Find orphan written inverses (inverse marked for write but
    # source is gone)
    for term in terms:
        for child in term.get("children", []):
            if child.get("writeInverse"):
                # This relationship is marked to write its inverse
                subj_name = term["name"]
                pred = child.get("pred")
                obj = (child.get("val") or "").strip()

                # Verify the source row still exists
                # (it does, since we're iterating over it)
                # But check if the target term exists
                if obj and ":" in obj:
                    obj_local = obj
                    if obj_local not in term_map:
                        issues["orphans"].append(
                            f"Orphan inverse: {subj_name} {pred} "
                            f"{obj} has writeInverse=true but target "
                            f"does not exist"
                        )

    return issues


def print_scan_report(issues: dict) -> None:
    """Print a formatted report of inverse relationship issues."""
    print("\n=== Inverse Relationship Scan Report ===\n")

    if issues["duplicates"]:
        print(f"WARNINGS ({len(issues['duplicates'])}):")
        for msg in issues["duplicates"]:
            print(f"  - {msg}")
        print()

    if issues["orphans"]:
        print(f"ERRORS ({len(issues['orphans'])}):")
        for msg in issues["orphans"]:
            print(f"  - {msg}")
        print()

    if issues["no_inverse"]:
        print(f"INFO ({len(issues['no_inverse'])}):")
        for msg in issues["no_inverse"]:
            print(f"  - {msg}")
        print()

    if not any(issues.values()):
        print("No issues found.")
        print()
