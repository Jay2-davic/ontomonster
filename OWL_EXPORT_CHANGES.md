# OWL Export Implementation Summary

## Branch: fix-owl-export-rules

### Changes Made

#### 1. Relationship Handling (Requirement 1)
**Rule**: Wrap a relationship in `owl:someValuesFrom` ONLY when ALL are true:
- Subject is a class
- Value is a class term  
- Predicate is an object property

**Implementation**:
- Built property type map from loaded references and project property terms
- Check property type (object/datatype/annotation) before creating restriction
- Always-direct predicates: annotation properties, SKOS mapping relations, dcterms:*, dc:*, oboInOwl:*, rdfs:seeAlso, rdfs:subClassOf
- Unknown predicates → direct triple with warning

**Test Results**:
```turtle
# Class-to-class object property → restriction
test:TestClass rdfs:subClassOf [ 
    a owl:Restriction ;
    owl:onProperty obo:RO_0000087 ;
    owl:someValuesFrom test:TestRole 
] .

# SKOS mapping → direct triple
test:TestClass skos:exactMatch external:SomeClass .

# dcterms annotation → direct triple  
test:TestClass dcterms:creator "John Doe" .
```

#### 2. Individual Handling (Requirement 2)
**Rule**: For individuals, write each relationship as direct assertion (x R y). Never rdfs:subClassOf or restriction.

**Implementation**:
- Detect individuals by rdf:type owl:NamedIndividual
- Declare with `rdf:type owl:NamedIndividual`
- All relationships become direct assertions

**Test Results**:
```turtle
test:TestIndividual a test:TestClass, owl:NamedIndividual ;
    rdfs:label "Test Individual" ;
    obo:RO_0000087 test:TestRole .  # Direct assertion
```

#### 3. Import Handling (Requirement 3)
**Rule**: Remove owl:imports of project base IRI. Add owl:imports of real reference ontology IRIs.

**Implementation**:
- Collect reference IRIs from loaded ontologies
- Filter out project base IRI
- Add owl:imports for each reference ontology (DECISION D1 = import mode)

**Test Results**:
```turtle
<http://example.org/test> a owl:Ontology ;
    owl:imports obo:ro.owl .  # Reference imported
# No self-import of test base IRI
```

#### 4. External Terms (Requirement 4)
**Rule**: For D1=import, add owl:imports of real ontology IRIs.

**Implementation**:
- References loaded via `load_ontologies()`
- IRIs collected and added as owl:imports
- No minimal declarations needed (import mode brings full definitions)

#### 5. BFO Anchor Check (Requirement 5)
**Rule**: Add check "Classes without a path to BFO". List failing classes. Don't block export.

**Implementation**:
- `_check_bfo_anchor()` function performs BFS from each workspace class
- Builds parent map from workspace terms + references
- Classes with /obo/BFO_ in parent chain pass
- Returns list of failing classes with name and label
- Added to export result as `bfo_anchor_failing`

**Status**: Backend complete. Frontend scan display pending.

#### 6. Consistency (Requirement 6)
**Rule**: Keep SHACL, LinkML and OWL files consistent (same IRIs in all three).

**Implementation**:
- All three generators use same prefix map and term structure
- LinkML schema generation unchanged (already consistent)
- OWL domain_ttl uses same resolution logic

### Files Changed

1. **src/ontomonster/exporter.py** (342 lines added, 37 removed):
   - Rewrote `domain_ttl()` with new relationship rules
   - Added property type detection from references
   - Added individual vs class detection
   - Added BFO anchor check function
   - Updated callers to pass references and handle warnings

2. **CHANGELOG.md**:
   - Documented OWL export changes under 1.0.0 Unreleased

### Test Output

Tested with sample project containing:
- Class with object property relationship → restriction ✓
- Class with SKOS mapping → direct triple ✓  
- Class with dcterms annotation → direct triple ✓
- Individual with relationships → direct assertions ✓
- Structural predicates (rdfs:subClassOf) → direct ✓
- Reference imports → owl:imports added ✓
- No self-imports ✓

Warnings: None (structural predicates handled, unknown predicates warned as expected)

### Commits

1. `aa4d3f0` - OWL export: fix relationship and individual handling
2. `5385b23` - Handle rdfs:subClassOf as structural predicate

### Decisions Applied

- **D1 = import (option b)**: Add owl:imports of real ontology IRIs

### Next Steps

Frontend integration for BFO anchor check in Scan overlay (requirement 5).
