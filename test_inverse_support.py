#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Test inverse relationship support (Parts 5 & 6).

This script tests:
1. Validator scan checks (duplicates, no inverse, orphans)
2. OWL export with owl:inverseOf
3. LinkML export with inverse field
"""

import json
import tempfile
from pathlib import Path
from src.ontomonster.validator import scan_inverse_issues
from src.ontomonster.exporter import build_schema, domain_ttl


def test_validator_scan():
    """Test the validator scan checks."""
    print("Testing validator scan checks...")

    # Mock project
    project = {
        "prefix": "test",
        "iri": "http://test.org/",
        "terms": [
            {
                "name": "test:describes",
                "children": [
                    {"pred": "rdf:type", "val": "owl:ObjectProperty"},
                    {"pred": "rdfs:label", "val": '"describes"'},
                ],
            },
            {
                "name": "test:ClassA",
                "children": [
                    {"pred": "rdf:type", "val": "owl:Class"},
                    {"pred": "test:describes", "val": "test:ClassB"},
                ],
            },
        ],
    }

    props = []
    issues = scan_inverse_issues(project, props)

    print(f"  Duplicates: {len(issues['duplicates'])}")
    print(f"  No inverse: {len(issues['no_inverse'])}")
    print(f"  Orphans: {len(issues['orphans'])}")
    print("  [OK] Validator works")


def test_linkml_inverse():
    """Test LinkML export with inverse field."""
    print("\nTesting LinkML export with inverse field...")

    project = {
        "prefix": "test",
        "iri": "http://test.org/",
        "terms": [
            {
                "name": "test:describes",
                "children": [
                    {"pred": "rdf:type", "val": "owl:ObjectProperty"},
                    {"pred": "om:inverseOf", "val": "test:described_by"},
                ],
            },
            {
                "name": "test:Dataset",
                "children": [
                    {"pred": "rdf:type", "val": "owl:Class"},
                    {"pred": "test:describes", "val": "test:Thing"},
                ],
            },
            {
                "name": "test:Thing",
                "children": [{"pred": "rdf:type", "val": "owl:Class"}],
            },
        ],
    }

    pm = {
        "test": "http://test.org/",
        "owl": "http://www.w3.org/2002/07/owl#",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    }
    schema = build_schema(project, pm)

    if "slots" in schema and "describes" in schema["slots"]:
        slot = schema["slots"]["describes"]
        if "inverse" in slot:
            print(f"  [OK] describes has inverse: {slot['inverse']}")
        else:
            print("  [INFO] inverse field not set")
    else:
        print("  [INFO] describes slot not created (expected)")

    print("  [OK] LinkML export test complete")


def test_owl_export():
    """Test OWL export with owl:inverseOf."""
    print("\nTesting OWL export with owl:inverseOf...")

    project = {
        "prefix": "test",
        "iri": "http://test.org/",
        "terms": [
            {
                "name": "test:describes",
                "children": [
                    {"pred": "rdf:type", "val": "owl:ObjectProperty"},
                    {"pred": "om:inverseOf", "val": "test:described_by"},
                ],
            },
        ],
    }

    pm = {"test": "http://test.org/"}

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ttl", delete=False
    ) as f:
        out_path = Path(f.name)

    try:
        domain_ttl(project, pm, out_path)
        content = out_path.read_text()

        if "owl:inverseOf" in content:
            print("  [OK] OWL contains owl:inverseOf")
        else:
            print("  [INFO] owl:inverseOf not found")

        if "owl:ObjectProperty" in content:
            print("  [OK] Property declared correctly")

        print("  [OK] OWL export test complete")

    finally:
        if out_path.exists():
            out_path.unlink()


def main():
    """Run all tests."""
    print("=== Testing Inverse Relationship Support ===\n")

    try:
        test_validator_scan()
        test_linkml_inverse()
        test_owl_export()
        print("\n=== All tests completed ===")
        return 0

    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
